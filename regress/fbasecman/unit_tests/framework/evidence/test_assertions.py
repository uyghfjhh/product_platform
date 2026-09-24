import unittest

from framework.evidence.assertions import (
    matching_rows, missing_markers, pipe_rows, require_markers, stats_delta,
)


class AssertionHelpersTest(unittest.TestCase):
    def test_reports_all_missing_markers(self):
        self.assertEqual(["two", "three"], missing_markers("one", ["one", "two", "three"]))
        with self.assertRaisesRegex(AssertionError, "demo missing markers: two, three"):
            require_markers("one", ["one", "two", "three"], "demo")

    def test_matches_rows_by_column_without_case_sensitivity(self):
        rows = [["entry1", "RESET ALL", "GUC_RESET_ALL", "0", "0"],
                ["entry2", "select 1", "NORMAL", "0", "1"]]
        matched = matching_rows(rows, 1, equals="reset all", minimum_columns=5)
        self.assertEqual(["entry1|RESET ALL|GUC_RESET_ALL|0|0"], pipe_rows(matched))

    def test_requires_a_matching_rule(self):
        with self.assertRaisesRegex(ValueError, "requires contains or equals"):
            matching_rows([], 1)

    def test_computes_numeric_delta_and_preserves_non_numeric_values(self):
        self.assertEqual(
            {"hits": 2, "mode": {"before": "old", "after": "new"}},
            stats_delta({"hits": "1", "mode": "old"}, {"hits": "3", "mode": "new"}),
        )
