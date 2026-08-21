#!/usr/bin/env python3
"""Report GitHub Projects v2 status-change history and time-in-status for issues.

Answers "how long has this ticket been sitting in Ready for QA?" -- and the
more general "when did it move through each column, and who moved it?"

Where the data comes from: GitHub records every project board status change as
a `ProjectV2ItemStatusChangedEvent` on the issue's timeline. Only the GraphQL
API exposes the useful fields (`previousStatus`, `status`, `project`); the REST
timeline endpoint returns the same events but with the from/to values stripped,
so REST alone can tell you *that* an issue moved and when, never *where to*.

Issues are fetched by **node id**, not by repository and number. A Projects v2
board can hold issues from many repositories, and issue numbers are only unique
within a repository, so number alone cannot identify a ticket on a multi-repo
board. Node ids also mean this layer never needs to know which repository an
issue lives in. See docs/github-api-notes.md for cost.

Caveats worth knowing before quoting a number from this:
  - Durations are wall-clock (calendar) time, not business hours.
  - History only goes back to when the issue was added to the board. Issues
    moved before GitHub started emitting these events (or bulk-imported) can
    show gaps.
  - An issue on more than one board (this project uses dev 172 and planning
    293) has an independent status timeline per board, so output is grouped by
    project. Use --project to narrow to one.

Usage:
  python3 status_history.py 101 --repo acme/example-board
  python3 status_history.py 101 102 --repo acme/example-board --project 1
  python3 status_history.py 101 --repo acme/example-board --status "Ready for QA"
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from github import graphql


# 100 covers every issue on this board today; the script warns rather than
# silently truncating if one ever exceeds it.
MAX_EVENTS = 100

# The fields fetched for each issue. `itemTypes:` filters server-side to just
# the status-change events, which keeps the response small and avoids paging
# past dozens of irrelevant label/comment events to reach the status ones.
ISSUE_FIELDS = """
    id
    number
    title
    url
    repository {{ nameWithOwner }}
    timelineItems(first: {max_events}, itemTypes: [PROJECT_V2_ITEM_STATUS_CHANGED_EVENT]) {{
      totalCount
      nodes {{
        ... on ProjectV2ItemStatusChangedEvent {{
          createdAt
          previousStatus
          status
          actor {{ login }}
          project {{ number title }}
        }}
      }}
    }}
"""


# Issues per GraphQL request. Each issue adds its own timeline connection to
# the query cost, so this trades request count against per-query cost; 20 keeps
# both modest for a board-wide sweep.
BATCH_SIZE = 20


def fetch_histories(node_ids, account=None):
    """Status-change timelines for many issues, keyed by node id.

    Keyed by id rather than issue number because a Projects v2 board can hold
    issues from several repositories, where numbers are no longer unique. The
    caller gets each issue's `url` and `repository` back too, so nothing
    downstream has to know or guess where an issue lives.
    """
    histories = {}
    for start in range(0, len(node_ids), BATCH_SIZE):
        histories.update(_fetch_batch(node_ids[start : start + BATCH_SIZE], account))
    return histories


def _fetch_batch(node_ids, account):
    ids = ", ".join(f'"{node_id}"' for node_id in node_ids)
    fields = ISSUE_FIELDS.format(max_events=MAX_EVENTS)
    data = graphql(f"{{ nodes(ids: [{ids}]) {{ ... on Issue {{ {fields} }} }} }}", account)
    # A null entry means an id no longer resolves; skip rather than crash so one
    # deleted issue cannot fail a whole refresh.
    return {node["id"]: node for node in data["nodes"] if node}


def resolve_issue_ids(owner, repo, numbers, account=None):
    """Node ids for issue numbers in one repository, for the standalone CLI.

    The pipeline never needs this: the board's item query already returns node
    ids. It exists so this file stays usable on its own, where a human types
    issue numbers rather than opaque ids.
    """
    blocks = "".join(
        f"i{number}: issue(number: {number}) {{ id }}" for number in numbers
    )
    data = graphql(f'{{ repository(owner: "{owner}", name: "{repo}") {{ {blocks} }} }}', account)
    found = data.get("repository") or {}
    return [node["id"] for node in found.values() if node]


def parse_ts(value):
    """Parse GitHub's ISO-8601 'Z' timestamps into aware datetimes."""
    # fromisoformat only learned to accept a literal 'Z' in Python 3.11, so
    # swap it for the explicit UTC offset that every version understands.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def humanize(delta):
    """Render a timedelta as a compact 'Nd Nh' / 'Nh Nm' string."""
    total_minutes = int(delta.total_seconds() // 60)
    days, remainder = divmod(total_minutes, 60 * 24)
    hours, minutes = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def build_intervals(events, now):
    """Turn a board's ordered status events into [(status, entered, duration, is_current)].

    Each event marks entry into a status; that status ends when the next event
    fires, or -- for the final event -- is still running as of `now`.
    """
    intervals = []
    for index, event in enumerate(events):
        entered = parse_ts(event["createdAt"])
        is_current = index == len(events) - 1
        ended = now if is_current else parse_ts(events[index + 1]["createdAt"])
        intervals.append((event["status"], entered, ended - entered, is_current, event))
    return intervals


def group_by_project(events):
    """Split one issue's events into {(project_number, title): [events]}.

    An issue tracked on two boards produces one interleaved timeline; durations
    are only meaningful within a single board, so they must be separated before
    any elapsed time is computed.
    """
    boards = {}
    for event in events:
        project = event.get("project") or {}
        key = (project.get("number"), project.get("title") or "(unknown project)")
        boards.setdefault(key, []).append(event)
    return boards


def report_issue(issue, now, project_filter, status_filter):
    timeline = issue["timelineItems"]
    events = [e for e in timeline["nodes"] if e]

    print(f"\n#{issue['number']} — {issue['title']}")

    if not events:
        print("  (no status-change history recorded)")
        return
    if timeline["totalCount"] > MAX_EVENTS:
        print(f"  ! Only the first {MAX_EVENTS} of {timeline['totalCount']} events shown.")

    for (project_number, project_title), board_events in group_by_project(events).items():
        if project_filter and project_number != project_filter:
            continue

        print(f"  Board {project_number} — {project_title}")
        intervals = build_intervals(board_events, now)

        for status, entered, duration, is_current, event in intervals:
            # An empty previousStatus means this was the initial placement when
            # the issue was first added to the board, not a move between columns.
            origin = event["previousStatus"] or "(added to board)"
            actor = (event.get("actor") or {}).get("login", "unknown")
            marker = "*" if is_current else " "
            # Pad the duration so the actor column lines up down the page.
            elapsed = f"[{humanize(duration)}]".ljust(11)
            print(
                f"    {entered:%Y-%m-%d %H:%M} UTC  {marker} {origin} → {status}"
                f"  {elapsed} by {actor}"
            )

        current_status, _, current_duration, _, _ = intervals[-1]
        print(f"    Currently: {current_status} for {humanize(current_duration)}")

        if status_filter:
            # Sum every visit to the named status, since a ticket can bounce
            # back into QA more than once and total waiting time is what matters.
            matches = [i for i in intervals if i[0].lower() == status_filter.lower()]
            if matches:
                total = sum((i[2] for i in matches), timedelta())
                visits = f"{len(matches)} visit{'s' if len(matches) > 1 else ''}"
                print(f"    Time in '{status_filter}': {humanize(total)} across {visits}")
            else:
                print(f"    Never in '{status_filter}' on this board.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("numbers", nargs="+", type=int, help="Issue number(s)")
    parser.add_argument(
        "--repo", required=True, metavar="OWNER/NAME",
        help="Repository the issue numbers belong to, e.g. acme/example-board",
    )
    parser.add_argument("--project", type=int, help="Only report this project board number")
    parser.add_argument("--status", help="Also total time spent in this status")
    args = parser.parse_args()

    if "/" not in args.repo:
        sys.exit("--repo must look like OWNER/NAME, e.g. acme/example-board")
    owner, repo = args.repo.split("/", 1)

    now = datetime.now(timezone.utc)
    # Numbers are repository-scoped, so they are resolved to node ids first;
    # everything past this point works in ids.
    histories = fetch_histories(resolve_issue_ids(owner, repo, args.numbers))
    by_number = {issue["number"]: issue for issue in histories.values()}

    for number in args.numbers:
        issue = by_number.get(number)
        if not issue:
            print(f"\n#{number} - not found in {args.repo}")
            continue
        report_issue(issue, now, args.project, args.status)
    print()


if __name__ == "__main__":
    main()
