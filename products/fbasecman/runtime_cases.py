"""fbasecman bindings for the platform runtime/executor case contract.

The remaining suites keep their product knowledge (runtime fixture/assertion
methods, executors, configuration transforms) while the platform engine owns
verdict mapping and the ``CaseContext`` evidence surface.  The product run
tree still lands under ``env.output_dir`` — the deployment profile points it
at the legacy report root consumed by the product web UI, so
``output/runs/<suite>/<case>/{steps,summary,report}`` stays byte-identical.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import yaml

from platform_regress import Blocked, Cancelled, CaseFailure
from platform_regress.execution.locking import ExclusiveFileLock
from platform_regress.suites.executor import RuntimeBinding, RuntimeExecutorCase


PRODUCT_ROOT = Path(__file__).parent
REPO_ROOT = PRODUCT_ROOT.parent.parent
DEFAULT_LEGACY_ROOT = PRODUCT_ROOT / "regression" / "legacy"
_EXTRA_CONFIGS = []
_LOADER_PROFILED = False


def _ensure_imports(source):
    global _LOADER_PROFILED
    for path in (REPO_ROOT, REPO_ROOT / "backend", source):
        value = str(path)
        while value in sys.path:
            sys.path.remove(value)
        sys.path.insert(0, value)
    import cmanconf
    if _LOADER_PROFILED:
        return
    original = cmanconf.load_regression_config

    def load_with_profile(root_dir, extra_configs=None, validate=True):
        extras = list(extra_configs or []) + list(_EXTRA_CONFIGS)
        return original(root_dir, extra_configs=extras, validate=validate)

    cmanconf.load_regression_config = load_with_profile
    # ``set_legacy_config_loader`` captured the unwrapped loader during the
    # cmanconf import above; re-register the wrapped one so runtime __init__
    # paths resolve the same environment overlay as this resolver.
    import platform_regress.runtime as _runtime
    _runtime.set_legacy_config_loader(load_with_profile)
    _LOADER_PROFILED = True


def _environment(context):
    environment = context.environment or {}
    source = Path(environment.get("legacy_source") or DEFAULT_LEGACY_ROOT).resolve()
    override_value = environment.get("legacy_override")
    report_value = environment.get("legacy_report_root")
    if not override_value or not report_value:
        raise Blocked("缺少当前环境的 fbasecman 测试配置或报告目录")
    override = Path(override_value).resolve()
    if not (source / "suites" / "registry.py").is_file() or not override.is_file():
        raise Blocked("fbasecman 用例来源或环境覆盖配置不存在")
    _EXTRA_CONFIGS[:] = [override]
    _ensure_imports(source)
    import cmanconf
    env = cmanconf.load_regression_config(source)
    cmanconf.validate_profile_isolation(env)
    return source, env


def _suite_case_items(manifest, suite_id):
    """Mirror each plugin's ``case_loader`` without importing suite.py/runner."""
    if suite_id == "handover":
        return [case for case in manifest.case_items(include_long_time=True)
                if case.enabled]
    if suite_id == "global_cache":
        return manifest.formal_case_items()
    return manifest.case_items()


def _spec(source, suite_id, name):
    try:
        manifest = importlib.import_module("suites.%s.manifest" % suite_id)
    except ImportError:
        raise Blocked("未注册的 fbasecman 套件: %s" % suite_id)
    spec = next((item for item in _suite_case_items(manifest, suite_id)
                 if item.name == name), None)
    if spec is None:
        raise Blocked("套件 %s 没有用例 %s" % (suite_id, name))
    return spec


def _write_init_failure_report(env, suite_id, spec, exc):
    """Preserve run_runtime_case's report when runtime construction fails."""
    try:
        run_root = Path(env.output_dir) / "runs" / suite_id / spec.name
        run_root.mkdir(parents=True, exist_ok=True)
        report = (
            "Test: %s\nStatus: FAIL\nSummary: %s\nFailure: %s\n"
            % (spec.target, spec.summary, exc)
        )
        report += "Steps: <runtime initialization failed before steps could run>\n"
        (run_root / "report.txt").write_text(report, encoding="utf-8")
    except Exception:
        pass


def _guarded_factory(env, suite_id, failure_class, make):
    """Wrap runtime construction; init failures keep the legacy FAIL report."""
    def factory(context, spec):
        try:
            runtime = make(context, spec)
        except (Blocked, Cancelled):
            raise
        except Exception as exc:
            _write_init_failure_report(env, suite_id, spec, exc)
            if isinstance(exc, CaseFailure):
                raise
            raise failure_class(str(exc)) from exc
        runtime.platform_case_context = context
        return runtime
    return factory


def _finalize_run(context, runtime):
    """Mirror the product run tree into the platform evidence surface."""
    run_root = getattr(runtime, "run_root", None)
    if run_root is None or not Path(run_root).is_dir():
        return
    for name in ("report.txt", "summary.json", "steps.json", "fbasecman.log",
                 "events.log", "backtrace.txt", "manifest.json"):
        path = Path(run_root) / name
        if path.is_file():
            try:
                context.attach_file(name, path)
            except Exception:
                pass
    try:
        payload = json.loads((Path(run_root) / "steps.json").read_text(encoding="utf-8"))
        records = payload.get("steps") or []
    except Exception:
        records = []
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            continue
        title = str(record.get("title") or "步骤 %d" % index)
        context.step(
            "journal-%d" % index, title[:200] or "步骤 %d" % index,
            status="PASS" if record.get("result") == "PASS" else "FAIL",
            details={
                "expected": record.get("expected"),
                "actual": record.get("actual"),
                "result": record.get("result"),
                "phase": record.get("phase"),
            },
        )


class _LockedHandoverRuntimeMixin:
    """Per-case suite lock; mirrors legacy ``run()``'s exclusive file lock."""

    def __enter__(self):
        self._suite_lock = ExclusiveFileLock(
            Path(self.env.output_dir) / "handover.lock", "handover suite")
        self._suite_lock.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.stop()
        finally:
            self._suite_lock.__exit__(exc_type, exc_val, exc_tb)
            self._suite_lock = None
        return False


def resolve_runtime_binding(context, suite_id, name):
    source, env = _environment(context)
    spec = _spec(source, suite_id, name)
    if suite_id == "ha_commands":
        executors = importlib.import_module("suites.ha_commands.dispatch").EXECUTORS
        importlib.import_module("suites.ha_commands.manifest").validate_manifest()
        runtime_type = importlib.import_module(
            "suites.ha_commands.runtime").HaCommandRuntime
        failure_class = runtime_type.failure_class
        ops = importlib.import_module("fbasecman_ops")

        def context_executor(ctx, runtime):
            ops.bind(runtime)
            try:
                try:
                    function = executors[spec.executor]
                except KeyError:
                    raise failure_class("missing executor %s" % spec.executor)
                return function(ctx)
            finally:
                ops.unbind()

        reason = "命令输入输出及用例声明的配置、日志和运行态证据均符合预期。"
        return RuntimeBinding(
            spec,
            _guarded_factory(env, suite_id, failure_class,
                             lambda ctx, case: runtime_type(
                                 source, case, env=env)),
            None, reason,
            context_executor=context_executor,
            finalize=_finalize_run)
    elif suite_id == "high_availability":
        executors = importlib.import_module(
            "suites.high_availability.dispatch").EXECUTORS
        runtime_type = importlib.import_module(
            "suites.high_availability.runtime").HighAvailabilityRuntime
        failure_class = importlib.import_module(
            "suites.high_availability.runtime").HighAvailabilityFailure
        ops = importlib.import_module("fbasecman_ops")

        def context_executor(ctx, runtime):
            ops.bind(runtime)
            try:
                function = executors.get(spec.name)
                if function is None:
                    raise failure_class("Executor for %s not implemented" % spec.name)
                return function(ctx)
            finally:
                ops.unbind()

        reason = "%s；报告所列操作均执行成功，全部检测项符合预期。" % spec.summary
        return RuntimeBinding(
            spec,
            _guarded_factory(env, suite_id, failure_class,
                             lambda ctx, case: runtime_type(
                                 source, case, env=env)),
            None, reason,
            on_failure=lambda runtime, exc: runtime.add_failure_diagnostic(exc),
            context_executor=context_executor,
            finalize=_finalize_run)
    elif suite_id == "handover":
        dispatch_executor = importlib.import_module(
            "suites.handover.executors").dispatch_executor
        importlib.import_module("suites.handover.manifest").validate_manifest()
        runtime_type = type(
            "HandoverRuntimeLocked",
            (_LockedHandoverRuntimeMixin,
             importlib.import_module("suites.handover.runtime").HandoverRuntime),
            {},
        )
        failure_class = importlib.import_module(
            "suites.handover.runtime").HandoverFailure
        ops = importlib.import_module("fbasecman_ops")

        def context_executor(ctx, runtime):
            ops.bind(runtime)
            try:
                return dispatch_executor(spec)(ctx)
            except KeyError as exc:
                raise failure_class(
                    "missing executor for %s: %s" % (spec.name, exc)) from exc
            finally:
                ops.unbind()

        reason = "文档规定的 SQL/JDBC 结果、路由、console 状态和业务统计均满足预期。"
        # Legacy run_case writes the FAIL report while the suite lock is still
        # held; teardown (stop + unlock) happens in ``finally`` afterwards.
        return RuntimeBinding(
            spec,
            _guarded_factory(env, suite_id, failure_class,
                             lambda ctx, case: runtime_type(
                                 source, case, env=env)),
            None, reason,
            teardown_before_finish=False,
            context_executor=context_executor,
            finalize=_finalize_run)
    else:
        raise Blocked("套件 %s 尚未定义平台 RuntimeBinding" % suite_id)
    return RuntimeBinding(
        spec,
        _guarded_factory(env, suite_id, failure_class,
                         lambda ctx, case: runtime_type(source, case)),
        executor, reason,
        finalize=_finalize_run)


def global_cache_case(item, spec):
    """SDK-hosted case preserving the suite's distinct verdict/report flow."""
    return _GlobalCachePlatformCase(
        item["target"], item["target"].partition(".")[2],
        summary=item.get("summary") or "",
        default_enabled=bool(item.get("enabled", True)
                             and getattr(spec, "enabled", True)))


class _GlobalCachePlatformCase:
    """Run one global_cache case through the platform engine.

    The suite's legacy ``_run_case`` owns a two-stage verdict (executor
    assertion, then core-dump detection during teardown) and its own report /
    summary writers; this host reproduces that flow verbatim while the engine
    owns the normalized verdict.
    """

    def __init__(self, target, name, *, summary="", default_enabled=True):
        self.target = target
        self.summary = summary
        self.default_enabled = default_enabled
        self._name = name
        self._runtime = None
        self._execute_case = None
        self._assert_negative_logs = None
        self._failure_class = CaseFailure

    def setup(self, context):
        source, env = _environment(context)
        spec = _spec(source, "global_cache", self._name)
        dispatch = importlib.import_module("suites.global_cache.dispatch")
        importlib.import_module(
            "suites.global_cache.runtime")._validate_report_levels()
        runtime_type = importlib.import_module(
            "suites.global_cache.runtime").CaseRuntime
        context_data = yaml.safe_load(
            env.test_context_file.read_text(encoding="utf-8")) or {}
        runtime = runtime_type(source, env, context_data, spec)
        runtime.platform_case_context = context
        self._runtime = runtime
        self._execute_case = dispatch._execute_case
        self._assert_negative_logs = importlib.import_module(
            "suites.global_cache.domains.common_assertions")._assert_negative_logs
        self._failure_class = importlib.import_module(
            "suites.global_cache.errors").GlobalCacheFailure

    def run(self, context):
        rt = self._runtime
        case = rt.case
        rt.trace("[global_cache] run   %s" % case.target)
        rt.trace("summary: %s" % case.summary)
        rt.trace(
            "manifest: batch=%s driver=%s topology=%s rw=%s pool=%s"
            % (case.batch, case.driver, case.topology,
               case.rw_split_method, case.pool_mode)
        )
        if case.notes:
            rt.trace("notes: %s" % " | ".join(case.notes))
        failure = None
        ops = importlib.import_module("fbasecman_ops")
        ops.bind(rt)
        try:
            self._execute_case(context)
            self._assert_negative_logs(context)
            rt.summary["status"] = "PASS"
        except Exception as exc:
            failure = exc
            rt.summary["status"] = "FAIL"
            issue_suffix = " [%s]" % case.issue_id if case.issue_id else ""
            rt.summary["reason"] = "%s%s" % (str(exc), issue_suffix)
            if not rt.summary.get("failed_step"):
                previous = (rt.step_records[-1].get("title", "<none>")
                            if rt.step_records else "<none>")
                rt.record_step(
                    "用例断言阶段失败",
                    output="last completed step: %s" % previous,
                    expected="用例完成全部业务步骤和产品行为检测，不抛出异常。",
                    actual=str(exc),
                    result="FAIL",
                    phase="assertion",
                )
                rt.summary["failed_step"] = dict(rt.step_records[-1])
        finally:
            rt.stop_fbasecman(best_effort=True, record=False)
            core_info = rt.detect_new_core()
            if core_info is not None:
                core_path, gdb_cmd = core_info
                reason = ("fbasecman generated core dump during execution or "
                          "shutdown: %s" % core_path)
                previous_reason = rt.summary.get("reason")
                rt.summary["status"] = "FAIL"
                rt.summary["reason"] = (
                    "%s; %s" % (previous_reason, reason)
                    if previous_reason else reason)
                rt.record_step(
                    "产品进程崩溃检测",
                    expected="用例执行及进程停止期间不产生 core dump。",
                    actual=core_path,
                    result="FAIL",
                    phase="cleanup",
                )
                rt.summary["failed_step"] = dict(rt.step_records[-1])
                print("    core: %s" % core_path, flush=True)
                print("    gdb : %s" % gdb_cmd, flush=True)
            rt.capture_core_log_evidence()
            rt.finish()
            rt.write_summary()
            rt.write_report()
            rt.prune_artifacts()
            ops.unbind()
        passed = rt.summary["status"] == "PASS"
        if passed:
            print("%-55s SUCCESS" % case.target, flush=True)
            return True
        print("%-55s FAIL" % case.target, flush=True)
        print("    reason: %s" % rt.summary.get("reason", "unknown"), flush=True)
        if isinstance(failure, (Blocked, Cancelled)):
            raise failure
        raise self._failure_class(rt.summary.get("reason", "unknown"))

    def cleanup(self, context):
        if self._runtime is None:
            return
        self._runtime.stop_fbasecman(best_effort=True, record=False)
        _finalize_run(context, self._runtime)


def runtime_case(item, spec):
    suite_id, _, name = item["target"].partition(".")
    if suite_id == "global_cache":
        return global_cache_case(item, spec)
    enabled = item.get("enabled", True)
    if spec is not None:
        enabled = enabled and getattr(spec, "enabled", True) \
            and not getattr(spec, "long_time", False)
    return RuntimeExecutorCase(
        item["target"], lambda context: resolve_runtime_binding(context, suite_id, name),
        summary=item.get("summary") or "", default_enabled=bool(enabled))
