import unittest

from suites.global_cache.drivers import LIBPQ_ASSETS
from suites.global_cache.manifest import GLOBAL_CACHE_CASES, formal_case_items
from suites.global_cache.suite import (
    COMPOSITE_PHASE_NAMES,
    SPECIAL_CASE_EXECUTORS,
    STARTED_CASE_EXECUTORS,
)


class GlobalCacheManifestTest(unittest.TestCase):
    def test_manifest_contains_only_the_consolidated_formal_gate(self):
        formal_names = {case.name for case in formal_case_items()}
        all_names = {case.name for case in GLOBAL_CACHE_CASES}

        self.assertEqual(all_names, formal_names)
        self.assertEqual(18, len(formal_names))
        self.assertEqual(
            formal_names,
            set(STARTED_CASE_EXECUTORS) | set(SPECIAL_CASE_EXECUTORS),
        )
        self.assertFalse(formal_names & COMPOSITE_PHASE_NAMES)
        self.assertTrue(set(LIBPQ_ASSETS).issubset(formal_names | COMPOSITE_PHASE_NAMES))
