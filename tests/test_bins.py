"""build_bins turns a list of aged tickets into the chart's bars.

The interesting behaviour is at the edges: the overflow bucket that only appears
when something is actually out past the last band, and empty bands in the middle
that have to survive so a gap in the data reads as a gap rather than closing up.
"""
import unittest

import tests.helpers  # noqa: F401  -- points the tool at the fixtures; must precede the import below
from ticket_aging import build_bins


def rows(*ages):
    return [{"days": days, "number": 100 + i} for i, days in enumerate(ages)]


class BuildBins(unittest.TestCase):

    def test_no_rows_produces_no_bins(self):
        # An empty report should render an empty chart, not raise on max().
        self.assertEqual(build_bins([], bin_days=7), [])

    def test_bands_reach_the_oldest_ticket_when_uncapped(self):
        bins = build_bins(rows(0, 20), bin_days=7)
        self.assertEqual([b["label"] for b in bins], ["0-6", "7-13", "14-20"])
        self.assertEqual([b["count"] for b in bins], [1, 0, 1])

    def test_empty_middle_bands_are_kept(self):
        # The gap is the finding. Dropping empty bands would hide it.
        bins = build_bins(rows(0, 21), bin_days=7)
        self.assertEqual([b["count"] for b in bins], [1, 0, 0, 1])

    def test_one_day_bands_are_labelled_with_a_bare_number(self):
        # "3-3" reads as a mistake; a single-day band is a point on the axis.
        bins = build_bins(rows(0, 1, 2), bin_days=1)
        self.assertEqual([b["label"] for b in bins], ["0", "1", "2"])

    def test_overflow_bucket_appears_only_when_data_runs_past_the_cap(self):
        within = build_bins(rows(0, 13), bin_days=1, max_bars=14)
        self.assertEqual(len(within), 14, "no ticket is past day 13, so no bucket")
        self.assertEqual(within[-1]["label"], "13")

        beyond = build_bins(rows(0, 14), bin_days=1, max_bars=14)
        self.assertEqual(len(beyond), 15)
        self.assertEqual(beyond[-1]["label"], "14+")
        self.assertIsNone(beyond[-1]["high"], "the bucket is open-ended")

    def test_everything_past_the_cap_lands_in_one_bucket(self):
        bins = build_bins(rows(20, 400, 9999), bin_days=1, max_bars=14)
        self.assertEqual(bins[-1]["count"], 3)
        self.assertEqual(bins[-1]["issues"], ["#100", "#101", "#102"])

    def test_a_ticket_exactly_on_a_boundary_starts_the_next_band(self):
        # Bands are [low, high], so day 7 with 7-day bands belongs to the second.
        bins = build_bins(rows(7), bin_days=7)
        self.assertEqual(bins[0]["count"], 0)
        self.assertEqual(bins[1]["count"], 1)

    def test_every_ticket_is_counted_exactly_once(self):
        ages = [0, 1, 1, 6, 7, 13, 14, 30, 200]
        for bin_days, max_bars in ((1, 14), (7, None), (3, 4)):
            with self.subTest(bin_days=bin_days, max_bars=max_bars):
                bins = build_bins(rows(*ages), bin_days, max_bars)
                self.assertEqual(sum(b["count"] for b in bins), len(ages))
                self.assertEqual(sum(len(b["issues"]) for b in bins), len(ages))


if __name__ == "__main__":
    unittest.main()
