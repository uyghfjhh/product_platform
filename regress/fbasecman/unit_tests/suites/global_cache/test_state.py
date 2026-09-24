import unittest

from suites.global_cache.state import capture_global_cache_state, parse_stats


class GlobalCacheStateTest(unittest.TestCase):
    def test_parses_console_snapshot(self):
        responses = {
            "SHOW GLOBAL_PREPARED_STATEMENTS;": [["global_name"], ["__fbasecman_1"]],
            "SHOW GLOBAL_PREPARED_STATEMENTS_STATS;": [
                ["headers"], ["2", "10", "1", "1", "0", "0", "0", "0", "0", "0", "4", "2", "0"]
            ],
            "SHOW SERVER_PREP_STMTS;": [["type"], ["prepared"]],
        }
        state = capture_global_cache_state(
            lambda sql, stem: responses[sql], "after", include_server=True
        )
        self.assertEqual(state["global"], [["__fbasecman_1"]])
        self.assertEqual(state["stats"]["capacity"], "10")
        self.assertEqual(state["server"], [["prepared"]])

    def test_empty_stats_are_empty(self):
        self.assertEqual({}, parse_stats([]))

    def test_snapshot_can_suppress_report_step_recording(self):
        calls = []

        def query(sql, stem, record=True):
            calls.append((sql, record))
            if "STATS" in sql:
                return [["headers"], ["0", "0", "0", "0", "10", "0", "0", "0"]]
            return [["headers"]]

        capture_global_cache_state(query, "quiet", record=False)

        self.assertTrue(calls)
        self.assertTrue(all(record is False for _, record in calls))
