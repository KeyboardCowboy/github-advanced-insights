"""The board-level pipeline behind the aging work-in-progress scatter (#10).

A report knows one column. This pipeline groups every open ticket by the column
it is in *now*, which is a different question and a different failure mode: a
report that loses a ticket shows a smaller histogram, while this one would move
it to the wrong column and still look complete.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import tests.helpers  # noqa: F401  -- points the tool at the fixtures
import dashboard
from tests.helpers import EXPECTED, FIXTURE_WORKSPACE, REPO_ROOT

PROJECT = "acme-board"


def event(day, status, project_number=1):
    return {"createdAt": f"2026-06-{day:02d}T09:00:00Z", "status": status,
            "previousStatus": "", "actor": {"login": "a-person"},
            "project": {"number": project_number, "title": "Acme Delivery"}}


def timeline(*events):
    return {"id": "I_x", "number": 1, "title": "A ticket", "url": "http://x",
            "repository": {"nameWithOwner": "acme/web"},
            "timelineItems": {"totalCount": len(events), "nodes": list(events)}}


class Percentile(unittest.TestCase):
    """Nearest-rank, so every line drawn is the age of a real ticket."""

    def test_empty_input_has_no_percentile(self):
        # Not zero: zero would draw a line along the axis implying every ticket
        # is brand new, on a board with nothing on it.
        self.assertIsNone(dashboard.percentile([], 50))

    def test_the_median_of_an_odd_set(self):
        self.assertEqual(dashboard.percentile([1, 5, 9], 50), 5)

    def test_it_returns_a_value_that_is_in_the_set(self):
        ages = [1, 2, 3, 4]
        for pct in (50, 85, 95):
            self.assertIn(dashboard.percentile(ages, pct), ages)

    def test_the_top_percentile_is_the_oldest(self):
        self.assertEqual(dashboard.percentile([1, 2, 30], 95), 30)

    def test_a_single_ticket_is_every_percentile(self):
        for pct in (50, 85, 95):
            self.assertEqual(dashboard.percentile([7], pct), 7)


class CurrentStatus(unittest.TestCase):
    """Where a ticket is now, and when it got there."""

    def test_the_last_move_names_the_current_column(self):
        status, entered = dashboard.current_status_entry(
            timeline(event(1, "Todo"), event(4, "In Progress"),
                     event(9, "Ready for QA")), 1)
        self.assertEqual(status, "Ready for QA")
        self.assertEqual(entered.day, 9)

    def test_returning_to_a_column_is_dated_from_the_return(self):
        # The same reasoning the reports use: this measures the current,
        # unbroken stint, not the total time ever spent in that column.
        status, entered = dashboard.current_status_entry(
            timeline(event(1, "In Progress"), event(3, "Blocked"),
                     event(8, "In Progress")), 1)
        self.assertEqual(status, "In Progress")
        self.assertEqual(entered.day, 8)

    def test_events_from_another_board_are_ignored(self):
        # An issue can sit on several boards; only this one's moves count.
        status, _ = dashboard.current_status_entry(
            timeline(event(1, "In Progress"), event(9, "Shipped", project_number=7)), 1)
        self.assertEqual(status, "In Progress")

    def test_no_events_means_no_answer(self):
        status, entered = dashboard.current_status_entry(timeline(), 1)
        self.assertIsNone(status)
        self.assertIsNone(entered)


class NormalizeAgainstTheGolden(unittest.TestCase):
    """Runs in a subprocess: workspace paths bind at import, and this writes."""

    def normalize(self):
        temp = tempfile.mkdtemp(prefix="gh-insights-dashboard-")
        self.addCleanup(shutil.rmtree, temp, ignore_errors=True)
        workspace = Path(temp) / "workspace"
        shutil.copytree(FIXTURE_WORKSPACE, workspace)
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "dashboard.py"), "normalize", PROJECT],
            cwd=REPO_ROOT, capture_output=True, text=True,
            env={"GH_INSIGHTS_HOME": str(workspace), "PATH": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(
            (workspace / "cache" / f"dashboard-{PROJECT}-view-model.json").read_text())

    def test_it_matches_its_golden(self):
        golden = json.loads(
            (EXPECTED / f"dashboard-{PROJECT}-view-model.json").read_text())
        self.assertEqual(self.normalize(), golden)

    def test_columns_are_in_board_order_not_alphabetical(self):
        """A workflow chart whose columns are out of workflow order is worse
        than none: it invites reading a sequence that does not exist."""
        model = self.normalize()
        declared = [c["status"] for c in model["columns"] if not c["off_board"]]
        self.assertEqual(declared,
                         ["Todo/Ready", "In Progress", "Dev Done / Peer Review",
                          "PR Validated", "Ready for QA", "Done"])

    def test_a_column_the_board_no_longer_declares_still_shows_its_tickets(self):
        # Dropping them would quietly shrink the total, which is the kind of
        # wrong that looks right.
        model = self.normalize()
        off_board = [c for c in model["columns"] if c["off_board"]]
        self.assertTrue(off_board, "the fixture has tickets in retired columns")
        self.assertTrue(all(c["count"] for c in off_board))

    def test_every_ticket_is_counted_once(self):
        model = self.normalize()
        plotted = sum(c["count"] for c in model["columns"])
        self.assertEqual(plotted, model["total"])
        self.assertEqual(plotted + len(model["unknown"]),
                         len(json.loads((FIXTURE_WORKSPACE / "cache"
                                         / f"dashboard-{PROJECT}-raw.json").read_text())
                             ["issue_ids"]))

    def test_a_ticket_that_cannot_be_aged_is_listed_not_plotted(self):
        # Plotting it at zero would read as brand new work.
        model = self.normalize()
        self.assertTrue(model["unknown"])
        self.assertNotIn("?", [t["number"] for c in model["columns"]
                               for t in c["tickets"]])

    def test_bands_are_ordered_and_within_the_data(self):
        model = self.normalize()
        days = [band["days"] for band in model["bands"]]
        self.assertEqual(days, sorted(days), "percentiles must not go backwards")
        self.assertLessEqual(days[-1], model["oldest"])

    def test_ages_come_from_the_fetch_not_from_now(self):
        # The same guarantee as the reports (#2): the ticket set describes one
        # moment, so the ages have to describe that moment too.
        first = self.normalize()
        second = self.normalize()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
