#!/usr/bin/env python3
"""Aging reports for any set of GitHub Projects v2 tickets.

Answers "how long have these tickets been sitting where they are?" for any
filter you can express in the Projects board filter bar -- waiting for QA,
waiting to be started, blocked, whatever the question is.

A report is a DEFINITION FILE, not code. Adding one means adding a JSON file to
definitions/; the pipeline and the interface never change. Each definition names
the filter that selects the tickets, the status whose entry timestamp they are
aged from, and the narrative copy that frames the numbers:

  {
    "slug": "qa-aging",
    "project": 172,
    "filter": "status:\\"Ready for QA\\" is:open",
    "measure_status": "Ready for QA",
    "bin_days": 7,
    "threshold_days": 30,
    "copy": { "title": ..., "headline": ..., "lede": ..., "notes": [...] }
  }

The stages stay separate so a refresh is a data operation, never a rebuild:

  fetch      GitHub -> cache/<slug>-raw.json
             Raw API payloads, stored as returned. No interpretation.

  normalize  raw.json -> cache/<slug>-view-model.json
             All derived values -- stats, bins, rows, batch flags -- plus the
             definition's copy and any data-dependent notes. The view does no
             arithmetic and composes no sentences.

  build      views/ticket-aging.html + view model -> cache/<slug>.html
             Injects the view model and page title. The template is read-only.

  refresh    All three in order. The normal command.
  report     Terminal view of a stored view model. No API calls.
  list       Show the available report definitions.

One template serves every report, so the interface is built once and reused; a
new question costs a definition file and a refresh.

Usage:
  python3 ticket_aging.py list
  python3 ticket_aging.py refresh qa-aging
  python3 ticket_aging.py report todo-aging
  python3 ticket_aging.py normalize qa-aging   # re-derive, no fetch
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# The timeline fetcher is shared with status_history.py rather than restated.
from github import GitHubError, graphql
from projects import account_for, get_project, list_projects, owner_scope
from markup import render as render_markup
from status_history import fetch_histories, parse_ts

# Everything the tool needs sits beside this file, so the whole thing can be
# moved or copied as a unit without rewiring paths.
BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR
CACHE_DIR = BASE_DIR / "cache"
REPORTS_DIR = BASE_DIR / "definitions"
VIEW_TEMPLATE = BASE_DIR / "views" / "ticket-aging.html"

# Definition defaults, applied when a report file omits them.
REPORT_DEFAULTS = DEFAULTS = {
    # Which named project in projects.json this report reads. A report is only
    # meaningful against the board it was written for, so the tie is explicit.
    # Unset falls back to the default project.
    "project": None,
    "bin_days": 7,          # histogram band width
    "threshold_days": 30,   # reference line: past this, a wait reads as stalled
    "batch_min": 4,         # same-day entries at or above this are a board sweep
    # How many banded bars the chart draws. Beyond them, tickets collapse into
    # one trailing bucket. A bar count rather than a day count because bar count
    # is what governs legibility, and it cannot describe a range the bands fail
    # to tile. Unset means bands run to the oldest ticket, with no bucket.
    "max_bars": None,
    # Extra board-filter clauses. The status clause is composed from
    # `measure_status`, so the two can never disagree.
    "additional_filter": "",
    # Sidebar position. Reports are listed in board workflow order rather than
    # alphabetically, so the sidebar reads like the columns it reports on.
    # Unset reports sort to the end.
    "order": 999,
}

# The two spans the build stage rewrites. Matching on markers rather than
# position lets the template move them freely.
DATA_ISLAND = re.compile(
    r'(<script type="application/json" id="view-model">)(.*?)(</script>)', re.DOTALL
)
TITLE_TAG = re.compile(r"(<title>)(.*?)(</title>)", re.DOTALL)


# --------------------------------------------------------------------------
# Report definitions
# --------------------------------------------------------------------------

def load_definition(slug):
    """Read a report definition and fill in defaults for anything it omits."""
    path = REPORTS_DIR / f"{slug}.json"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in REPORTS_DIR.glob("*.json"))) or "none"
        sys.exit(f"No report definition at {path}\nAvailable: {available}")

    definition = {**DEFAULTS, **json.loads(path.read_text())}
    if not definition.get("measure_status"):
        sys.exit(f"{path.name} is missing required key 'measure_status'")
    if "filter" in json.loads(path.read_text()):
        sys.exit(
            f"{path.name} uses the removed 'filter' key. The status clause is now "
            f"composed from 'measure_status'; put any remaining clauses in "
            f"'additional_filter'."
        )

    # These reports always age tickets from entry into the status that also
    # selects them, so the status clause is generated rather than typed. That
    # removes a whole class of definition where the filter and the measured
    # status silently disagree.
    definition["filter"] = " ".join(
        part for part in (
            f'status:"{definition["measure_status"]}"',
            definition["additional_filter"].strip(),
        ) if part
    )

    if "connection" in json.loads(path.read_text()):
        sys.exit(
            f"{path.name} uses the renamed 'connection' key. Rename it to "
            f"'project'; the repository is no longer stored."
        )

    project = get_project(definition["project"])
    if project is None:
        named = definition["project"] or "(no default set)"
        sys.exit(
            f"{path.name} references project '{named}', which is not in "
            f"projects.json. Available: "
            f"{', '.join(p['id'] for p in list_projects()) or 'none'}"
        )
    definition["project"] = project["id"]
    definition["_project"] = project
    definition["project_number"] = project["project_number"]
    definition["slug"] = slug
    return definition


def reports_using_project(project_id):
    """Slugs of every report bound to a project. Used before deleting one."""
    users = []
    for path in REPORTS_DIR.glob("*.json"):
        raw = json.loads(path.read_text())
        if raw.get("project") == project_id:
            users.append(path.stem)
    return sorted(users)


def ordered_slugs():
    """Every report slug, in board workflow order (then alphabetically).

    Ordering lives in the definitions rather than being derived from the board
    so a fresh clone with no cache and no network still lists them correctly.
    """
    definitions = [(load_definition(path.stem), path.stem) for path in REPORTS_DIR.glob("*.json")]
    return [slug for _, slug in sorted(definitions, key=lambda d: (d[0]["order"], d[1]))]


def cache_paths(slug):
    return {
        "raw": CACHE_DIR / f"{slug}-raw.json",
        "model": CACHE_DIR / f"{slug}-view-model.json",
        "html": CACHE_DIR / f"{slug}.html",
    }


def list_reports():
    """Show each definition's filter and what it measures."""
    slugs = ordered_slugs()
    if not slugs:
        sys.exit(f"No report definitions in {REPORTS_DIR}")

    print(f"\n{len(slugs)} report(s) in {REPORTS_DIR.relative_to(REPO_ROOT)}, "
          "in board workflow order:\n")
    for slug in slugs:
        definition = load_definition(slug)
        state = "built" if cache_paths(slug)["html"].exists() else "not built"
        print(f"  {slug}")
        print(f"    filter: {definition['filter']}")
        print(f"    ages from entry into '{definition['measure_status']}'  ({state})")
    print()


# --------------------------------------------------------------------------
# Stage 1 -- fetch
# --------------------------------------------------------------------------

def run_graphql(query, account=None):
    """Run a query, turning a failure into a clean exit for CLI callers."""
    try:
        return graphql(query, account)
    except GitHubError as exc:
        sys.exit(str(exc))


def fetch_report(slug):
    """Pull the filtered item set, plus each matched issue's status timeline."""
    definition = load_definition(slug)

    # The filter's own quotes sit inside the outer GraphQL string literal, so
    # they must be escaped or that literal terminates early -- which surfaces as
    # a confusing parse error rather than an invalid-filter error.
    escaped = definition["filter"].replace('"', '\\"')
    project = definition["_project"]
    account = account_for(project)
    scope = owner_scope(project)
    data = run_graphql(f'''
    {{
      {scope}(login: "{project["owner"]}") {{
        projectV2(number: {definition["project_number"]}) {{
          number
          title
          items(first: 100, query: "{escaped}") {{
            totalCount
            nodes {{
              content {{
                ... on Issue {{ id number title url repository {{ nameWithOwner }} }}
              }}
            }}
          }}
        }}
      }}
    }}''', account)

    board = data[scope]["projectV2"]
    items = board["items"]
    issues = [n["content"] for n in items["nodes"] if n.get("content")]

    if items["totalCount"] > len(issues):
        print(
            f"! Filter matched {items['totalCount']} items but only {len(issues)} were "
            "fetched. The 100-item page was exceeded.",
            file=sys.stderr,
        )

    raw = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "board": {"number": board["number"], "title": board["title"]},
        "filter": definition["filter"],
        "measure_status": definition["measure_status"],
        "project": project["id"],
        # Node ids, not issue numbers: a board can span repositories, where
        # numbers are no longer unique.
        "issue_ids": [issue["id"] for issue in issues],
        # Timelines keyed by issue number as a string, since JSON has no int keys.
        "timelines": fetch_histories([issue["id"] for issue in issues], account),
    }

    CACHE_DIR.mkdir(exist_ok=True)
    path = cache_paths(slug)["raw"]
    path.write_text(json.dumps(raw, indent=2))
    repos = {i["repository"]["nameWithOwner"] for i in issues if i.get("repository")}
    span = f" across {len(repos)} repos" if len(repos) > 1 else ""
    print(f"fetch     {len(issues)} issues{span} -> {path.relative_to(REPO_ROOT)}")


# --------------------------------------------------------------------------
# Stage 2 -- normalize
# --------------------------------------------------------------------------

def last_entry_into_status(timeline, project_number, status):
    """Timestamp of the most recent move into `status` on this board, or None."""
    entries = [
        event
        for event in timeline["timelineItems"]["nodes"]
        if event
        and (event.get("project") or {}).get("number") == project_number
        and event["status"] == status
    ]
    # Timeline nodes arrive oldest-first, so the last match starts the current stint.
    return parse_ts(entries[-1]["createdAt"]) if entries else None


def build_stats(days, oldest_issue, threshold, noun):
    """The four headline tiles, defined so an empty dataset still yields zeros."""
    count = len(days)
    ordered = sorted(days)
    if count:
        median = (
            ordered[(count - 1) // 2]
            if count % 2
            else (ordered[count // 2 - 1] + ordered[count // 2]) / 2
        )
        oldest = max(days)
        stale = sum(1 for d in days if d >= threshold)
        stale_pct = round(stale / count * 100)
    else:
        median = oldest = stale = stale_pct = 0

    return [
        {"label": "Tickets", "value": str(count), "unit": "", "note": noun},
        {"label": "Median wait", "value": f"{median:.1f}", "unit": "d",
         "note": "half have waited longer"},
        {"label": "Oldest", "value": str(round(oldest)), "unit": "d",
         "note": f"#{oldest_issue}" if oldest_issue else "no tickets"},
        {"label": f"Over {threshold} days", "value": str(stale), "unit": "",
         "note": f"{stale_pct}% of the set"},
    ]


def build_bins(rows, bin_days, max_bars=None):
    """Fixed-width day bands, including empty ones so gaps stay visible.

    With `max_bars` set, that many bands are drawn and every ticket past them
    lands in one trailing bucket. One-day bands capped at fourteen read as
    "which day did this clear on, and did it overrun the sprint?", which a
    uniform scale cannot show once a single outlier stretches the axis. The
    bucket is only added when something is actually out there.

    Each bin carries a short `label` for the axis and a fuller `range_label`
    for the tooltip, both composed here so the template never builds a phrase.
    """
    if not rows:
        return []

    longest = max(r["days"] for r in rows)
    banded = max_bars if max_bars is not None else int(longest // bin_days) + 1
    overflow_from = banded * bin_days
    needs_overflow = max_bars is not None and longest >= overflow_from

    bins = []
    for index in range(banded):
        low = index * bin_days
        high = low + bin_days
        bins.append({
            "low": low,
            "high": high - 1,
            # A one-day band is a point on the axis, so it gets a bare number
            # rather than a "3-3" range that reads as a mistake.
            "label": str(low) if bin_days == 1 else f"{low}-{high - 1}",
            "range_label": f"{low} to {high} days",
            "count": 0,
            "issues": [],
        })

    if needs_overflow:
        bins.append({
            "low": overflow_from,
            "high": None,
            "label": f"{overflow_from}+",
            "range_label": f"{overflow_from} days or more",
            "count": 0,
            "issues": [],
        })

    for row in rows:
        index = int(row["days"] // bin_days)
        # Anything past the last band belongs to the overflow bucket. Without
        # one, max_bars is unset and the bands already reach the oldest ticket.
        index = min(index, len(bins) - 1)
        bins[index]["count"] += 1
        bins[index]["issues"].append(f"#{row['number']}")
    return bins


def derived_notes(rows, unknown, batch_days, status, threshold):
    """Notes that state facts about THIS data, appended to the authored ones.

    Kept out of the view so the template never composes a sentence, and out of
    the definition file so they can't go stale against a fresh fetch.
    """
    notes = []
    if batch_days:
        notes.append(
            f"**Some spikes are batch moves, not a queue forming.** Several tickets entered "
            f"{status} on the same day on {', '.join(batch_days)}. Those bands age together, "
            f"so the shape reflects when someone swept the board as much as when the work "
            f"actually changed state."
        )
    notes.append(
        "**Wall-clock days, not business days.** A three-week wait includes three weekends."
    )
    notes.append(
        f"**Only the current stint counts.** A ticket that left {status} and came back is "
        f"aged from its latest entry, not its first."
    )
    if unknown:
        listed = ", ".join(f"#{u['number']}" for u in unknown)
        count = len(unknown)
        subject = "ticket has" if count == 1 else "tickets have"
        notes.append(
            f"**{count} {subject} no recorded entry event** and are excluded from the counts: "
            f"{listed}. This usually means their board history predates GitHub's status-event "
            f"tracking."
        )
    elif rows:
        notes.append(
            f"**Nothing is estimated.** All {len(rows)} tickets had a usable entry event in "
            f"their GitHub timeline."
        )
    return notes


def normalize_report(slug):
    """Turn raw payloads plus the definition's copy into the shape the view renders."""
    definition = load_definition(slug)
    paths = cache_paths(slug)
    if not paths["raw"].exists():
        sys.exit(f"No raw data at {paths['raw']}. Run: ticket_aging.py fetch {slug}")

    raw = json.loads(paths["raw"].read_text())
    project_number = raw["board"]["number"]
    status = definition["measure_status"]
    now = datetime.now(timezone.utc)

    if "issue_ids" not in raw:
        sys.exit(
            f"{paths['raw'].name} predates node-id fetching. "
            f"Run: ticket_aging.py fetch {slug}"
        )

    rows, unknown = [], []
    for issue_id in raw["issue_ids"]:
        timeline = raw["timelines"].get(issue_id)
        if not timeline:
            unknown.append({"number": "?", "title": "(no longer resolves)"})
            continue

        entered = last_entry_into_status(timeline, project_number, status)
        if entered is None:
            # No recorded entry event -- usually board history predating
            # GitHub's status-event tracking. Surfaced rather than counted as 0.
            unknown.append({"number": timeline["number"], "title": timeline["title"]})
            continue

        days = (now - entered).total_seconds() / 86400
        rows.append({
            "number": timeline["number"],
            "title": timeline["title"],
            # Straight from the API rather than assembled from config, so it
            # stays correct when a board spans repositories.
            "url": timeline["url"],
            "repo": (timeline.get("repository") or {}).get("nameWithOwner"),
            "days": round(days, 2),
            "days_display": f"{days:.1f}",
            "entered_date": entered.date().isoformat(),
        })

    rows.sort(key=lambda r: r["days"], reverse=True)

    entry_counts = {}
    for row in rows:
        entry_counts[row["entered_date"]] = entry_counts.get(row["entered_date"], 0) + 1
    batch_days = sorted(d for d, n in entry_counts.items() if n >= definition["batch_min"])

    max_days = max((r["days"] for r in rows), default=0)
    for row in rows:
        # Bar width for the table's inline scale, as a share of the longest wait.
        row["bar_pct"] = round(row["days"] / max_days * 100, 1) if max_days else 0
        row["is_batch"] = row["entered_date"] in batch_days

    bins = build_bins(rows, definition["bin_days"], definition["max_bars"])
    peak = max((b["count"] for b in bins), default=0)
    # Round the axis to a clean multiple of 5 so gridlines land on whole numbers.
    y_max = max(5, -(-peak // 5) * 5)

    copy = dict(definition.get("copy", {}))
    # The authored info panel is rendered here, not in the template: the view
    # model then carries HTML that is already safe, and the view stays dumb.
    copy["info_html"] = render_markup(copy.pop("info_panel", ""))
    copy["notes"] = list(copy.get("notes", [])) + derived_notes(
        rows, unknown, batch_days, status, definition["threshold_days"]
    )

    fetched = parse_ts(raw["fetched_at"]) if raw.get("fetched_at") else now
    view_model = {
        "header": {
            "board_number": project_number,
            "board_title": raw["board"]["title"],
            "status": status,
            "filter": definition["filter"],
            "updated_iso": fetched.isoformat(),
            "updated_display": fetched.strftime("%d %b %Y, %H:%M UTC"),
        },
        "copy": copy,
        "stats": build_stats(
            [r["days"] for r in rows],
            rows[0]["number"] if rows else None,
            definition["threshold_days"],
            copy.get("stat_noun", "tickets in this set"),
        ),
        "bins": bins,
        "rows": rows,
        "unknown": unknown,
        "axis": {
            "bin_days": definition["bin_days"],
            # Where the banded range ends, so the view can clamp the reference
            # line: past this point the axis is no longer linear.
            "banded_days": (definition["max_bars"] * definition["bin_days"]
                            if definition["max_bars"] else None),
            "y_max": y_max,
            "y_ticks": list(range(0, y_max + 1, 5)),
            "threshold_days": definition["threshold_days"],
        },
        "batch_days": batch_days,
    }

    CACHE_DIR.mkdir(exist_ok=True)
    paths["model"].write_text(json.dumps(view_model, indent=2))
    print(f"normalize {len(rows)} rows, {len(bins)} bins -> {paths['model'].relative_to(REPO_ROOT)}")
    if unknown:
        label = "ticket" if len(unknown) == 1 else "tickets"
        print(f"          {len(unknown)} {label} had no entry event; listed in the page")


# --------------------------------------------------------------------------
# Stage 3 -- build
# --------------------------------------------------------------------------

def build_report(slug):
    """Inject the view model and title into the template. Template is read-only."""
    paths = cache_paths(slug)
    if not paths["model"].exists():
        sys.exit(f"No view model at {paths['model']}. Run: ticket_aging.py normalize {slug}")
    if not VIEW_TEMPLATE.exists():
        sys.exit(f"No template at {VIEW_TEMPLATE}")

    template = VIEW_TEMPLATE.read_text()
    view_model = json.loads(paths["model"].read_text())

    # A literal "</script>" inside the JSON would close the data island early;
    # escaping the slash keeps the payload inert to the HTML parser.
    payload = json.dumps(view_model, separators=(",", ":")).replace("</", "<\\/")

    built, replaced = DATA_ISLAND.subn(
        lambda m: m.group(1) + payload + m.group(3), template, count=1
    )
    if not replaced:
        sys.exit('Template has no <script type="application/json" id="view-model"> block.')

    # The published artifact takes its name from <title>, so each report needs
    # its own -- one shared template, one title per report.
    title = view_model.get("copy", {}).get("title")
    if title:
        built = TITLE_TAG.sub(lambda m: m.group(1) + title + m.group(3), built, count=1)

    CACHE_DIR.mkdir(exist_ok=True)
    paths["html"].write_text(built)
    print(f"build     {len(built) / 1024:.0f} KB -> {paths['html'].relative_to(REPO_ROOT)}")


def print_report(slug):
    """Terminal view of a stored view model -- a second view over one model."""
    paths = cache_paths(slug)
    if not paths["model"].exists():
        sys.exit(f"No view model at {paths['model']}. Run: ticket_aging.py refresh {slug}")

    model = json.loads(paths["model"].read_text())
    header, copy = model["header"], model.get("copy", {})

    print(f"\n{copy.get('title', slug)}")
    print(f"Board {header['board_number']} ({header['board_title']})  filter: {header['filter']}")
    print(f"Last updated {header['updated_display']}\n")

    for row in model["rows"]:
        flag = " *" if row["is_batch"] else "  "
        print(f"  {float(row['days_display']):7.1f}d{flag} #{row['number']:<6} {row['title'][:58]}")

    if model["unknown"]:
        print("\n  No entry event found (excluded):")
        for row in model["unknown"]:
            print(f"    #{row['number']:<6} {row['title'][:58]}")

    print()
    for stat in model["stats"]:
        print(f"  {stat['label']:<16} {stat['value']}{stat['unit']}  ({stat['note']})")

    if model["bins"]:
        print("\n  Distribution (days waiting):")
        for bin_ in model["bins"]:
            print(f"    {bin_['label']:>7}  {'#' * bin_['count']} {bin_['count']}")
    if model["batch_days"]:
        print(f"\n  * entered on a batch-move day: {', '.join(model['batch_days'])}")
    print()


def refresh_report(slug):
    """Run all three stages in order. The normal way to update a report."""
    fetch_report(slug)
    normalize_report(slug)
    build_report(slug)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler, help_text in [
        ("fetch", fetch_report, "Stage 1: pull raw data from GitHub"),
        ("normalize", normalize_report, "Stage 2: derive the view model"),
        ("build", build_report, "Stage 3: inject the view model into the template"),
        ("refresh", refresh_report, "Run all three stages in order"),
        ("report", print_report, "Print a stored view model as a terminal table"),
    ]:
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("slug", help="Report definition name (see: ticket_aging.py list)")
        sub.set_defaults(handler=handler)

    subparsers.add_parser("list", help="Show available report definitions").set_defaults(
        handler=None
    )

    args = parser.parse_args()
    if args.handler is None:
        list_reports()
    else:
        args.handler(args.slug)


if __name__ == "__main__":
    main()
