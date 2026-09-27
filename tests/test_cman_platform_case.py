import importlib.util
import json
from pathlib import Path

import pytest

from platform_regress import Blocked, CaseContext, RegressionEngine
from platform_regress.suites.legacy import LegacyCaseBinding, LegacySuiteCase


TARGET = "global_cache.reuse_single_and_cross_client"
LONG_TIME_TARGETS = {
    "handover.console_server_lifecycle_statistics",
    "handover.console_client_statistics",
    "handover.console_pgbench_mmr_hint_statistics",
    "handover.console_pgbench_mmr_port_statistics",
    "handover.console_pgbench_rep_hint_statistics",
    "handover.console_pgbench_balance_statistics",
}


def load_cases():
    path = Path(__file__).parents[1] / "products" / "fbasecman" / "cases.py"
    spec = importlib.util.spec_from_file_location("cman_cases_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _binding(tmp_path, run_case, status=None, reason=None):
    """Build a resolved binding whose fake executor writes suite artifacts."""
    report_dir = tmp_path / "runs" / "global_cache" / "reuse_single_and_cross_client"

    def executor(root, spec):
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "report.txt").write_text("原始报告\n", encoding="utf-8")
        if status is not None:
            (report_dir / "summary.json").write_text(
                json.dumps({"status": status, "reason": reason}, ensure_ascii=False),
                encoding="utf-8")
        return run_case(root, spec)

    spec = type("Spec", (), {"target": TARGET, "suite_id": "global_cache",
                             "name": "reuse_single_and_cross_client"})()
    return LegacyCaseBinding(spec, executor, tmp_path / "source",
                             run_root=lambda item: report_dir)


def _engine_run(case, tmp_path):
    context = CaseContext(TARGET, tmp_path / "output", environment={})
    return RegressionEngine().run(case, context), context


def test_catalog_registers_all_targets():
    module = load_cases()
    assert len(module.CASES) == len(module.CASE_METADATA) == 212


def test_long_time_cases_skip_suite_runs():
    module = load_cases()
    disabled = {target for target, case in module.CASES.items()
                if not getattr(case, "default_enabled", True)}
    assert disabled == LONG_TIME_TARGETS


def test_resolve_blocked_without_environment():
    module = load_cases()
    case = module.CASES[TARGET]
    with pytest.raises(Blocked):
        case._resolve(type("Ctx", (), {"environment": {}})())


@pytest.mark.parametrize("status,reason,expected", [
    ("PASS", "全部通过", "PASS"),
    ("FAIL", "旧断言失败", "FAIL"),
])
def test_inprocess_bridge_maps_suite_verdict(tmp_path, status, reason, expected):
    case = LegacySuiteCase(TARGET, lambda ctx: _binding(
        tmp_path, lambda root, spec: status == "PASS", status, reason))
    result, context = _engine_run(case, tmp_path)
    assert result.verdict == expected
    if expected == "FAIL":
        assert result.reason == reason
    assert f"artifacts/{result.execution_id}/legacy-summary.json" in result.evidence
    assert f"artifacts/{result.execution_id}/legacy-report.txt" in result.evidence


def test_executor_exception_maps_to_fail(tmp_path):
    def broken(root, spec):
        raise RuntimeError("no executor found for handover case: x")
    case = LegacySuiteCase("handover.x", lambda ctx: _binding(
        tmp_path, broken, "FAIL", "no executor found for handover case: x"))
    result, _ = _engine_run(case, tmp_path)
    assert result.verdict == "FAIL"
    assert "no executor found" in result.reason


def test_pass_without_report_is_error(tmp_path):
    case = LegacySuiteCase(TARGET, lambda ctx: _binding(
        tmp_path, lambda root, spec: True, None, None))
    result, _ = _engine_run(case, tmp_path)
    assert result.verdict == "ERROR"
    assert "未生成可核对" in result.reason


def test_pass_result_rejects_fail_summary(tmp_path):
    case = LegacySuiteCase(TARGET, lambda ctx: _binding(
        tmp_path, lambda root, spec: True, "FAIL", "矛盾"))
    result, _ = _engine_run(case, tmp_path)
    assert result.verdict == "ERROR"
    assert "不一致" in result.reason


def test_savepoint_protocol_case_is_native():
    module = load_cases()
    case = module.CASES["sql_parse.savepoint_recovery_after_local_25p02"]
    assert type(case).__name__ == "SavepointRecoveryCase"


def test_jdbc_console_case_is_native():
    module = load_cases()
    assert type(module.CASES["ha_commands.jdbc_console_ha_commands"]).__name__ == "JdbcConsoleHaCommandsCase"


def test_set_node_write_idempotent_case_is_native():
    module = load_cases()
    assert type(module.CASES["ha_commands.set_node_write_idempotent"]).__name__ == "SetNodeWriteIdempotentCase"


def test_more_idempotent_ha_cases_are_native():
    module = load_cases()
    assert type(module.CASES["ha_commands.set_node_promoted_idempotent"]).__name__ == "IdempotentHaCommandCase"
    assert type(module.CASES["ha_commands.set_cluster_active_idempotent"]).__name__ == "IdempotentHaCommandCase"
