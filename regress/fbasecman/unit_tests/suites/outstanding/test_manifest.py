import unittest

from suites.outstanding import suite
from suites.outstanding.manifest import OUTSTANDING_CASES, case_items


class OutstandingManifestTest(unittest.TestCase):
    def test_case_names_and_targets_are_unique(self):
        self.assertEqual(11, len(OUTSTANDING_CASES))
        self.assertEqual(
            len(OUTSTANDING_CASES),
            len(set(case.name for case in OUTSTANDING_CASES)),
        )
        self.assertTrue(all(case.target.startswith("outstanding.") for case in case_items()))

    def test_all_executors_are_registered(self):
        self.assertEqual(
            set(case.executor for case in OUTSTANDING_CASES),
            set(suite.EXECUTORS),
        )

    def test_all_cases_compare_two_cache_phases(self):
        for case in OUTSTANDING_CASES:
            self.assertIn(case.mode, suite.EXPECTED_COUNTS)
            self.assertEqual(2, len(suite.EXPECTED_COUNTS[case.mode]))
            self.assertTrue(case.notes)
