import unittest
from unittest.mock import patch

from suites.ha_commands.runtime import HaCommandRuntime, _port_pair
from suites.ha_commands.manifest import HA_COMMAND_CASES, validate_manifest
from suites.ha_commands.suite import EXECUTORS


class SemanticConfigTest(unittest.TestCase):
    def test_inline_comment_is_not_part_of_field_value(self):
        objects, _ = HaCommandRuntime._semantic_objects(
            'group "g" {\n    write_cluster "c1"  # keep formatting\n}\n')
        self.assertEqual(objects[("group", "g")]["write_cluster"], '"c1"')

    def test_hash_inside_quoted_value_is_preserved(self):
        objects, _ = HaCommandRuntime._semantic_objects(
            'group "g" {\n    storage_db "db#tenant" # comment\n}\n')
        self.assertEqual(objects[("group", "g")]["storage_db"], '"db#tenant"')

    def test_multiple_fields_on_one_line_are_parsed(self):
        objects, _ = HaCommandRuntime._semantic_objects(
            'datasources "d" { host "127.0.0.1" port 5432 weight 10 status "active" }\n')
        self.assertEqual(objects[("datasources", "d")], {
            "host": '"127.0.0.1"', "port": "5432", "weight": "10",
            "status": '"active"',
        })

    def test_tabs_and_alignment_spaces_are_ignored(self):
        objects, _ = HaCommandRuntime._semantic_objects(
            'group "g" {\n\twrite_cluster    "c1" # comment\n'
            '  promoted_cluster\t"c2"\n}\n')
        self.assertEqual(objects[("group", "g")]["write_cluster"], '"c1"')
        self.assertEqual(objects[("group", "g")]["promoted_cluster"], '"c2"')


class MonitorOutputTest(unittest.TestCase):
    def test_expanded_rows_are_indexed_by_node_name(self):
        output = (
            "-[ RECORD 1 ]------+---------\n"
            "node_name          | pg_1\n"
            "probe_state        | READY\n"
            "connect_status     | ONLINE\n"
            "-[ RECORD 2 ]------+---------\n"
            "node_name          | pg_3\n"
            "probe_state        | PENDING\n"
            "connect_status     | UNKNOWN\n"
        )
        rows = HaCommandRuntime._expanded_rows(output, "node_name")
        self.assertEqual(rows["pg_1"]["probe_state"], "READY")
        self.assertEqual(rows["pg_3"]["connect_status"], "UNKNOWN")


class ManifestTest(unittest.TestCase):
    def test_all_manifest_executors_are_registered(self):
        self.assertTrue(validate_manifest())
        self.assertEqual(
            set(case.executor for case in HA_COMMAND_CASES) - set(EXECUTORS), set())

    def test_progress_closure_cases_are_in_default_gate(self):
        names = {case.name for case in HA_COMMAND_CASES if case.enabled}
        self.assertTrue({
            "ha_command_role_change_route_matrix",
            "reload_restore_failure",
        }.issubset(names))


class PortAllocationTest(unittest.TestCase):
    @patch("suites.ha_commands.runtime._non_ephemeral_port_range", return_value=(2000, 8999))
    @patch("suites.ha_commands.runtime._port_free", return_value=True)
    def test_port_pair_stays_outside_ephemeral_range(self, _free, _port_range):
        listen, read = _port_pair(1)
        self.assertGreaterEqual(listen, 2000)
        self.assertLessEqual(listen + 2, 8999)
        self.assertEqual(read, listen + 1)


if __name__ == "__main__":
    unittest.main()
