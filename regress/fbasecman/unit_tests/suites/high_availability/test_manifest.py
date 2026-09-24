import unittest
from pathlib import Path

from suites.high_availability import suite
from suites.high_availability.cluster_ops import NodeController
from suites.high_availability.console_parser import (
    ConsoleAssertionError,
    ConsoleSnapshot,
    parse_console_pipe_table,
)
from suites.high_availability.manifest import (
    HIGH_AVAILABILITY_CASES,
    case_items,
    find_case,
    validate_manifest,
)
from suites.high_availability.runtime import HighAvailabilityRuntime


class HighAvailabilityManifestTest(unittest.TestCase):
    def test_command_result_includes_return_code_and_output(self):
        rendered = HighAvailabilityRuntime.command_result("psql -c 'SHOW CLUSTERS'", 1, "ERROR: failed\n")
        self.assertIn("psql -c 'SHOW CLUSTERS'", rendered)
        self.assertIn("返回码: 1", rendered)
        self.assertIn("ERROR: failed", rendered)

    def test_node_command_result_marks_empty_output(self):
        rendered = NodeController._format_result("pg_basebackup", 0, "")
        self.assertIn("返回码: 0", rendered)
        self.assertIn("<无输出>", rendered)

    def test_ha_report_sources_do_not_hardcode_old_host_or_summary_commands(self):
        suite_root = Path(__file__).resolve().parents[3] / "suites/high_availability"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in suite_root.rglob("*.py")
        )
        self.assertNotIn("192.168.1.24", source)
        self.assertNotIn("start A1 + start B1 + REFRESH", source)

    def test_config_template_uses_millisecond_retry_period(self):
        template = (
            Path(__file__).resolve().parents[3]
            / "suites/high_availability/assets/config/fbasecman_ha.conf"
        ).read_text(encoding="utf-8")
        self.assertIn("monitor_retry_period_ms 1000", template)
        self.assertNotIn("monitor_retry_period 1", template)
        self.assertNotIn('storage "', template)

    def test_manifest_is_valid_and_cases_are_unique(self):
        self.assertTrue(validate_manifest())
        names = [case.name for case in HIGH_AVAILABILITY_CASES]
        self.assertEqual(10, len(names))
        self.assertEqual(len(names), len(set(names)))

    def test_every_case_has_a_registered_executor(self):
        for case in HIGH_AVAILABILITY_CASES:
            self.assertIn(case.executor, suite.EXECUTORS, case.target)
            self.assertTrue(callable(suite.EXECUTORS[case.executor]))
        self.assertEqual(
            sorted(case.executor for case in HIGH_AVAILABILITY_CASES),
            sorted(suite.EXECUTORS),
        )

    def test_source_sections_cover_chapter_4_cores(self):
        covered = {
            case.source_sections[0].rsplit(":", 1)[-1]
            for case in HIGH_AVAILABILITY_CASES
        }
        expected = {
            "CORE-13", "CORE-14", "CORE-15", "CORE-16", "CORE-17",
            "CORE-18", "CORE-19", "CORE-20", "CORE-21", "CORE-22",
        }
        self.assertEqual(expected, covered)

    def test_case_surface_matches_shared_runtime(self):
        for case in case_items():
            self.assertEqual("high_availability", case.suite_name)
            self.assertEqual("high_availability.%s" % case.name, case.target)
            self.assertTrue(case.summary)
            self.assertTrue(case.report_groups)

    def test_find_case(self):
        self.assertEqual(
            "core_17_mmr_write_center_failover",
            find_case("core_17_mmr_write_center_failover").name,
        )
        with self.assertRaises(KeyError):
            find_case("missing")

    def test_console_snapshot_parsing_and_assertions(self):
        raw_output = (
            " group_name | group_mode | candidate_node | effective_state | is_write_target \n"
            "------------+------------+----------------+-----------------+-----------------\n"
            " qa_rep     | rep        | test_mmr1      | active          | true\n"
            " qa_rep     | rep        | test_mmr1_s1   | active          | false\n"
            "(2 rows)\n"
        )
        snapshot = ConsoleSnapshot(raw_output)
        self.assertEqual(2, len(snapshot.records))
        self.assertEqual("test_mmr1", snapshot.records[0]["candidate_node"])
        self.assertEqual("true", snapshot.records[0]["is_write_target"])

        # Semantic field assertions
        snapshot.assert_field({"candidate_node": "test_mmr1"}, "is_write_target", "true")
        snapshot.assert_field({"candidate_node": "test_mmr1_s1"}, "effective_state", "active")
        snapshot.assert_candidate_present("test_mmr1_s1")
        snapshot.assert_candidate_absent("test_mmr2")

        with self.assertRaises(ConsoleAssertionError):
            snapshot.assert_field({"candidate_node": "test_mmr1"}, "is_write_target", "false")

        with self.assertRaises(ConsoleAssertionError):
            snapshot.assert_candidate_absent("test_mmr1")


if __name__ == "__main__":
    unittest.main()
