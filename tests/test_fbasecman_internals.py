"""Unit tests for products.fbasecman internal configuration, console, and process."""

import tempfile
import unittest
from pathlib import Path

from products.fbasecman.config import (
    apply_datasource_runtime,
    extract_config_lines,
    set_config_block_line,
)
from products.fbasecman.console import ConsoleQueryError, parse_pipe_rows
from products.fbasecman.process import FbasecmanProcess, FbasecmanProcessError


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


class ConsoleParserTest(unittest.TestCase):
    def test_parses_pipe_rows(self):
        self.assertEqual([["a", "b"], ["1", "2"]], parse_pipe_rows("a|b\n1|2\n"))

    def test_rejects_psql_error(self):
        with self.assertRaises(ConsoleQueryError):
            parse_pipe_rows("psql: error: connection failed\n")


class FbasecmanProcessTest(unittest.TestCase):
    def test_start_default_ready_timeout_allows_mmr_initialisation(self):
        self.assertEqual((30.0, False, True, None), FbasecmanProcess.start.__defaults__)

    def test_start_converts_command_failure_to_process_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = FbasecmanProcess(
                "/bin/fbasecman", "/opt/pg", 6432, 7777,
                root / "fbasecman.pid", root / "locks", root / "fbasecman.log",
                root / "logs", lambda *args, **kwargs: (1, "address already in use"),
                lambda message: None, lambda port: True,
            )
            with self.assertRaisesRegex(FbasecmanProcessError, "start failed rc=1"):
                process.start(root / "handover.conf")

    def test_wait_ready_retries_and_uses_console_psql(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            ready_results = [(1, "starting"), (0, "ready")]

            def execute(command, logfile, **kwargs):
                calls.append((command, logfile, kwargs))
                return ready_results.pop(0)

            process = FbasecmanProcess(
                "/bin/fbasecman", "/opt/pg", 6432, 7777,
                root / "fbasecman.pid", root / "locks", root / "fbasecman.log",
                root / "logs", execute, lambda message: None, lambda port: True,
                sleep=lambda seconds: None,
            )
            process.wait_ready(1)

        self.assertEqual(2, len(calls))
        self.assertEqual("/opt/pg/bin/psql", calls[0][0][0])
        self.assertIn("show global_prepared_statements_stats;", calls[0][0])

    def test_runtime_replacements_use_run_specific_paths_and_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = FbasecmanProcess(
                "/bin/fbasecman", "/opt/pg", 6432, 7778,
                root / "pid", root / "locks", root / "product.log", root / "logs",
                lambda *args, **kwargs: (0, ""), lambda message: None, lambda port: True,
            )
            rendered = "\n".join(new for old, new in process.config_replacements("debug1"))
        self.assertIn('ports "6432"', rendered)
        self.assertIn("promhttp_server_port 7778", rendered)
        self.assertIn('log_min_messages "debug1"', rendered)
