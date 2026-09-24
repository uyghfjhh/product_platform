import unittest
from types import SimpleNamespace

from suites.global_cache.errors import GlobalCacheFailure
from suites.global_cache.manifest import GLOBAL_CACHE_CASES
from suites.global_cache.suite import _case_phase, _collect_phase_checks


class CompositeCaseTest(unittest.TestCase):
    def test_phase_case_restores_parent_identity_and_overrides_phase_data(self):
        parent = next(
            case for case in GLOBAL_CACHE_CASES if case.name == "reuse_single_and_cross_client"
        )
        runtime = SimpleNamespace(case=parent)

        with _case_phase(runtime, "basic_reuse", sql={"tag": "child"}):
            self.assertEqual("basic_reuse", runtime.case.name)
            self.assertEqual("child", runtime.case.sql["tag"])
            self.assertIsNot(parent, runtime.case)

        self.assertIs(parent, runtime.case)
        self.assertEqual("reuse_single_and_cross_client", runtime.case.name)

    def test_phase_checks_are_prefixed_and_accumulated(self):
        runtime = SimpleNamespace(summary={}, step_records=[])
        collected = []

        def action():
            runtime.step_records.append({"title": "执行 JDBC driver"})
            runtime.summary["verification_checks"] = [
                {
                    "title": "entry 被复用",
                    "expected": "hit 增加",
                    "actual": "hits=1",
                    "result": "PASS",
                }
            ]

        _collect_phase_checks(runtime, "跨客户端复用", action, collected)

        self.assertEqual(1, len(collected))
        self.assertEqual("跨客户端复用: entry 被复用", collected[0]["title"])
        self.assertEqual("跨客户端复用: 执行 JDBC driver", runtime.step_records[0]["title"])
        self.assertNotIn("verification_checks", runtime.summary)

    def test_unknown_composite_phase_is_rejected(self):
        runtime = SimpleNamespace(case=SimpleNamespace(name="parent"))

        with self.assertRaisesRegex(GlobalCacheFailure, "unknown composite phase"):
            with _case_phase(runtime, "hidden_case"):
                pass


if __name__ == "__main__":
    unittest.main()
