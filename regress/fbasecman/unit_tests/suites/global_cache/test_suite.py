import tempfile
import unittest
from pathlib import Path

from suites.global_cache import suite


ROOT = Path(__file__).resolve().parents[3]


class GlobalCacheSuiteTest(unittest.TestCase):
    def test_discard_all_known_product_issue_is_attributed(self):
        case = next(item for item in suite.case_items()
                    if item.name == "discard_all_clears_backend_cache")
        self.assertEqual("F-001", case.issue_id)

    def test_backend_global_split_case_and_asset_are_registered(self):
        case = next(item for item in suite.case_items() if item.name == "backend_global_split_eviction")
        self.assertEqual(4, case.fbasecman["global_prepared_statements_limit"])
        self.assertEqual(2, case.fbasecman["backend_prepared_statements_limit"])
        source = suite._libpq_driver_source(ROOT, case)
        self.assertEqual("GC_backend_global_split_eviction.c", source.name)
        self.assertTrue(source.exists())

    def test_append_driver_log_preserves_previous_output(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "libpq.log"
            suite._append_driver_log(log_path, "seed_commit_ok=true\n")
            suite._append_driver_log(log_path, "trigger_done=true\n")
            output = log_path.read_text(encoding="utf-8")
        self.assertEqual("seed_commit_ok=true\ntrigger_done=true\n", output)
