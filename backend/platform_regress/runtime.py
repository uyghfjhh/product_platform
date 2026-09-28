"""产品无关的用例运行时基类。

``CaseRuntime`` 承担每个回归用例的公共生命周期：

- 运行目录 ``output/runs/<suite>/<case>`` 的重建与 workdir/logs 布局；
- ``StepJournal`` 崩溃安全的步骤落盘 + ``record_step``/``evidence_step`` 记录；
- ``run_command`` 命令执行包装（自动记账 + 非零失败）；
- ``write_report`` 报告骨架：步骤时间线合并、FAIL 诊断钩子、
  ``report.txt`` 渲染与 ``summary.json`` 结构化摘要；
- ``__enter__``/``__exit__`` 套件互斥锁（``lock_name``）。

产品相关部分（进程生命周期、配置模板、协议断言）由产品层子类提供，
例如 ``products.fbasecman.case_runtime.FbasecmanCaseRuntime``。
"""

import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from platform_regress.evidence import EvidenceStep, StepJournal
from platform_regress.execution.command import run_logged_command
from platform_regress.execution.locking import ExclusiveFileLock
from platform_regress.persistence.atomic import atomic_write_text, write_json
from platform_regress.reporting import ReportCheck, ReportDocument, ReportStep, render_report


class CaseRuntimeFailure(RuntimeError):
    """用例执行失败的统一异常类型；各 suite 可子类化保留自己的名字。"""


@dataclass(frozen=True)
class EnvironmentRef:
    """A pgcluster-managed environment reference supplied by the platform."""

    id: str
    product_id: str
    deployment_config: Path
    deployment_target: str
    host: str
    port: int
    database_name: str = "postgres"
    database_user: str = "postgres"


@dataclass(frozen=True)
class RegressionContext:
    """Stable product SDK input; no product framework imports are required."""

    environment: EnvironmentRef
    output_dir: Path
    profile_id: str
    cancellation: object | None = None


class CaseRuntime(object):
    """一个回归用例的运行上下文基类。

    子类扩展点：
    - ``failure_class``：失败异常类型（默认 CaseRuntimeFailure）
    - ``lock_name``：互斥锁文件名；同一 lock_name 的用例串行执行
    - ``stop()``：资源清理钩子
    - ``report_config_lines()`` / ``report_document_kwargs()`` /
      ``summary_extra()`` / ``collect_failure_steps()``：报告与摘要钩子
    """

    failure_class = CaseRuntimeFailure
    lock_name = None

    def __init__(self, root, case, platform_context: RegressionContext | None = None):
        self.root = Path(root)
        self.case = case
        self.platform_context = platform_context
        self.env = None
        self.context = {}
        if platform_context is not None:
            self.context = {
                "environment_id": platform_context.environment.id,
                "product_id": platform_context.environment.product_id,
                "deployment_target": platform_context.environment.deployment_target,
                "host": platform_context.environment.host,
                "port": platform_context.environment.port,
                "database_name": platform_context.environment.database_name,
                "database_user": platform_context.environment.database_user,
            }
        else:
            # 旧框架兼容路径：产品包自带的 legacy 用例直接实例化本类时，
            # 按原 CaseRuntime 语义加载回归配置和 test_context。
            import yaml
            from framework.configuration import load_regression_config
            self.env = load_regression_config(self.root)
            if not self.env.test_context_file.exists():
                raise self.failure_class(
                    "missing %s, run env setup first" % self.env.test_context_file)
            self.context = yaml.safe_load(
                self.env.test_context_file.read_text(encoding="utf-8")) or {}
        self.suite_id = (getattr(case, "suite_name", None)
                         or getattr(case, "suite_id", None) or "suite")
        # 每次用例运行重建目录，保证报告工件不混入上一次结果
        output_root = (platform_context.output_dir if platform_context is not None
                       else self.env.output_dir)
        self.run_root = output_root / "runs" / self.suite_id / case.name
        if self.run_root.exists():
            shutil.rmtree(str(self.run_root))
        self.workdir = self.run_root / "workdir"
        self.logs_dir = self.run_root / "logs"
        self.workdir.mkdir(parents=True)
        self.logs_dir.mkdir(parents=True)
        self.started_at = datetime.now()
        self.finished_at = None
        self._step_order = 0
        self.steps = []
        self.step_journal = StepJournal(self.run_root / "steps.json", case.target)
        self._lock = None

    # ------------------------------------------------------------------
    # 事件与步骤记录
    # ------------------------------------------------------------------

    def trace(self, message):
        """向 events.log 追加一条带时间戳的事件，用于事后还原执行过程。"""
        with (self.run_root / "events.log").open("a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (datetime.now().strftime("%H:%M:%S"), message))

    def _next_order(self):
        self._step_order += 1
        return self._step_order

    def record_step(self, title, command=None, expected=None, actual=None,
                    result=None, details=None):
        """记录一个平面步骤（命令+预期+实际+结论），并即时刷新 RUNNING 报告。"""
        self.steps.append({
            "title": title, "command": command, "expected": expected,
            "actual": actual, "result": result, "details": details or [],
            "order": self._next_order(),
        })
        self.write_report("RUNNING")

    def evidence_step(self, title, expected=None):
        """返回一个 EvidenceStep 上下文管理器，步骤执行过程持续落 journal。"""
        order = self._next_order()
        return EvidenceStep(
            title, self.step_journal, expected=expected,
            metadata={"order": order, "console": True},
            on_change=lambda: self.write_report("RUNNING"),
        )

    def check(self, title, expected, actual, passed):
        """断言并记账：不通过时先记 FAIL 步骤再抛异常终止用例。"""
        self.record_step(title, "", expected, actual,
                         "PASS" if passed else "FAIL")
        if not passed:
            raise self.failure_class("%s: expected %s, actual %s" % (title, expected, actual))

    def _context_value(self, key, default=""):
        """从环境上下文取字符串值；非字符串（如嵌套 dict）回落默认值。"""
        value = self.context.get(key, default)
        return value if isinstance(value, str) else default

    # ------------------------------------------------------------------
    # 命令执行
    # ------------------------------------------------------------------

    def run_command(self, command, logfile, cwd=None, env=None, echo=False,
                    check=True, step_title=None, record=True):
        """执行命令并落日志；record=True 时自动记录为一个报告步骤。"""
        result = run_logged_command(command, logfile, cwd=cwd or self.root,
                                    env=env, echo=echo)
        if record:
            self.record_step(step_title or "执行命令", result.command,
                             "命令执行完成", result.output,
                             "PASS" if result.returncode == 0 else "FAIL")
        if check and result.returncode != 0:
            raise self.failure_class("command failed rc=%s: %s" % (result.returncode, result.command))
        return result.returncode, result.output

    def asserted_command(self, command, title, expected, judge, *,
                         retry_timeout=0, interval=0.2, log_stem="command",
                         failure=None):
        """Run ``command`` inside an evidence step, polling until it matches.

        ``judge(result, output, attempt, elapsed) -> (passed, actual)`` is
        evaluated once per attempt; retries stop at ``retry_timeout`` seconds.
        Log files land in ``logs/<log_stem>_<step_order>_<attempt>.log``.
        ``failure(title, actual, result) -> message`` customises the raised
        error text; the default is ``"<title>: <actual>"``.  Returns
        ``(output, result)`` of the final attempt.
        """
        with self.evidence_step(title, expected=expected) as step:
            deadline = time.time() + max(0, retry_timeout)
            started = time.time()
            attempt = 0
            passed = False
            actual = ""
            while True:
                attempt += 1
                result = run_logged_command(
                    command,
                    self.logs_dir / ("%s_%02d_%02d.log" % (
                        log_stem, self._step_order, attempt)),
                    cwd=self.workdir)
                output = result.output.rstrip() or "<empty>"
                passed, actual = judge(
                    result, output, attempt, time.time() - started)
                if passed or time.time() >= deadline:
                    break
                time.sleep(interval)
            step.actual_execution("$ %s" % result.command, output)
            step.assess(expected, actual, passed)
        if not passed:
            message = (failure(title, actual, result) if failure is not None
                       else "%s: %s" % (title, actual))
            raise self.failure_class(message)
        return output, result

    def file_diff(self, before, after, log_name="config_diff.log"):
        """Run ``diff -u`` between two files and return the logged result."""
        return run_logged_command(
            ["diff", "-u", str(before), str(after)],
            self.logs_dir / log_name, cwd=self.workdir)

    # ------------------------------------------------------------------
    # 报告渲染
    # ------------------------------------------------------------------

    def _timeline_steps(self):
        """把 record_step 平面步骤与 journal 证据步骤按 order 合并成 ReportStep 列表。"""
        timeline = []
        for item in self.steps:
            checks = []
            for check in item.get("checks", []):
                if isinstance(check, ReportCheck):
                    checks.append(check)
                else:
                    checks.append(ReportCheck(*check))
            timeline.append((item["order"], ReportStep(
                item["title"], details=item["details"],
                execution=([{"label": "实际执行", "text": item["command"]}] if item["command"] else []),
                key_expected=item["expected"], actual=item["actual"], result=item["result"],
                checks=checks,
            )))
        for item in self.step_journal.steps:
            timeline.append((item.get("order", 0), ReportStep(
                item["title"], execution=item.get("execution", []),
                intermediate=item.get("intermediate", []), evidence=item.get("evidence", []),
                key_expected=item.get("expected"), actual=item.get("actual"),
                result=item.get("result"),
            )))
        return [item for _, item in sorted(timeline, key=lambda value: value[0])]

    def report_config_lines(self):
        """报告"关键配置"区块的行；子类覆盖以补充产品特定配置佐证。"""
        return []

    def report_document_kwargs(self, status, reason):
        """构造 ReportDocument 的额外字段；返回 ``steps`` 可完全接管步骤列表。"""
        return {
            "config_lines": self.report_config_lines(),
            "pass_reason": reason if status == "PASS" else None,
            "failure_reason": reason if status == "FAIL" else None,
        }

    def collect_failure_steps(self, status):
        """FAIL 时追加的诊断步骤（如崩溃现场）；默认无。"""
        return []

    def summary_extra(self):
        """summary.json 的附加字段；子类按需补充。"""
        return {}

    def write_report(self, status, reason=None):
        """渲染 report.txt 并写 summary.json。

        步骤列表默认 = 时间线合并 + 失败诊断钩子；子类在
        ``report_document_kwargs`` 里返回 ``steps`` 可整体替换。
        """
        kwargs = self.report_document_kwargs(status, reason)
        if "steps" in kwargs:
            items = kwargs.pop("steps")
        else:
            items = self._timeline_steps()
            items.extend(self.collect_failure_steps(status))
        document = ReportDocument(
            self.case.target, status,
            self.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            (self.finished_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
            self.case.summary,
            steps=items,
            **kwargs
        )
        atomic_write_text(self.run_root / "report.txt", render_report(document))
        self.write_summary(status, reason)

    def write_summary(self, status, reason):
        """写结构化 summary.json，供 JUnit/HTML 报告与失败重跑判定消费。"""
        payload = {
            "target": self.case.target, "status": status, "reason": reason,
            "source_sections": getattr(self.case, "source_sections", []),
        }
        payload.update(self.summary_extra())
        write_json(self.run_root / "summary.json", payload)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def stop(self):
        """清理钩子；持有资源的子类在此释放（如停止产品进程）。"""

    def finish(self, status, reason=None):
        """结束用例：写终态报告并清理资源。"""
        self.finished_at = datetime.now()
        self.write_report(status, reason)
        self.stop()

    def __enter__(self):
        # lock_name 相同的用例共用一把文件锁，串行执行避免争抢共享环境
        if self.lock_name:
            self._lock = ExclusiveFileLock(
                self.root / "output" / ("%s.lock" % self.lock_name),
                "%s suite" % self.suite_id)
            self._lock.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        if self._lock is not None:
            self._lock.__exit__(exc_type, exc_value, traceback)
            self._lock = None
        return False
