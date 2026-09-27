"""Engine adapter for suite-owned per-case executors.

Products in migration keep case bodies inside suite modules that expose a
``run_case(source_root, spec) -> bool`` hook.  This adapter lets those cases
execute under :class:`platform_regress.engine.RegressionEngine` unchanged:
the suite callable keeps owning fixtures, assertions and report artifacts,
while the engine owns scheduling, verdict normalization and evidence
collection.
"""

import json
from pathlib import Path

from ..engine import Blocked, Cancelled
from ..execution.locking import ExclusiveFileLock


class LegacyCaseBinding(object):
    """Resolved wiring for one legacy case execution."""

    def __init__(self, spec, run_case, source_root, run_root=None):
        self.spec = spec
        self.run_case = run_case
        self.source_root = Path(source_root)
        # ``run_root`` resolves the directory the suite wrote its report
        # artifacts into (``spec`` -> Path); ``None`` skips evidence checks.
        self.run_root = run_root


class LegacySuiteCase(object):
    """RegressionCase delegating to a suite-owned ``run_case`` callable.

    ``resolver`` is called for every execution with the engine context and
    must return a :class:`LegacyCaseBinding`; products may therefore resolve
    environment-dependent imports lazily inside it.
    """

    def __init__(self, target, resolver, *, summary="", default_enabled=True,
                 lock_name=None):
        self.target = target
        self.summary = summary
        self.default_enabled = default_enabled
        self._resolver = resolver
        self._lock_name = lock_name
        self._lock = None

    def setup(self, context):
        if self._lock_name is None:
            return
        binding = self._resolver(context)
        self._lock = ExclusiveFileLock(
            binding.source_root / "output" / ("%s.lock" % self._lock_name),
            "%s suite" % self._lock_name,
        )
        self._lock.__enter__()

    def cleanup(self, context):
        if self._lock is not None:
            self._lock.__exit__(None, None, None)
            self._lock = None

    def run(self, context):
        binding = self._resolver(context)
        ok = False
        reason = None
        try:
            ok = bool(binding.run_case(binding.source_root, binding.spec))
        except (Blocked, Cancelled, KeyboardInterrupt, SystemExit):
            self._attach(context, binding)
            raise
        except Exception as exc:  # noqa: BLE001 - legacy reports every failure as FAIL
            reason = str(exc) or type(exc).__name__
        status, summary_reason = self._read_summary(binding)
        self._attach(context, binding)
        if ok:
            if status is None:
                raise RuntimeError("本次未生成可核对的用例报告")
            if status != "PASS":
                raise RuntimeError("用例结果与报告状态不一致: %s" % status)
            return True
        raise AssertionError(reason or summary_reason or "用例返回失败")

    def _report_dir(self, binding):
        if binding.run_root is None:
            return None
        return Path(binding.run_root(binding.spec))

    def _read_summary(self, binding):
        report_dir = self._report_dir(binding)
        if report_dir is None:
            return (None, None)
        try:
            summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return (None, None)
        return (summary.get("status"), summary.get("reason"))

    def _attach(self, context, binding):
        report_dir = self._report_dir(binding)
        if report_dir is None or not report_dir.is_dir():
            return
        report_dir = report_dir.resolve()
        summary = report_dir / "summary.json"
        if summary.is_file():
            context.attach_text("legacy-summary.json",
                                summary.read_text(encoding="utf-8"))
        report = report_dir / "report.txt"
        if report.is_file():
            context.attach_file("legacy-report.txt", report)
        for index, source in enumerate(sorted(report_dir.rglob("*.log")), 1):
            resolved = source.resolve()
            if not resolved.is_relative_to(report_dir) or not resolved.is_file():
                continue
            context.attach_file("legacy-log-%d.log" % index, resolved)
        status, _ = self._read_summary(binding)
        context.step("legacy-verdict", "核对旧用例原始判定",
                     status="PASS" if status == "PASS" else "FAIL",
                     details={"legacy_status": status})
