"""The GitHub filter is composed, never typed.

Every report ages tickets from their entry into the same status that selects
them. Letting a definition write its own filter meant the filter and the
measured status could silently disagree — the chart would then be titled for one
status while counting another, with nothing to show anything was wrong. So
`load_definition` builds the status clause from `measure_status` and appends only
what the definition adds.

These run the CLI rather than importing it: the definition has to exist on disk
to be loaded, and each case needs a different one.
"""
import json
import shutil
import unittest

from tests.helpers import TemporaryWorkspace, run_cli


def write_definition(workspace, slug, **overrides):
    """Add a report to a workspace, reusing the baseline's fetched data."""
    definition = json.loads(
        (workspace / "definitions" / "baseline.json").read_text())
    definition.update(overrides)
    (workspace / "definitions" / f"{slug}.json").write_text(json.dumps(definition))
    shutil.copy(workspace / "cache" / "baseline-raw.json",
                workspace / "cache" / f"{slug}-raw.json")


def composed_filter(workspace, slug):
    """The filter a report ends up with, read back off the built view model."""
    result = run_cli("normalize", slug, workspace=workspace)
    if result.returncode != 0:
        raise AssertionError(f"normalize failed:\n{result.stderr}")
    model = json.loads((workspace / "cache" / f"{slug}-view-model.json").read_text())
    return model["header"]["filter"]


class FilterComposition(unittest.TestCase):

    def test_status_clause_is_generated_from_measure_status(self):
        with TemporaryWorkspace() as workspace:
            write_definition(workspace, "generated",
                             measure_status="Ready for QA", additional_filter="")
            self.assertEqual(composed_filter(workspace, "generated"),
                             'status:"Ready for QA"')

    def test_additional_clauses_are_appended(self):
        with TemporaryWorkspace() as workspace:
            write_definition(workspace, "extra", measure_status="In Progress",
                             additional_filter="is:open -type:Epic,Feature")
            self.assertEqual(composed_filter(workspace, "extra"),
                             'status:"In Progress" is:open -type:Epic,Feature')

    def test_a_blank_additional_filter_leaves_no_trailing_space(self):
        with TemporaryWorkspace() as workspace:
            write_definition(workspace, "spaced", additional_filter="   ")
            composed = composed_filter(workspace, "spaced")
            self.assertEqual(composed, composed.strip())
            self.assertNotIn("  ", composed)

    def test_a_multi_word_status_stays_quoted(self):
        # Unquoted, "Dev Done / Peer Review" would parse as several clauses.
        with TemporaryWorkspace() as workspace:
            write_definition(workspace, "multiword",
                             measure_status="Dev Done / Peer Review")
            self.assertIn('status:"Dev Done / Peer Review"',
                          composed_filter(workspace, "multiword"))

    def test_the_filter_shown_to_the_reader_names_the_measured_status(self):
        # The header prints both; they describe the same thing, so they must agree.
        with TemporaryWorkspace() as workspace:
            write_definition(workspace, "agreeing", measure_status="PR Validated")
            result = run_cli("normalize", "agreeing", workspace=workspace)
            self.assertEqual(result.returncode, 0, result.stderr)
            header = json.loads(
                (workspace / "cache" / "agreeing-view-model.json").read_text())["header"]
            self.assertIn(f'status:"{header["status"]}"', header["filter"])


class RemovedKeys(unittest.TestCase):
    """Old definitions should fail loudly rather than be silently misread."""

    def test_a_hand_written_filter_key_is_refused(self):
        with TemporaryWorkspace() as workspace:
            write_definition(workspace, "legacy", filter='status:"Anything"')
            result = run_cli("normalize", "legacy", workspace=workspace)
            self.assertNotEqual(result.returncode, 0)
            message = result.stdout + result.stderr
            self.assertIn("'filter'", message)
            self.assertIn("additional_filter", message,
                          "the error should say where the clauses go instead")

    def test_the_renamed_connection_key_is_refused(self):
        with TemporaryWorkspace() as workspace:
            write_definition(workspace, "renamed", connection="acme-board")
            result = run_cli("normalize", "renamed", workspace=workspace)
            self.assertNotEqual(result.returncode, 0)
            message = result.stdout + result.stderr
            self.assertIn("'connection'", message)
            self.assertIn("'project'", message, "the error should name the new key")

    def test_a_missing_measure_status_is_refused(self):
        with TemporaryWorkspace() as workspace:
            write_definition(workspace, "statusless", measure_status="")
            result = run_cli("normalize", "statusless", workspace=workspace)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("measure_status", result.stdout + result.stderr)

    def test_an_unknown_report_lists_the_ones_that_exist(self):
        with TemporaryWorkspace() as workspace:
            result = run_cli("normalize", "no-such-report", workspace=workspace)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("baseline", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
