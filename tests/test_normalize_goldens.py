"""normalize must reproduce its golden output exactly, for every fixture.

This is the broadest test in the suite. One comparison covers every stat, bin,
row, note and label at once, so a change nobody thought to assert on is still
caught.

A failure here is not automatically a stale golden. If the diff shows ages
shifting, someone has reintroduced a clock into the age calculation, which is the
bug #2 fixed. Read the diff before reaching for --update-goldens.
"""
import json
import unittest

from tests.helpers import EXPECTED, FIXTURE_SLUGS, TemporaryWorkspace, run_cli


class NormalizeGoldens(unittest.TestCase):

    def test_every_fixture_matches_its_golden(self):
        for slug in FIXTURE_SLUGS:
            with self.subTest(fixture=slug), TemporaryWorkspace() as workspace:
                result = run_cli("normalize", slug, workspace=workspace)
                self.assertEqual(result.returncode, 0,
                                 f"normalize failed:\n{result.stderr}")

                produced = json.loads(
                    (workspace / "cache" / f"{slug}-view-model.json").read_text())
                golden = json.loads(
                    (EXPECTED / f"{slug}-view-model.json").read_text())
                self.assertEqual(produced, golden,
                                 f"{slug} no longer matches its golden")

    def test_every_fixture_has_a_golden(self):
        # A fixture added without a golden would otherwise be silently untested.
        missing = [s for s in FIXTURE_SLUGS
                   if not (EXPECTED / f"{s}-view-model.json").exists()]
        self.assertEqual(missing, [], "fixtures with no golden")

    def test_running_twice_produces_identical_output(self):
        # The property that makes goldens viable at all: same input, same output,
        # no matter when it runs.
        with TemporaryWorkspace() as workspace:
            run_cli("normalize", "baseline", workspace=workspace)
            first = (workspace / "cache" / "baseline-view-model.json").read_text()
            run_cli("normalize", "baseline", workspace=workspace)
            second = (workspace / "cache" / "baseline-view-model.json").read_text()
        self.assertEqual(first, second, "normalize is not deterministic")


if __name__ == "__main__":
    unittest.main()
