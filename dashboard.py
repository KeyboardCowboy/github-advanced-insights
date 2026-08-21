"""Board-level view: every column at once, rather than one column at a time.

A report answers "how long has work waited in *this* column". That question has
a column in it, so each report only ever knows about its own. "Where is
everything right now, and what is old" has no column in it, and cannot be
answered by adding a sixth report -- which is why this is a separate view with
its own pipeline rather than another definition (#10).

The chart it feeds is the aging work-in-progress scatter: every column across
the x-axis, one dot per ticket, age up the y-axis, with percentile lines drawn
across the whole set. That last part is what makes it readable without someone
having to pick a sensible threshold for every column separately.

A standalone module and CLI, like status_history.py. It imports the pipeline
from ticket_aging rather than the other way round, so nothing here creates an
import cycle with the module that owns the report stages.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import workspace
from projects import account_for, get_project, list_projects, owner_scope
from status_history import fetch_histories
from ticket_aging import fetch_board_items, parse_ts, run_graphql

# Where a board's dashboard settings live. One file per board, next to the
# report definitions, because a dashboard belongs to a board the same way a
# report does.
DASHBOARDS_DIRNAME = "dashboards"

# The starting point for a board that has never been configured. The same
# exclusions the reports use, so the scatter and the histograms describe the
# same population: Epics and Features are containers rather than work someone
# is moving, and their age says nothing about flow.
DEFAULT_FILTER = 'is:open -type:Epic,Feature'

# A filter is free text handed to GitHub, so there is little to check here
# beyond "somebody typed something". What it actually matches is only knowable
# by asking GitHub, which saving does immediately -- so a filter that is valid
# but wrong is reported by the refresh rather than guessed at here.
MAX_FILTER_LENGTH = 500

# Drawn across the whole set rather than per column. A line per column would
# need every column to hold enough tickets to have a distribution, which small
# columns never do.
PERCENTILES = (50, 85, 95)


def dashboards_dir():
    return workspace.WORKSPACE / DASHBOARDS_DIRNAME


def definition_path(project_id):
    return dashboards_dir() / f"{project_id}.json"


def load_definition(project_id):
    """A board's dashboard settings, with defaults for anything absent.

    A board with no file is not an error: it has simply never been configured,
    and the default filter is a reasonable thing to show it.
    """
    path = definition_path(project_id)
    stored = json.loads(path.read_text()) if path.exists() else {}
    return {"filter": (stored.get("filter") or DEFAULT_FILTER).strip()}


def validate_definition(values):
    """Every problem with a candidate dashboard, empty when it is usable."""
    problems = []
    query = str(values.get("filter") or "").strip()
    if not query:
        problems.append("A filter is required. Use is:open to include everything open.")
    elif len(query) > MAX_FILTER_LENGTH:
        problems.append(
            f"That filter is {len(query)} characters; the limit is "
            f"{MAX_FILTER_LENGTH}.")
    elif "\n" in query:
        problems.append("A filter has to be a single line.")
    return problems


def save_definition(project_id, values):
    """Write a board's dashboard settings. Returns what was stored."""
    problems = validate_definition(values)
    if problems:
        raise ValueError("\n".join(problems))

    path = definition_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = {"filter": str(values["filter"]).strip()}
    # Same write-then-rename as the report definitions: a half-written file
    # would break the dashboard for a board rather than just this save.
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(stored, indent=2, ensure_ascii=False) + "\n")
    os.replace(temp_path, path)
    return stored


def cache_paths(project_id):
    return {
        "raw": workspace.CACHE_DIR / f"dashboard-{project_id}-raw.json",
        "model": workspace.CACHE_DIR / f"dashboard-{project_id}-view-model.json",
    }


def resolve_project(project_id=None):
    project = get_project(project_id)
    if project is None:
        known = ", ".join(p["id"] for p in list_projects()) or "none"
        sys.exit(f"No project called '{project_id}'. Known: {known}")
    return project


def board_statuses(project, account):
    """The board's Status options, in the order the board itself lists them.

    That order is the x-axis. Deriving it from the data instead would sort the
    columns alphabetically or by whatever happened to be busiest, and a
    workflow chart whose columns are out of workflow order is worse than no
    chart -- it invites reading a sequence that does not exist.
    """
    scope = owner_scope(project)
    data = run_graphql(f'''
    {{
      {scope}(login: "{project["owner"]}") {{
        projectV2(number: {project["project_number"]}) {{
          field(name: "Status") {{
            ... on ProjectV2SingleSelectField {{ options {{ name }} }}
          }}
        }}
      }}
    }}''', account)
    field = ((data.get(scope) or {}).get("projectV2") or {}).get("field") or {}
    return [option["name"] for option in field.get("options") or []]


def current_status_entry(timeline, project_number):
    """Which column a ticket is in now, and when it got there.

    The last status change on this board is both answers at once: the event
    records the status it moved *to*, so the most recent one names the current
    column and dates the current stint.
    """
    events = [
        event for event in timeline["timelineItems"]["nodes"]
        if event and (event.get("project") or {}).get("number") == project_number
    ]
    if not events:
        return None, None
    # Nodes arrive oldest-first, so the last one is the current position.
    latest = events[-1]
    return latest["status"], parse_ts(latest["createdAt"])


def fetch(project_id=None):
    """Pull every open ticket on the board, with its status timeline."""
    project = resolve_project(project_id)
    account = account_for(project)
    scope = owner_scope(project)

    # fetch_board_items takes the shape a report definition has; the dashboard
    # has no definition, so it passes the same two keys directly.
    stored = load_definition(project["id"])
    definition = {"filter": stored["filter"],
                  "project_number": project["project_number"]}
    board, issues, matched, seen = fetch_board_items(
        definition, project, account, scope)

    if matched is not None and seen < matched:
        print(f"! Filter matched {matched} items but only {seen} were fetched. "
              "This dashboard is incomplete.", file=sys.stderr)

    raw = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "project": project["id"],
        "project_label": project["label"],
        "board": {"number": board["number"], "title": board["title"]},
        "filter": stored["filter"],
        "statuses": board_statuses(project, account),
        "issue_ids": [issue["id"] for issue in issues],
        "timelines": fetch_histories([issue["id"] for issue in issues], account),
    }

    workspace.CACHE_DIR.mkdir(exist_ok=True)
    path = cache_paths(project["id"])["raw"]
    path.write_text(json.dumps(raw, indent=2))
    print(f"fetch     {len(issues)} tickets across {len(raw['statuses'])} columns "
          f"-> {workspace.short_path(path)}")
    return raw


def percentile(sorted_values, pct):
    """Nearest-rank percentile. Empty input has no percentile, not zero.

    Nearest-rank rather than interpolating: every line drawn is then the age of
    a ticket someone can actually point at, which is easier to argue with than
    a number sitting between two of them.
    """
    if not sorted_values:
        return None
    rank = max(1, -(-pct * len(sorted_values) // 100))   # ceil, integer-only
    return sorted_values[min(rank, len(sorted_values)) - 1]


def normalize(project_id=None):
    """Group tickets by the column they are in now and age each one."""
    project = resolve_project(project_id)
    paths = cache_paths(project["id"])
    if not paths["raw"].exists():
        sys.exit(f"No dashboard data yet. Run: dashboard.py fetch {project['id']}")

    raw = json.loads(paths["raw"].read_text())
    # Ages measured from the fetch, not from now, for the same reason the
    # reports are: the set of tickets describes that moment, so their ages have
    # to as well, or the page mixes two different instants (#2).
    snapshot_at = (parse_ts(raw["fetched_at"]) if raw.get("fetched_at")
                   else datetime.now(timezone.utc))
    project_number = raw["board"]["number"]

    by_status, unknown = {}, []
    for issue_id in raw["issue_ids"]:
        timeline = raw["timelines"].get(issue_id)
        if not timeline:
            unknown.append({"number": "?", "title": "(no longer resolves)"})
            continue

        status, entered = current_status_entry(timeline, project_number)
        if status is None or entered is None:
            # Board history predating GitHub's status events. Listed rather
            # than plotted at zero, which would look like brand new work.
            unknown.append({"number": timeline["number"], "title": timeline["title"]})
            continue

        days = (snapshot_at - entered).total_seconds() / 86400
        by_status.setdefault(status, []).append({
            "number": timeline["number"],
            "title": timeline["title"],
            "url": timeline["url"],
            "repo": (timeline.get("repository") or {}).get("nameWithOwner"),
            "days": round(days, 2),
            "days_display": f"{days:.1f}",
            "entered_date": entered.date().isoformat(),
        })

    # Board order for the columns the board declares, then anything left over.
    # A status can disappear from the board while tickets still sit in it, and
    # dropping those tickets would quietly shrink the total.
    declared = list(raw.get("statuses") or [])
    extra = sorted(s for s in by_status if s not in declared)
    columns = []
    for status in declared + extra:
        tickets = sorted(by_status.get(status, []),
                         key=lambda t: t["days"], reverse=True)
        columns.append({
            "status": status,
            "count": len(tickets),
            "off_board": status in extra,
            "tickets": tickets,
        })

    every_age = sorted(t["days"] for column in columns for t in column["tickets"])
    bands = [
        {"label": f"p{pct}", "days": percentile(every_age, pct)}
        for pct in PERCENTILES
    ]

    model = {
        "project": raw["project"],
        "project_label": raw.get("project_label") or raw["project"],
        "board": raw["board"],
        "filter": raw["filter"],
        "updated_iso": raw["fetched_at"],
        "updated_display": snapshot_at.strftime("%d %b %Y, %H:%M UTC"),
        "columns": columns,
        "bands": [band for band in bands if band["days"] is not None],
        "total": len(every_age),
        "oldest": every_age[-1] if every_age else 0,
        "unknown": unknown,
    }

    paths["model"].write_text(json.dumps(model, indent=2))
    print(f"normalize {model['total']} tickets, {len(columns)} columns "
          f"-> {workspace.short_path(paths['model'])}")
    return model


def refresh(project_id=None):
    fetch(project_id)
    return normalize(project_id)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, handler, help_text in [
        ("fetch", fetch, "Pull every open ticket on the board"),
        ("normalize", normalize, "Group by column and age each ticket"),
        ("refresh", refresh, "Both, in order"),
    ]:
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("project", nargs="?", default=None,
                         help="Project id. Defaults to the default project.")
        sub.set_defaults(handler=handler)

    args = parser.parse_args()
    workspace.ensure()
    args.handler(args.project)


if __name__ == "__main__":
    main()
