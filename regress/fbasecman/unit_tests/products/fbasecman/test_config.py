import unittest

from products.fbasecman.config import (
    apply_datasource_runtime,
    extract_config_lines,
    set_config_block_line,
)


class FbasecmanConfigTest(unittest.TestCase):
    def test_extracts_enabled_product_configuration(self):
        text = "# heartbeat_request old\nserver_lifetime 10\nheartbeat_request select 1\n"
        self.assertEqual(
            "server_lifetime 10\nheartbeat_request select 1",
            extract_config_lines(text, ["server_lifetime", "heartbeat_request"]),
        )

    def test_updates_datasource_block_for_selected_topology(self):
        rendered = "".join(
            'datasources "%s" {\n    host "old"\n    port 1\n}\n' % name
            for name in ("pg_220", "pg_230", "pg_240")
        )
        config = {
            "database": {
                "mmr_host": "mmr", "rep_host": "rep",
                "ports": {
                    "mmr1": 11, "mmr2": 12, "mmr1_standby1": 13,
                    "rep_primary": 21, "rep_standby1": 22, "rep_standby2": 23,
                },
            }
        }
        updated = apply_datasource_runtime(rendered, "mmr", config)
        self.assertIn('host "mmr"', updated)
        self.assertIn("port 11", updated)
        self.assertIn("port 12", updated)
        self.assertIn("port 13", updated)

    def test_appends_missing_block_key(self):
        updated = set_config_block_line(
            'user "demo" {\n}\n', "user", "demo", "server_lifetime", "server_lifetime 10"
        )
        self.assertIn("    server_lifetime 10", updated)
