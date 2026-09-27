import tempfile
import unittest
from pathlib import Path

from products.fbasecman.process import FbasecmanProcess, FbasecmanProcessError


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
