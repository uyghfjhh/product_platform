import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from framework.evidence import EvidenceStep, StepJournal


class EvidenceStepTest(unittest.TestCase):
    def test_persists_execution_intermediate_and_window_evidence(self):
        with TemporaryDirectory() as directory:
            journal = StepJournal(Path(directory) / "steps.json", "handover.example")
            opened = []

            def open_window():
                opened.append(True)
                return "mark"

            def collect_window(mark):
                self.assertEqual("mark", mark)
                return [("fbasecman 证据", "route=pg_230"), ("PG 证据", "statement: SELECT 1")]

            with EvidenceStep("查询", journal, open_window, collect_window,
                              expected="返回 1") as step:
                step.actual_execution("$ psql -c 'SELECT 1'", " ?column? \n----------\n        1")
                step.intermediate_state("$ psql -d console -c 'SHOW SERVERS;'", "pg_230 | active")
                step.expect("返回 1", "返回 1", True)

            saved = json.loads((Path(directory) / "steps.json").read_text(encoding="utf-8"))
        self.assertTrue(opened)
        self.assertEqual("COMPLETED", saved["steps"][0]["status"])
        self.assertEqual("PASS", saved["steps"][0]["result"])
        self.assertEqual(2, len(saved["steps"][0]["evidence"]))

    def test_exception_is_persisted_as_failed_step(self):
        with TemporaryDirectory() as directory:
            journal = StepJournal(Path(directory) / "steps.json", "global_cache.example")
            with self.assertRaises(RuntimeError):
                with EvidenceStep("失败步骤", journal) as step:
                    step.actual_execution("$ psql -c 'SELECT 1'", "ERROR")
                    raise RuntimeError("expected failure")
            saved = json.loads((Path(directory) / "steps.json").read_text(encoding="utf-8"))
        self.assertEqual("FAILED", saved["steps"][0]["status"])
        self.assertEqual("FAIL", saved["steps"][0]["result"])

    def test_change_callback_sees_running_and_completed_step(self):
        with TemporaryDirectory() as directory:
            journal = StepJournal(Path(directory) / "steps.json", "handover.example")
            observed = []

            def changed():
                observed.append(journal.steps[0]["status"])

            with EvidenceStep("即时落盘", journal, on_change=changed) as step:
                step.actual_execution("$ psql -c 'SELECT 1'", " ?column? \n----------\n        1")
                step.expect("返回 1", "返回 1", True)

        self.assertIn("RUNNING", observed)
        self.assertEqual("COMPLETED", observed[-1])
