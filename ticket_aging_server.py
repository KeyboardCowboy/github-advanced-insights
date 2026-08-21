#!/usr/bin/env python3
"""Local web app for browsing the ticket aging reports.

Serves the same template the static build uses, plus a small JSON API the page
uses to list reports, read a cached one, and trigger a fresh fetch. Running
locally is what makes this possible: a published artifact cannot call back to
anything, but a page served from here can.

  GET  /                            the interface (read live from views/)
  GET  /api/reports                 every definition + its cache freshness
  GET  /api/reports/<slug>          one cached view model
  POST /api/definitions            create a report, refusing a taken slug
  POST /api/reports/<slug>/refresh  run the pipeline, return the new model

The page works in either context, and decides which at load: if /api/reports
answers, it runs as an app with a report sidebar and a refresh button; if not
-- as in a published artifact -- it falls back to the view model baked into its
data island. One template, two contexts.

Refreshing shells out to `gh`, so this binds to 127.0.0.1 by default and is not
intended to be exposed to a network.

Usage:
  python3 ticket_aging_server.py
  python3 ticket_aging_server.py --port 8080
"""
import argparse
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import workspace
from accounts import (
    delete_account,
    validate_account,
    describe_source,
    get_account,
    list_accounts,
    load_accounts,
    resolve_token,
    save_account,
)
from github import GitHubError, graphql
from projects import (
    account_for,
    delete_project,
    get_project,
    list_projects,
    load_projects,
    owner_scope,
    save_project,
    validate_project,
)
from markup import render as render_markup
from report_store import (
    delete_report,
    load_raw,
    save_report,
    slug_from_title,
)
from ticket_aging import (
    BASE_DIR,
    REPORTS_DIR,
    VIEW_TEMPLATE,
    cache_paths,
    load_definition,
    ordered_slugs,
    refresh_report,
    REPORT_DEFAULTS,
    reports_using_project,
)

SETTINGS_TEMPLATE = BASE_DIR / "views" / "settings.html"
REPORT_FORM_TEMPLATE = BASE_DIR / "views" / "report-form.html"

# Slugs come from the URL, so they are constrained before touching the filesystem.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# One refresh at a time: concurrent runs would race on the same cache files, and
# a double-clicked button should queue rather than corrupt a report.
REFRESH_LOCK = threading.Lock()


def available_slugs():
    """Report slugs in board workflow order — the order the sidebar renders."""
    return ordered_slugs()


def report_summary(slug):
    """Definition metadata plus whatever the cache currently holds for it."""
    definition = load_definition(slug)
    raw = json.loads((REPORTS_DIR / f"{slug}.json").read_text())
    summary = {
        "slug": slug,
        "title": definition.get("copy", {}).get("title", slug),
        "status": definition["measure_status"],
        "filter": definition["filter"],
        "project": definition["project"],
        "project_label": definition["_project"]["label"],
        "project_number": definition["project_number"],
        "owner": definition["_project"]["owner"],
        # Carried so the sidebar can filter by account without a second call.
        "account": (account_for(definition["_project"]) or {}).get("id"),
        "account_label": (account_for(definition["_project"]) or {}).get("label",
                                                                        "Active gh account"),
        "updated_display": "never",
        "count": None,
    }

    model_path = cache_paths(slug)["model"]
    if model_path.exists():
        model = json.loads(model_path.read_text())
        summary["updated_display"] = model["header"]["updated_display"]
        summary["updated_iso"] = model["header"]["updated_iso"]
        summary["count"] = len(model["rows"])
    return summary


class Handler(BaseHTTPRequestHandler):
    # Quieter logs: one line per request, without the default's noise.
    def log_message(self, fmt, *args):
        sys.stderr.write(f"  {self.command} {self.path} -> {args[1]}\n")

    # -- response helpers --------------------------------------------------

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The cached model changes under the page; never let a proxy or the
        # browser serve a stale one after a refresh.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, markup):
        body = markup.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        """Parse a JSON request body, answering the client on malformed input."""
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_json({"error": "bad_json", "message": "Malformed request body."}, 400)
            return None

    def parse_slug(self, raw):
        """Validate a slug from the URL, answering the client on failure."""
        if not SLUG_PATTERN.match(raw) or raw not in available_slugs():
            self.send_json({"error": f"Unknown report '{raw}'"}, status=404)
            return None
        return raw

    # -- routes ------------------------------------------------------------

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            # Read the template per request so edits show on a plain reload.
            # The data island stays empty here; the page fetches its own model.
            return self.send_html(VIEW_TEMPLATE.read_text())

        if self.path in ("/settings", "/settings/"):
            return self.send_html(SETTINGS_TEMPLATE.read_text())

        if self.path.split("?")[0] in ("/report", "/report/"):
            return self.send_html(REPORT_FORM_TEMPLATE.read_text())

        if self.path.startswith("/api/boards/search"):
            return self.search_boards(parse_qs(urlparse(self.path).query))

        match = re.fullmatch(r"/api/boards/([^/]+)/statuses", self.path)
        if match:
            board = get_project(match.group(1))
            if board is None:
                return self.send_json({"error": "not_found"}, status=404)
            return self.board_statuses(board)

        match = re.fullmatch(r"/api/definitions/([^/]+)", self.path)
        if match:
            stored = load_raw(match.group(1))
            if stored is None:
                return self.send_json({"error": "not_found"}, status=404)
            return self.send_json({
                "slug": match.group(1), "stored": stored, "defaults": REPORT_DEFAULTS,
            })

        if self.path == "/api/accounts":
            document = load_accounts()
            from projects import list_projects as _list_projects
            return self.send_json({
                "accounts": [
                    {**a,
                     "source": describe_source(a),
                     "projects": [p["id"] for p in _list_projects()
                                  if (p.get("account") or document.get("default_account")) == a["id"]]}
                    for a in document["accounts"]
                ],
                "default_account": document.get("default_account"),
            })

        if self.path == "/api/projects":
            document = load_projects()
            return self.send_json({
                "projects": [
                    {**p, "reports": reports_using_project(p["id"])}
                    for p in document["projects"]
                ],
                "default_project": document.get("default_project"),
            })

        if self.path == "/api/reports":
            return self.send_json({"reports": [report_summary(s) for s in available_slugs()]})

        match = re.fullmatch(r"/api/reports/([^/]+)", self.path)
        if match:
            slug = self.parse_slug(match.group(1))
            if slug is None:
                return
            model_path = cache_paths(slug)["model"]
            if not model_path.exists():
                return self.send_json(
                    {"error": "not_cached", "slug": slug,
                     "message": "This report has never been fetched. Use Refresh."},
                    status=404,
                )
            return self.send_json(json.loads(model_path.read_text()))

        self.send_json({"error": "not_found"}, status=404)

    def do_PUT(self):
        match = re.fullmatch(r"/api/accounts/([^/]+)", self.path)
        if match:
            values = self.read_json_body()
            if values is None:
                return
            values["id"] = match.group(1)
            try:
                return self.send_json(save_account(values))
            except ValueError as exc:
                return self.send_json(
                    {"error": "invalid", "problems": str(exc).splitlines()}, status=400
                )

        match = re.fullmatch(r"/api/definitions/([^/]+)", self.path)
        if match:
            values = self.read_json_body()
            if values is None:
                return
            # PUT updates a report that exists. Creating one is POST
            # /api/definitions, because this URL cannot express the difference
            # and guessing it is what let a new report overwrite an old one.
            try:
                stored = save_report(match.group(1), values, is_new=False)
            except ValueError as exc:
                return self.send_json(
                    {"error": "invalid", "problems": str(exc).splitlines()}, status=400
                )
            return self.send_json({"slug": match.group(1), "stored": stored})

        match = re.fullmatch(r"/api/projects/([^/]+)", self.path)
        if not match:
            return self.send_json({"error": "not_found"}, status=404)

        values = self.read_json_body()
        if values is None:
            return
        values["id"] = match.group(1)

        # A project that already has reports cannot be silently repointed at a
        # board whose statuses those reports do not use. Only checked when the
        # board actually changes, so editing a label costs no API call.
        existing = get_project(values["id"])
        board_changed = existing and (
            existing["project_number"] != values.get("project_number")
            or existing["owner"] != values.get("owner")
        )
        if board_changed and not values.get("force"):
            payload, error = self.inspect_board(values)
            if error:
                return self.send_json(error[0], status=error[1])
            if payload["incompatible"]:
                return self.send_json({
                    "error": "would_break_reports",
                    "message": f"{len(payload['incompatible'])} report(s) would stop "
                               f"returning data on \"{payload['title']}\".",
                    "incompatible": payload["incompatible"],
                }, status=409)

        values.pop("force", None)
        try:
            return self.send_json(save_project(values))
        except ValueError as exc:
            # Every problem at once, so the form does not surface them one per attempt.
            return self.send_json(
                {"error": "invalid", "problems": str(exc).splitlines()}, status=400
            )

    def do_DELETE(self):
        match = re.fullmatch(r"/api/accounts/([^/]+)", self.path)
        if match:
            try:
                delete_account(match.group(1))
            except ValueError as exc:
                return self.send_json({"error": "in_use", "message": str(exc)}, 409)
            return self.send_json({"deleted": match.group(1)})

        match = re.fullmatch(r"/api/definitions/([^/]+)", self.path)
        if match:
            try:
                delete_report(match.group(1))
            except ValueError as exc:
                return self.send_json({"error": "invalid", "message": str(exc)}, 404)
            return self.send_json({"deleted": match.group(1)})

        match = re.fullmatch(r"/api/projects/([^/]+)", self.path)
        if not match:
            return self.send_json({"error": "not_found"}, status=404)
        try:
            delete_project(match.group(1))
        except ValueError as exc:
            # In use by reports; refusing is safer than orphaning them.
            return self.send_json({"error": "in_use", "message": str(exc)}, status=409)
        return self.send_json({"deleted": match.group(1)})

    def do_POST(self):
        if self.path == "/api/definitions":
            return self.create_report()

        if self.path == "/api/accounts/test":
            return self.test_account()

        if self.path == "/api/projects/test":
            return self.test_project()

        if self.path == "/api/definitions/preview":
            return self.preview_filter()

        if self.path == "/api/definitions/render":
            values = self.read_json_body()
            if values is None:
                return
            return self.send_json({"html": render_markup(values.get("markdown", ""))})

        if self.path == "/api/definitions/slug":
            values = self.read_json_body()
            if values is None:
                return
            return self.send_json({"slug": slug_from_title(values.get("title", ""))})

        match = re.fullmatch(r"/api/reports/([^/]+)/refresh", self.path)
        if not match:
            return self.send_json({"error": "not_found"}, status=404)

        slug = self.parse_slug(match.group(1))
        if slug is None:
            return

        with REFRESH_LOCK:
            try:
                refresh_report(slug)
            except SystemExit as exc:
                # The pipeline reports failures by exiting; in a server that has
                # to become a response rather than take the process down.
                return self.send_json({"error": "refresh_failed", "message": str(exc)}, status=502)
            except Exception as exc:  # noqa: BLE001 - surface anything to the page
                return self.send_json({"error": "refresh_failed", "message": repr(exc)}, status=500)

        self.send_json(json.loads(cache_paths(slug)["model"].read_text()))


    def search_boards(self, params):
        """Projects belonging to an owner, filtered by a search term.

        A picker rather than a plain dropdown because an owner can have
        hundreds of projects: nyulh has over 200, most of them untitled
        personal boards. The term is passed to GitHub's own filter.
        """
        owner = (params.get("owner") or [""])[0].strip()
        owner_type = (params.get("type") or ["organization"])[0]
        term = (params.get("q") or [""])[0].strip()
        if not owner:
            return self.send_json({"error": "invalid", "message": "Owner is required."}, 400)

        scope = "user" if owner_type == "user" else "organization"
        # GitHub's own project search; `is:open` keeps closed boards out.
        search = ("is:open " + term).strip().replace('"', '\\"')
        data = self.query(f"""
        {{
          {scope}(login: "{owner}") {{
            projectsV2(first: 20, query: "{search}") {{
              totalCount
              nodes {{ number title closed }}
            }}
          }}
        }}""", get_account((params.get("account") or [None])[0]))
        if data is None:
            return

        found = ((data.get(scope) or {}).get("projectsV2")) or {}
        self.send_json({
            "total": found.get("totalCount", 0),
            "projects": [
                {"number": n["number"], "title": n["title"] or f"Untitled #{n['number']}"}
                for n in found.get("nodes", [])
            ],
        })

    def board_statuses(self, board):
        """The board's Status options, in workflow order, for the form's dropdown."""
        scope = owner_scope(board)
        data = self.query(f"""
        {{
          {scope}(login: "{board['owner']}") {{
            projectV2(number: {board['project_number']}) {{
              field(name: "Status") {{
                ... on ProjectV2SingleSelectField {{ options {{ name }} }}
              }}
            }}
          }}
        }}""", account_for(board))
        if data is None:
            return
        found = (data.get(scope) or {}).get("projectV2") or {}
        options = [o["name"] for o in ((found.get("field") or {}).get("options") or [])]
        self.send_json({"statuses": options})

    def create_report(self):
        """Create a new report, refusing a slug that is already taken.

        The slug is derived here rather than accepted from the client. The form
        shows the same derivation so the author knows the filename before
        saving, but the copy that decides whether a file gets overwritten should
        not be the one a stale page happens to be holding.
        """
        values = self.read_json_body()
        if values is None:
            return

        title = (values.get("copy") or {}).get("title", "")
        slug = slug_from_title(title)
        try:
            stored = save_report(slug, values, is_new=True)
        except ValueError as exc:
            return self.send_json(
                {"error": "invalid", "problems": str(exc).splitlines()}, status=400)
        return self.send_json({"slug": slug, "stored": stored}, status=201)

    def preview_filter(self):
        """Run a candidate filter and report what it matches, without saving.

        The form's counterpart to the project test: a filter typo otherwise
        saves cleanly and only shows up as an empty report after a refresh.
        """
        values = self.read_json_body()
        if values is None:
            return

        project = get_project(values.get("project"))
        if project is None:
            return self.send_json({"error": "no_project"}, status=400)

        status = str(values.get("measure_status", "")).strip()
        if not status:
            return self.send_json(
                {"error": "invalid", "message": "Choose a status first."}, status=400)

        # Composed exactly as load_definition composes it, so the preview cannot
        # report on a different set than the saved report would.
        composed = " ".join(part for part in (
            f'status:"{status}"', str(values.get("additional_filter") or "").strip()
        ) if part)
        escaped = composed.replace('"', '\\"')
        scope = owner_scope(project)

        data = self.query(f"""
        {{
          {scope}(login: "{project['owner']}") {{
            projectV2(number: {project['project_number']}) {{
              items(first: 5, query: "{escaped}") {{
                totalCount
                nodes {{ content {{ ... on Issue {{ number title }} }} }}
              }}
            }}
          }}
        }}""", account_for(project), error_key="bad_filter")
        if data is None:
            return

        items = ((data.get(scope) or {}).get("projectV2") or {}).get("items") or {}
        samples = [n["content"] for n in items.get("nodes", []) if n.get("content")]
        self.send_json({
            "filter": composed,
            "total": items.get("totalCount", 0),
            "samples": samples,
        })

    def test_account(self):
        """Resolve a candidate account's token and report which login it is.

        Takes the values from the request body rather than looking up a saved
        account, so an account can be checked *before* it is committed -- which
        is when a typo is cheapest to fix, and the only time the button was
        previously useless.

        Naming the resolved login matters either way: an account with no source
        falls back to the active `gh` login, which would otherwise be
        indistinguishable from success.
        """
        account = self.read_json_body()
        if account is None:
            return

        problems = validate_account(account, is_new=False)
        if problems:
            return self.send_json({"error": "invalid", "message": " ".join(problems)}, 400)

        try:
            resolve_token(account)
        except RuntimeError as exc:
            return self.send_json({"error": "no_token", "message": str(exc)}, status=400)

        data = self.query("{ viewer { login } rateLimit { remaining } }", account)
        if data is None:
            return
        self.send_json({
            "login": data["viewer"]["login"],
            "rate_remaining": data["rateLimit"]["remaining"],
            "source": describe_source(account),
        })

    def query(self, gql, account=None, error_key="unreachable", status=502):
        """Run a query, answering the client on failure. Returns None if it did."""
        try:
            return graphql(gql, account)
        except GitHubError as exc:
            self.send_json({"error": error_key, "message": str(exc)}, status=status)
            return None

    def inspect_board(self, values):
        """Query a board and report which of its reports it would break.

        Returns (payload, error_response) where exactly one is None. Shared by
        Test and Save: a check that only Test performs is a check that a hurried
        save walks straight past.
        """
        scope = owner_scope(values)
        query = f"""
        {{
          {scope}(login: "{values['owner']}") {{
            projectV2(number: {int(values['project_number'])}) {{
              title
              items(first: 1) {{ totalCount }}
              repositories(first: 20) {{ nodes {{ nameWithOwner }} }}
              field(name: "Status") {{
                ... on ProjectV2SingleSelectField {{ options {{ name }} }}
              }}
            }}
          }}
        }}"""

        try:
            data = graphql(query, get_account(values.get("account")))
        except GitHubError as exc:
            return None, ({"error": "unreachable", "message": str(exc)}, 502)

        project = (data.get(scope) or {}).get("projectV2")
        if not project:
            return None, ({
                "error": "not_found",
                "message": f"No project {values['project_number']} found for "
                           f"{scope} '{values['owner']}'.",
            }, 404)

        options = [o["name"] for o in ((project.get("field") or {}).get("options") or [])]
        incompatible = []
        for slug in reports_using_project(values["id"]):
            wanted = load_definition(slug)["measure_status"]
            if wanted not in options:
                incompatible.append({"slug": slug, "status": wanted})

        return {
            "title": project["title"],
            "item_count": project["items"]["totalCount"],
            # Shown as read-only context: a board can span repositories, and the
            # tool no longer stores one, but a reader still wants to know.
            "repositories": [r["nameWithOwner"]
                             for r in (project.get("repositories") or {}).get("nodes", [])],
            "status_count": len(options),
            "statuses": options,
            "incompatible": incompatible,
        }, None

    def test_project(self):
        """Query a candidate board without saving.

        Catches a typo in the form, and -- more importantly -- reports which
        existing reports would stop returning data if this project were
        pointed at that board, since a report's `measure_status` is written in
        one board's vocabulary.
        """
        values = self.read_json_body()
        if values is None:
            return

        existing = [c["id"] for c in list_projects()]
        problems = validate_project(values, existing, is_new=False)
        if problems:
            return self.send_json({"error": "invalid", "message": " ".join(problems)}, 400)

        payload, error = self.inspect_board(values)
        if error:
            return self.send_json(error[0], status=error[1])
        self.send_json(payload)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("PORT") or 8080),
        help="Port to listen on. Defaults to $PORT, then 8080.",
    )
    parser.add_argument(
        "--host", default=os.environ.get("GH_INSIGHTS_HOST") or "127.0.0.1",
        help="Address to bind. Defaults to 127.0.0.1, which is the only "
             "address that keeps this off the network. Override only for a "
             "throwaway environment holding no real credentials.",
    )
    args = parser.parse_args()

    # A fresh clone has no workspace; create it rather than failing, so the
    # first run lands on the settings page instead of a stack trace.
    workspace.ensure()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    slugs = available_slugs()
    boards = list_projects()

    print(f"\nWork Item Age by Status — http://localhost:{args.port}")
    print(f"  workspace: {workspace.describe()}")
    if args.host != "127.0.0.1":
        # Anyone who can reach this port can drive the settings pages, and
        # refreshing a report spends the credentials of whichever account the
        # board is configured with. Worth saying out loud every time.
        print(f"  ! Bound to {args.host}, not just this machine. Anyone who can "
              f"reach it can use the accounts configured here.")
    if boards:
        for board in boards:
            source = describe_source(get_account(board.get("account")))
            print(f"  {board['id']}: {board['owner']} board "
                  f"{board['project_number']} via {source}")
    else:
        # Not fatal: the settings page exists precisely to fix this.
        print(f"  No projects yet. Set one up at "
              f"http://localhost:{args.port}/settings")
    print(f"  {len(slugs)} report(s): {', '.join(slugs) or 'none'}")
    print("  Ctrl-C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
