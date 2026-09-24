import tempfile
import unittest
from pathlib import Path
import sys

from suites.global_cache.case import GlobalCacheCase
from suites.global_cache.runtime import CaseRuntime, VerificationFailure
from framework.evidence import StepJournal
from framework.execution.phased_process import PhaseAction, PhasedProcess


class RuntimeVerificationTest(unittest.TestCase):
    def test_console_ready_default_allows_mmr_group_check(self):
        runtime = CaseRuntime.__new__(CaseRuntime)

        class Process(object):
            def __init__(self):
                self.timeout = None

            def wait_ready(self, timeout):
                self.timeout = timeout
                return "ready"

        runtime.product_process = Process()

        self.assertEqual("ready", runtime.wait_for_console_ready())
        self.assertEqual(30.0, runtime.product_process.timeout)

    def test_pass_keeps_report_and_action_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = CaseRuntime.__new__(CaseRuntime)
            runtime.run_root = Path(tmp)
            runtime.summary = {"status": "PASS"}
            for name in (
                "report.txt", "fbasecman.log", "manifest.json", "summary.json", "events.log", "steps.json"
            ):
                (runtime.run_root / name).write_text(name, encoding="utf-8")
            (runtime.run_root / "logs").mkdir()
            (runtime.run_root / "logs" / "jdbc.log").write_text("log", encoding="utf-8")
            (runtime.run_root / "workdir").mkdir()

            runtime.prune_artifacts()

            self.assertEqual(
            ["fbasecman.log", "logs", "report.txt", "steps.json", "summary.json"],
                sorted(path.name for path in runtime.run_root.iterdir()),
            )

    def test_failure_keeps_all_diagnostic_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = CaseRuntime.__new__(CaseRuntime)
            runtime.run_root = Path(tmp)
            runtime.summary = {"status": "FAIL"}
            for name in ("report.txt", "fbasecman.log", "summary.json"):
                (runtime.run_root / name).write_text(name, encoding="utf-8")

            runtime.prune_artifacts()

            self.assertEqual(
                ["fbasecman.log", "report.txt", "summary.json"],
                sorted(path.name for path in runtime.run_root.iterdir()),
            )

    def test_failed_verification_preserves_expected_and_actual(self):
        runtime = CaseRuntime.__new__(CaseRuntime)
        runtime.summary = {}
        runtime.step_records = []

        with self.assertRaises(VerificationFailure):
            runtime.verify(
                "cache entry remains", "one matching entry", "zero matching entries",
                False, phase="driver_paused",
            )

        failed = runtime.summary["failed_step"]
        self.assertEqual(failed["phase"], "driver_paused")
        self.assertEqual(failed["expected"], "one matching entry")
        self.assertEqual(failed["actual"], "zero matching entries")
        self.assertEqual(failed["result"], "FAIL")

    def test_failure_report_names_expected_and_actual(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = CaseRuntime.__new__(CaseRuntime)
            runtime.case = GlobalCacheCase(
                name="report_contract", summary="report contract test", batch=0,
                priority="p0", driver="jdbc", topology="mmr",
                rw_split_method="hint", pool_mode="transaction",
            )
            runtime.summary = {
                "status": "FAIL",
                "reason": "cache entry remains: expected=one; actual=zero",
                "failed_step": {
                    "title": "验证: cache entry remains", "phase": "driver_paused",
                    "expected": "one matching entry", "actual": "zero matching entries",
                    "result": "FAIL", "output": "<missing>",
                },
            }
            runtime.step_records = []
            runtime.report_file = Path(tmp) / "report.txt"
            runtime.write_report()
            report = runtime.report_file.read_text(encoding="utf-8")

        self.assertNotIn("phase: driver_paused", report)
        self.assertIn("预期: one matching entry", report)
        self.assertIn("实际: zero matching entries", report)
        self.assertIn("判定: FAIL", report)
        self.assertNotIn("执行中断: 验证: cache entry remains", report)

    def test_jdbc_phase_observation_persists_console_and_log_evidence(self):
        script = (
            "import sys\n"
            "print('READY=FIRST', flush=True)\n"
            "assert sys.stdin.readline().strip() == 'continue'\n"
            "print('DONE', flush=True)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Driver.java"
            source.write_text(
                "class Driver { void run(java.sql.Connection c) throws Exception {\n"
                "  java.sql.PreparedStatement ps = c.prepareStatement(\"SELECT value FROM t WHERE id = ?\");\n"
                "  ps.setInt(1, 10); ps.executeQuery(); c.commit();\n"
                "} }\n", encoding="utf-8",
            )
            runtime = CaseRuntime.__new__(CaseRuntime)
            runtime._step_order = 0
            runtime.step_journal = StepJournal(root / "steps.json", "global_cache.phase")
            runtime._last_evidence_windows = ("", "")
            runtime.write_live_step_report = lambda: None
            runtime.begin_fbasecman_log_window = lambda: "proxy-mark"
            runtime.begin_pg_log_window = lambda _stem: "pg-mark"
            runtime.collect_fbasecman_log_window = lambda _mark: (
                "routing to pg_220\nglobal prepared statement cache hit", []
            )
            runtime.collect_pg_log_window = lambda _mark, _stem: {
                "text": "LOG: statement: SELECT value FROM t WHERE id = $1", "lines": []
            }
            process = PhasedProcess([sys.executable, "-u", "-c", script], root / "driver.log")
            observations, rc, output = runtime.observe_jdbc_phases(
                process, source, "jdbc:postgresql://127.0.0.1:6432/postgres?prepareThreshold=1",
                [PhaseAction("first", "READY=FIRST", title="首次 PreparedStatement 后检查缓存")],
                lambda _phase, _marker: {
                    "command": "$ psql -d console -c 'SHOW GLOBAL_PREPARED_STATEMENTS;'",
                    "output": "__fbasecman_1 | SELECT value FROM t WHERE id = $1",
                },
                collect_logs=True,
            )

        self.assertEqual(0, rc)
        self.assertIn("DONE", output)
        self.assertIn("first", observations)
        saved = runtime.step_journal.steps[0]
        self.assertEqual("PASS", saved["result"])
        self.assertIn("PreparedStatement", saved["execution"][0]["text"])
        self.assertIn("SHOW GLOBAL_PREPARED_STATEMENTS", saved["intermediate"][0]["text"])
        self.assertEqual(2, len(saved["evidence"]))

    def test_jdbc_phase_observation_fails_on_business_observation(self):
        script = (
            "import sys\n"
            "print('PHASE=READY', flush=True)\n"
            "sys.stdin.readline()\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Driver.java"
            source.write_text("class Driver {}\n", encoding="utf-8")
            runtime = self._journal_runtime(root)
            process = runtime.start_jdbc_phase_process(
                [sys.executable, "-u", "-c", script], source,
                "jdbc:postgresql://127.0.0.1:6432/postgres", root / "driver.log",
                "执行阶段 JDBC 查询",
            )
            with self.assertRaisesRegex(Exception, "expected one cache row"):
                runtime.observe_jdbc_phases(
                    process, source, "jdbc:postgresql://127.0.0.1:6432/postgres",
                    [PhaseAction("ready", "PHASE=READY", title="观察缓存")],
                    lambda _phase, _marker: {
                        "expected": "one cache row",
                        "actual": "zero cache rows",
                        "passed": False,
                    },
                )

        phase = next(item for item in runtime.step_journal.steps if item.get("jdbc_phase"))
        self.assertEqual("FAIL", phase["result"])
        self.assertEqual("one cache row", phase["expected"])
        self.assertEqual("zero cache rows", phase["actual"])

    def test_journaled_jdbc_phase_process_persists_lifecycle_and_evidence(self):
        script = (
            "import sys\n"
            "print('phase_result=value-10', flush=True)\n"
            "print('PHASE=READY', flush=True)\n"
            "assert sys.stdin.readline().strip() == 'continue'\n"
            "print('driver_complete', flush=True)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Driver.java"
            source.write_text(
                "class Driver { void run(java.sql.Connection c) throws Exception {\n"
                "  java.sql.PreparedStatement ps = c.prepareStatement(\"SELECT value FROM t WHERE id = ?\");\n"
                "  ps.setInt(1, 10); ps.executeQuery(); c.commit();\n"
                "} }\n", encoding="utf-8",
            )
            runtime = self._journal_runtime(root)
            process = runtime.start_jdbc_phase_process(
                [sys.executable, "-u", "-c", script], source,
                "jdbc:postgresql://127.0.0.1:6432/postgres?prepareThreshold=1",
                root / "driver.log", "执行阶段 JDBC 查询",
                sql_operations=[{
                    "sql": "SELECT value FROM t WHERE id = ?",
                    "parameters": ["$1=10"],
                }],
            )

            self.assertEqual("RUNNING", runtime.step_journal.steps[0]["status"])
            process.wait_for("PHASE=READY")
            process.resume("continue")
            rc, output = process.finish()

        self.assertEqual(0, rc)
        self.assertIn("driver_complete", output)
        saved = runtime.step_journal.steps[0]
        self.assertEqual("COMPLETED", saved["status"])
        self.assertEqual("PASS", saved["result"])
        self.assertIn("PreparedStatement", saved["execution"][0]["text"])
        self.assertIn("$1=10", saved["execution"][0]["text"])
        self.assertIn("driver_complete", saved["execution"][0]["text"])
        self.assertEqual(0, len(saved["evidence"]))

    def test_journaled_jdbc_phase_process_marks_terminated_driver_failed(self):
        script = (
            "import sys\n"
            "print('PHASE=READY', flush=True)\n"
            "sys.stdin.readline()\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Driver.java"
            source.write_text("class Driver {}\n", encoding="utf-8")
            runtime = self._journal_runtime(root)
            process = runtime.start_jdbc_phase_process(
                [sys.executable, "-u", "-c", script], source,
                "jdbc:postgresql://127.0.0.1:6432/postgres", root / "driver.log",
                "执行阶段 JDBC 查询",
            )
            process.wait_for("PHASE=READY")
            process.terminate()

        saved = runtime.step_journal.steps[0]
        self.assertEqual("FAILED", saved["status"])
        self.assertEqual("FAIL", saved["result"])
        self.assertIn("被终止", saved["actual"])

    def test_journaled_jdbc_phase_process_marks_start_failure_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Driver.java"
            source.write_text("class Driver {}\n", encoding="utf-8")
            runtime = self._journal_runtime(root)
            with self.assertRaises(FileNotFoundError):
                runtime.start_jdbc_phase_process(
                    [str(root / "missing-jdbc-driver")], source,
                    "jdbc:postgresql://127.0.0.1:6432/postgres", root / "driver.log",
                    "执行阶段 JDBC 查询",
                )

        saved = runtime.step_journal.steps[0]
        self.assertEqual("FAILED", saved["status"])
        self.assertEqual("FAIL", saved["result"])
        self.assertIn("missing-jdbc-driver", saved["actual"])

    @staticmethod
    def _journal_runtime(root):
        runtime = CaseRuntime.__new__(CaseRuntime)
        runtime._step_order = 0
        runtime.step_journal = StepJournal(root / "steps.json", "global_cache.phase")
        runtime._last_evidence_windows = ("", "")
        runtime.write_live_step_report = lambda: None
        runtime.begin_fbasecman_log_window = lambda: "proxy-mark"
        runtime.begin_pg_log_window = lambda _stem: "pg-mark"
        runtime.collect_fbasecman_log_window = lambda _mark: (
            "routing to pg_220", []
        )
        runtime.collect_pg_log_window = lambda _mark, _stem: {
            "text": "LOG: statement: SELECT value FROM t WHERE id = $1", "lines": []
        }
        return runtime
