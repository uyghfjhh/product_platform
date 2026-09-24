import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from framework.execution.shell import (
    LoggedShellRunner, ShellCommandError, ShellTimeoutError, quote_arguments,
)


class LoggedShellRunnerTest(unittest.TestCase):
    def test_quotes_argument_list(self):
        self.assertEqual("plain 'two words'", quote_arguments(["plain", "two words"]))

    def test_captures_separate_streams_and_writes_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            result = LoggedShellRunner(log_dir, verbose=False).run(
                "printf stdout; printf stderr >&2", "command.log"
            )
            log = (log_dir / "command.log").read_text(encoding="utf-8")

        self.assertEqual("stdout", result.stdout)
        self.assertEqual("stderr", result.stderr)
        self.assertIn("=== STDOUT ===\nstdout", log)
        self.assertIn("=== STDERR ===\nstderr", log)
        self.assertIn("=== RETURN CODE: 0 ===", log)

    def test_raises_with_result_after_persisting_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            with self.assertRaises(ShellCommandError) as raised:
                LoggedShellRunner(log_dir, verbose=False).run(
                    "printf failed >&2; exit 7", "failure.log"
                )
            log = (log_dir / "failure.log").read_text(encoding="utf-8")

        self.assertEqual(7, raised.exception.result.returncode)
        self.assertIn("failed", raised.exception.result.stderr)
        self.assertIn("=== RETURN CODE: 7 ===", log)

    def test_timeout_terminates_the_process_group_and_persists_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            runner = LoggedShellRunner(
                log_dir, verbose=False, default_timeout=0.05,
                heartbeat_interval=0.01,
            )
            with self.assertRaises(ShellTimeoutError) as raised:
                runner.run(
                    [sys.executable, "-c", "import time; time.sleep(10)"],
                    "timeout.log",
                )
            log = (log_dir / "timeout.log").read_text(encoding="utf-8")

        self.assertTrue(raised.exception.result.timed_out)
        self.assertIn("=== TIMED OUT: yes ===", log)

    def test_remote_execution_uses_hardened_ssh_and_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = LoggedShellRunner(Path(directory), verbose=False)
            with patch.object(runner, "run", return_value="result") as mocked:
                result = runner.run_remote("postgres", "db", "printf ok", "remote.log")

        command = mocked.call_args[0][0]
        self.assertEqual("result", result)
        self.assertIn("BatchMode=yes", command)
        self.assertIn("ConnectTimeout=10", command)
        self.assertEqual("printf ok", mocked.call_args[1]["input_text"])
