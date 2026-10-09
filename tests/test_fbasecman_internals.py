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


class DeploymentInvalidationTest(unittest.TestCase):
    """After re-publishing or rebuilding a deployment, the stale fixture
    context bound to the old cluster identity must be removed."""

    def _provider_and_context(self, tmp):
        from platform_app.config import Settings
        from products.fbasecman.provider import PROVIDER

        settings = Settings(
            data_dir=tmp / "data",
            pgcluster_root=tmp / "pgcluster",
            license_key_dir=tmp / "keys",
            license_vendor="测试",
        )
        environment = {"id": "cman-x", "product_id": "fbasecman"}
        context = (
            settings.profile_dir("cman-x") / "fixture"
            / "test_context.yaml"
        )
        context.parent.mkdir(parents=True, exist_ok=True)
        context.write_text("stale")
        return PROVIDER, settings, environment, context

    def test_rebuilding_actions_invalidate_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            for action in (
                "deployment.create",
                "deployment.clean",
                "deployment.rejoin",
                "deployment.restore",
            ):
                provider, settings, environment, context = self._provider_and_context(
                    Path(tmp)
                )
                provider.after_command(None, settings, environment, "t", action, True)
                self.assertFalse(context.exists(), action)

    def test_failed_rebuild_still_invalidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider, settings, environment, context = self._provider_and_context(
                Path(tmp)
            )
            provider.after_command(
                None, settings, environment, "t", "deployment.create", False
            )
            self.assertFalse(context.exists())

    def test_non_rebuilding_actions_keep_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            for action in (
                "deployment.start",
                "deployment.stop",
                "deployment.failover",
                "deployment.switchover",
                "deployment.reset",
            ):
                provider, settings, environment, context = self._provider_and_context(
                    Path(tmp)
                )
                provider.after_command(None, settings, environment, "t", action, True)
                self.assertTrue(context.exists(), action)


def test_native_guc_suite_and_failed_rerun_do_not_require_legacy_fixture():
    from products.fbasecman.provider import native_regression_target
    native = {'guc.one': object(), 'guc.two': object()}
    targets = {'guc.one', 'guc.two', 'global_cache.runtime'}
    assert native_regression_target('guc', native, targets)
    assert native_regression_target('guc.failed', native, targets)
    assert native_regression_target('guc.one', native, targets)
    assert not native_regression_target('global_cache', native, targets)
    assert not native_regression_target('missing', native, targets)
