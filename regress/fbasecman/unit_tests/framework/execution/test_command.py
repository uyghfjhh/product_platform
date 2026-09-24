import tempfile
import unittest
import sys
from pathlib import Path

from framework.execution.command import command_display, run_logged_command


class CommandRunnerTest(unittest.TestCase):
    def test_captures_merged_output_and_return_code(self):
        with tempfile.TemporaryDirectory() as directory:
            logfile = Path(directory) / "command.log"
            result = run_logged_command(
                ["sh", "-c", "echo stdout; echo stderr >&2; exit 7"], logfile
            )

            self.assertEqual(7, result.returncode)
            self.assertIn("stdout", result.output)
            self.assertIn("stderr", result.output)
            self.assertEqual(result.output, logfile.read_text(encoding="utf-8"))

    def test_formats_list_and_string_commands(self):
        self.assertEqual("echo hello", command_display(["echo", "hello"]))
        self.assertEqual("echo hello", command_display("echo hello"))

    def test_formats_argv_as_a_copyable_shell_command(self):
        self.assertEqual(
            "psql -F '|' -c 'SELECT value FROM t WHERE id = 1;'",
            command_display([
                "psql", "-F", "|", "-c", "SELECT value FROM t WHERE id = 1;",
            ]),
        )

    def test_timeout_terminates_a_silent_command_and_records_the_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            logfile = Path(directory) / "timeout.log"
            result = run_logged_command(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                logfile, timeout=0.05,
            )

            self.assertTrue(result.timed_out)
            self.assertIn("command timed out", result.output)
            self.assertIn("command timed out", logfile.read_text(encoding="utf-8"))
