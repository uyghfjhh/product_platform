import unittest

from suites.global_cache.waits import (
    wait_target_entries_released,
    wait_target_entries_unref,
    wait_target_entry_absent,
)


class FakeRuntime(object):
    def __init__(self, global_rows):
        self.global_rows = global_rows
        self.summary = {}

    def console_query(self, sql, stem, record=False):
        if "STATS" in sql:
            return [
                ["total_entries", "capacity", "referenced_count", "unreferenced_count",
                 "parse_valid_count", "parse_failed_count", "forced_write_count",
                 "bypass_count", "heartbeat_bypass_count", "guc_bypass_count",
                 "hits", "misses", "evictions"],
                ["1", "10", "0", "1", "0", "0", "0", "0", "0", "0", "0", "1", "0"],
            ]
        return [["global_name", "description", "sql_class", "has_bypass_response", "ref_count"]] + self.global_rows


class GlobalCacheWaitTest(unittest.TestCase):
    def test_returns_matching_unref_entries(self):
        runtime = FakeRuntime([["g1", "select 1 /* target */", "NORMAL", "0", "0"]])
        state, matched = wait_target_entries_unref(runtime, "target", 1, 0.1, interval=0)
        self.assertEqual("0", matched[0][4])
        self.assertEqual("1", state["stats"]["unreferenced_entries"])

    def test_returns_when_target_is_absent(self):
        runtime = FakeRuntime([["g1", "select 1", "NORMAL", "0", "0"]])
        state = wait_target_entry_absent(runtime, "missing", 0.1, interval=0)
        self.assertEqual(1, len(state["global"]))

    def test_released_accepts_evicted_or_zero_ref_targets(self):
        runtime = FakeRuntime([])
        state, matched = wait_target_entries_released(runtime, "target", 0.1, interval=0)
        self.assertEqual([], matched)
        self.assertEqual([], state["global"])
