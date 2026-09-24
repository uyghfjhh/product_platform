import sys
import tempfile
import unittest
from pathlib import Path

from framework.execution.phased_process import PhaseAction, PhasedProcess, observe_phases


class PhasedProcessTest(unittest.TestCase):
    def test_driver_can_pause_observe_and_resume_twice(self):
        script = (
            "import sys\n"
            "print('PHASE=ONE', flush=True)\n"
            "assert sys.stdin.readline().strip() == 'next'\n"
            "print('PHASE=TWO', flush=True)\n"
            "assert sys.stdin.readline().strip() == 'finish'\n"
            "print('DONE', flush=True)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            process = PhasedProcess(
                [sys.executable, "-u", "-c", script], Path(tmp) / "driver.log"
            )
            self.assertEqual(process.wait_for("PHASE=ONE"), "PHASE=ONE")
            process.resume("next")
            self.assertEqual(process.wait_for("PHASE=TWO"), "PHASE=TWO")
            process.resume("finish")
            rc, output = process.finish()

        self.assertEqual(rc, 0)
        self.assertIn("DONE", output)

    def test_observer_runs_while_each_driver_phase_is_live(self):
        script = (
            "import sys\n"
            "print('PHASE=ONE', flush=True)\n"
            "assert sys.stdin.readline().strip() == 'next_one'\n"
            "print('PHASE=TWO', flush=True)\n"
            "assert sys.stdin.readline().strip() == 'next_two'\n"
            "print('DONE', flush=True)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            process = PhasedProcess(
                [sys.executable, "-u", "-c", script], Path(tmp) / "driver.log"
            )
            seen = []
            observations, rc, output = observe_phases(
                process,
                [
                    PhaseAction("one", "PHASE=ONE", "next_one"),
                    PhaseAction("two", "PHASE=TWO", "next_two"),
                ],
                lambda name, marker: seen.append((name, marker)) or "observed-" + name,
            )

        self.assertEqual(rc, 0)
        self.assertEqual(observations, {"one": "observed-one", "two": "observed-two"})
        self.assertEqual([item[0] for item in seen], ["one", "two"])
        self.assertIn("DONE", output)
