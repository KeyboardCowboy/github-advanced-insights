#!/usr/bin/env python3
"""Generate the synthetic workspace the tests run against.

The fixtures are committed, so the tests do not depend on this script running.
It exists because the interesting cases are edge cases, and a spec like

    ticket(3, "...", entered="2026-06-01", status="In Progress")

says what is being tested where a hand-written wall of JSON does not. Changing a
case means editing one line and re-running, rather than editing several nested
objects consistently.

Everything here is fixed: a frozen `fetched_at`, invented repositories, and issue
numbers that cannot collide with anything real. Nothing reaches the network, and
running this twice produces identical output.

    python3 tests/fixtures/build_fixtures.py                    # fixtures only
    python3 tests/fixtures/build_fixtures.py --update-goldens   # and the expected output

Regenerating fixtures without --update-goldens deliberately leaves the goldens
stale, so the tests fail. That is the safe direction: a golden should only change
when someone decided it should, and the diff is where a reviewer sees it.

The cases are chosen because a live board cannot reliably produce them, which is
the whole reason not to test against real data:

  baseline        the ordinary path, several tickets at different ages
  no-entry-event  a ticket whose history predates status tracking
  multi-repo      one board holding issues from two repositories
  batch-day       four tickets entering on one day, which triggers batch flagging
  overflow        more tickets than max_bars allows, producing the "+" bucket
  empty           a report that matches nothing and must render zeros
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
WORKSPACE = FIXTURES / "workspace"

# Frozen so ages never drift. normalize measures from this, not from now().
FETCHED_AT = "2026-06-15T12:00:00+00:00"

BOARD = {"number": 1, "title": "Acme Delivery"}
PROJECT_ID = "acme-board"
DEFAULT_REPO = "acme/widgets"


def event(day, to_status, previous="", actor="a-person"):
    """One ProjectV2ItemStatusChangedEvent. An empty `previous` means the item
    was added to the board rather than moved between columns."""
    return {
        "createdAt": f"{day}T09:00:00Z",
        "previousStatus": previous,
        "status": to_status,
        "actor": {"login": actor},
        "project": BOARD,
    }


def ticket(number, title, history, repo=DEFAULT_REPO):
    """One issue and its status history.

    `history` is a list of (day, status) in order. The first entry is the item
    being added to the board; each later one records where it came from, which is
    what the real API returns.
    """
    events, previous = [], ""
    for day, status in history:
        events.append(event(day, status, previous))
        previous = status
    return {
        "id": f"I_fixture{number}",
        "number": number,
        "title": title,
        "url": f"https://github.com/{repo}/issues/{number}",
        "repository": {"nameWithOwner": repo},
        "timelineItems": {"totalCount": len(events), "nodes": events},
    }


def raw(measure_status, tickets, additional_filter="is:open"):
    composed = " ".join(p for p in (f'status:"{measure_status}"', additional_filter) if p)
    return {
        "fetched_at": FETCHED_AT,
        "board": BOARD,
        "filter": composed,
        "measure_status": measure_status,
        "project": PROJECT_ID,
        "issue_ids": [t["id"] for t in tickets],
        "timelines": {t["id"]: t for t in tickets},
    }


def definition(measure_status, **overrides):
    base = {
        "project": PROJECT_ID,
        "order": 10,
        "measure_status": measure_status,
        "additional_filter": "is:open",
        "bin_days": 7,
        "max_bars": None,
        "threshold_days": 14,
        "batch_min": 4,
        "copy": {"title": overrides.pop("title", measure_status)},
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# The cases
# --------------------------------------------------------------------------

def baseline():
    """Several tickets at different ages. The ordinary path."""
    tickets = [
        ticket(101, "Add pagination to the widget list",
               [("2026-05-02", "Todo"), ("2026-05-04", "In Progress")]),
        ticket(102, "Fix off-by-one in the date picker",
               [("2026-05-20", "Todo"), ("2026-06-01", "In Progress")]),
        ticket(103, "Refactor the export job",
               [("2026-06-08", "Todo"), ("2026-06-09", "In Progress")]),
        ticket(104, "Upgrade the build toolchain",
               [("2026-06-13", "In Progress")]),
        # Left and came back: aged from the latest entry, not the first.
        ticket(105, "Rework the settings drawer",
               [("2026-04-01", "In Progress"), ("2026-04-10", "Todo"),
                ("2026-06-11", "In Progress")]),
    ]
    return "baseline", raw("In Progress", tickets), definition("In Progress", title="Baseline")


def no_entry_event():
    """A ticket with no event for the measured status.

    Its board history predates status tracking, so it cannot be aged. It belongs
    in `unknown` and must not be counted as zero days old.
    """
    tickets = [
        ticket(201, "Normal ticket", [("2026-06-05", "In Review")]),
        ticket(202, "Another normal one", [("2026-06-10", "In Review")]),
        ticket(203, "History predates status tracking", []),
    ]
    return "no-entry-event", raw("In Review", tickets), definition("In Review", title="No entry event")


def multi_repo():
    """One board holding issues from two repositories.

    Issue numbers are only unique within a repository, so 301 appears twice on
    purpose: the tool keys on node id and must keep both.
    """
    tickets = [
        ticket(301, "Widget crash on save", [("2026-06-01", "Blocked")], repo="acme/widgets"),
        ticket(301, "Tools CLI hangs", [("2026-06-03", "Blocked")], repo="acme/tools"),
        ticket(302, "Shared library bump", [("2026-06-09", "Blocked")], repo="acme/tools"),
    ]
    # Distinct ids despite the repeated number.
    tickets[1]["id"] = "I_fixture301b"
    return "multi-repo", raw("Blocked", tickets), definition("Blocked", title="Multi repo")


def batch_day():
    """Four tickets entering on one day, which is what batch_min flags."""
    tickets = [
        ticket(400 + n, f"Swept ticket {n}", [("2026-06-02", "Ready for QA")])
        for n in range(1, 5)
    ] + [
        ticket(405, "Arrived on its own", [("2026-06-10", "Ready for QA")]),
    ]
    return "batch-day", raw("Ready for QA", tickets), definition("Ready for QA", title="Batch day")


def overflow():
    """More tickets than max_bars allows, producing the trailing bucket.

    One-day bands capped at five bars: anything six days or older collapses into
    a single "5+" bin, and the bucket must appear only because data runs past it.
    """
    days = ["2026-06-15", "2026-06-14", "2026-06-13", "2026-06-11", "2026-06-01", "2026-04-20"]
    tickets = [
        ticket(500 + n, f"Ticket aged {n}", [(day, "In QA")])
        for n, day in enumerate(days, start=1)
    ]
    return ("overflow", raw("In QA", tickets),
            definition("In QA", bin_days=1, max_bars=5, threshold_days=5, title="Overflow"))


def empty():
    """A report matching nothing. Must render zeros rather than fail."""
    return "empty", raw("Done", []), definition("Done", title="Empty")


CASES = [baseline, no_entry_event, multi_repo, batch_day, overflow, empty]


# The board's own Status options, in workflow order. The dashboard puts these
# on the x-axis, so the order is part of the fixture rather than something
# derived from whichever column happens to be busiest.
BOARD_STATUSES = ["Todo/Ready", "In Progress", "Dev Done / Peer Review",
                  "PR Validated", "Ready for QA", "Done"]


def dashboard_raw():
    """One board-wide file, pooling every case's tickets.

    The dashboard fetches the whole board at once rather than one status at a
    time, so its fixture is the union of the per-report ones. Pooling them also
    gives it what no single report has: tickets spread across several columns,
    which is the entire point of the chart.

    Some fixture tickets sit in columns the board no longer declares. That is
    deliberate -- a status can be removed while tickets are still in it, and
    dropping those would quietly shrink the total.
    """
    pooled = {
        "fetched_at": FETCHED_AT,
        "project": PROJECT_ID,
        "project_label": "Acme Delivery",
        "board": BOARD,
        "filter": "is:open -type:Epic,Feature",
        "statuses": BOARD_STATUSES,
        "issue_ids": [],
        "timelines": {},
    }
    for case in CASES:
        _, raw_data, _ = case()
        for issue_id in raw_data["issue_ids"]:
            if issue_id in pooled["timelines"]:
                continue
            pooled["issue_ids"].append(issue_id)
            pooled["timelines"][issue_id] = raw_data["timelines"][issue_id]
    return pooled


EXPECTED = FIXTURES / "expected"
REPO_ROOT = FIXTURES.parent.parent


def update_goldens(slugs):
    """Run normalize over each fixture and record its output as the golden.

    Shells out to the CLI rather than importing, because workspace paths are
    resolved at import time from GH_INSIGHTS_HOME; a subprocess is both simpler
    and closer to how the tests will run it.
    """
    EXPECTED.mkdir(exist_ok=True)
    env = {**os.environ, "GH_INSIGHTS_HOME": str(WORKSPACE)}
    for slug in slugs:
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "ticket_aging.py"), "normalize", slug],
            capture_output=True, text=True, env=env, cwd=REPO_ROOT)
        if result.returncode != 0:
            sys.exit(f"normalize failed for {slug}:\n{result.stderr}")
        produced = WORKSPACE / "cache" / f"{slug}-view-model.json"
        (EXPECTED / f"{slug}-view-model.json").write_text(produced.read_text())
        print(f"  golden updated: {slug}")

    # The dashboard is a second pipeline over the same raw data, so it gets the
    # same treatment: one golden covering every column, band and row at once.
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "dashboard.py"), "normalize", PROJECT_ID],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    if result.returncode != 0:
        sys.exit(f"dashboard normalize failed:\n{result.stderr}")
    produced = WORKSPACE / "cache" / f"dashboard-{PROJECT_ID}-view-model.json"
    (EXPECTED / f"dashboard-{PROJECT_ID}-view-model.json").write_text(
        produced.read_text())
    print("  golden updated: dashboard")


def main():
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    (WORKSPACE / "definitions").mkdir(parents=True)
    (WORKSPACE / "cache").mkdir()

    (WORKSPACE / "projects.json").write_text(json.dumps({
        "projects": [{
            "id": PROJECT_ID,
            "label": "Acme Delivery",
            "account": None,
            "owner": "acme",
            "owner_type": "organization",
            "project_number": BOARD["number"],
        }],
        "default_project": PROJECT_ID,
    }, indent=2) + "\n")

    slugs = []
    for case in CASES:
        slug, raw_data, defn = case()
        (WORKSPACE / "definitions" / f"{slug}.json").write_text(
            json.dumps(defn, indent=2) + "\n")
        (WORKSPACE / "cache" / f"{slug}-raw.json").write_text(
            json.dumps(raw_data, indent=2) + "\n")
        print(f"  {slug:<16} {len(raw_data['issue_ids'])} tickets")
        slugs.append(slug)

    pooled = dashboard_raw()
    (WORKSPACE / "cache" / f"dashboard-{PROJECT_ID}-raw.json").write_text(
        json.dumps(pooled, indent=2) + "\n")
    print(f"  {'dashboard':<16} {len(pooled['issue_ids'])} tickets "
          f"across {len(pooled['statuses'])} declared columns")

    if "--update-goldens" in sys.argv:
        print()
        update_goldens(slugs)
    else:
        print("\n  Fixtures written. Goldens NOT updated; pass --update-goldens "
              "if the\n  expected output is meant to change.")


if __name__ == "__main__":
    main()
