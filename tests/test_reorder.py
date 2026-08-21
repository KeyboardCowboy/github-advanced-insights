"""reorder_reports renumbers definitions without editing anything else.

Ordering used to mean opening each report's form and editing a relative integer
by hand (#14). The sidebar can now do it, which means a code path that rewrites
every definition at once — so what it must *not* touch matters as much as what
it does.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import tests.helpers  # noqa: F401  -- points the tool at the fixtures
from tests.helpers import FIXTURE_WORKSPACE, REPO_ROOT


class Reorder(unittest.TestCase):
    """Runs in a subprocess against a temp workspace.

    report_store binds REPORTS_DIR at import from the workspace, and these
    tests write, so they need a copy of their own rather than the checked-out
    fixtures.
    """

    def run_in_workspace(self, code):
        # The directory has to outlive this call: callers read the files back to
        # check what was written. Cleanup is deferred to the end of the test
        # rather than the end of this method.
        temp = tempfile.mkdtemp(prefix="gh-insights-reorder-")
        self.addCleanup(shutil.rmtree, temp, ignore_errors=True)
        workspace = Path(temp) / "workspace"
        shutil.copytree(FIXTURE_WORKSPACE, workspace)
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=REPO_ROOT,
            capture_output=True, text=True,
            env={"GH_INSIGHTS_HOME": str(workspace), "PATH": ""})
        return result, workspace

    def test_it_assigns_spaced_values_in_the_order_given(self):
        result, workspace = self.run_in_workspace("""
import json, report_store
written = report_store.reorder_reports(
    ["overflow", "baseline", "empty", "batch-day", "multi-repo", "no-entry-event"])
print(json.dumps(written))
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        written = json.loads(result.stdout)
        self.assertEqual(written["overflow"], 10)
        self.assertEqual(written["baseline"], 20)
        self.assertEqual(written["no-entry-event"], 60)

    def test_gaps_are_left_between_neighbours(self):
        # So a later hand-edit can slot one report between two without
        # renumbering everything after it.
        result, _ = self.run_in_workspace("""
import json, report_store
print(json.dumps(sorted(report_store.reorder_reports(
    sorted(report_store.existing_slugs())).values())))
""")
        self.assertEqual(json.loads(result.stdout), [10, 20, 30, 40, 50, 60])

    def test_nothing_but_order_is_touched(self):
        """Reordering is not an edit of what a report measures."""
        before = json.loads(
            (FIXTURE_WORKSPACE / "definitions" / "baseline.json").read_text())
        result, workspace = self.run_in_workspace("""
import report_store
report_store.reorder_reports(sorted(report_store.existing_slugs()))
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads((workspace / "definitions" / "baseline.json").read_text())

        before.pop("order", None)
        moved_order = after.pop("order", None)
        self.assertEqual(after, before, "a key other than 'order' changed")
        self.assertIsNotNone(moved_order)

    def test_a_partial_list_is_refused(self):
        """A report added or deleted in another tab since the page loaded.

        Renumbering the rest anyway would drop it to an arbitrary position, so
        this fails loudly instead and says which reports were missing.
        """
        result, workspace = self.run_in_workspace("""
import report_store
try:
    report_store.reorder_reports(["baseline", "overflow"])
    print("NOT REFUSED")
except ValueError as exc:
    print(exc)
""")
        self.assertIn("missing", result.stdout)
        self.assertIn("batch-day", result.stdout)
        # And nothing was written.
        unchanged = json.loads(
            (workspace / "definitions" / "baseline.json").read_text())
        self.assertEqual(unchanged["order"], 10)

    def test_an_unknown_slug_is_refused(self):
        result, _ = self.run_in_workspace("""
import report_store
try:
    report_store.reorder_reports(report_store.existing_slugs() + ["ghost"])
    print("NOT REFUSED")
except ValueError as exc:
    print(exc)
""")
        self.assertIn("ghost", result.stdout)

    def test_a_repeated_slug_is_refused(self):
        # Would otherwise silently drop a report out of the ordering.
        result, _ = self.run_in_workspace("""
import report_store
try:
    report_store.reorder_reports(["baseline"] * len(report_store.existing_slugs()))
    print("NOT REFUSED")
except ValueError as exc:
    print(exc)
""")
        self.assertIn("twice", result.stdout)

    def test_the_new_order_is_what_the_sidebar_then_reads(self):
        # ordered_slugs is what the server serves the sidebar, so the round trip
        # is what actually matters -- not the numbers in isolation.
        result, _ = self.run_in_workspace("""
import json, report_store, ticket_aging
report_store.reorder_reports(
    ["overflow", "empty", "baseline", "batch-day", "multi-repo", "no-entry-event"])
print(json.dumps(ticket_aging.ordered_slugs()))
""")
        self.assertEqual(
            json.loads(result.stdout),
            ["overflow", "empty", "baseline", "batch-day", "multi-repo",
             "no-entry-event"])


if __name__ == "__main__":
    unittest.main()
