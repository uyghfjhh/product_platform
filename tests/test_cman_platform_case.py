import importlib.util
import json
from pathlib import Path

import pytest

from platform_regress import CaseContext, CommandResult, RegressionEngine


TARGET = "global_cache.reuse_single_and_cross_client"


def load_cases():
    path = Path(__file__).parents[1] / "products" / "fbasecman" / "cases.py"
    spec = importlib.util.spec_from_file_location("cman_cases_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("legacy_status,exit_code,expected", [
    ("PASS", 0, "PASS"), ("FAIL", 1, "FAIL"), ("PASS", 1, "ERROR"),
])
def test_bulk_cman_bridge_checks_fresh_summary(tmp_path, legacy_status, exit_code, expected):
    source = tmp_path / "source"
    (source / "suites").mkdir(parents=True)
    (source / "suites" / "registry.py").write_text("", encoding="utf-8")
    override = tmp_path / "override.yaml"
    override.write_text("framework: {}\n", encoding="utf-8")
    report_root = tmp_path / "reports"
    summary = report_root / "output" / "runs" / "global_cache" / "reuse_single_and_cross_client" / "summary.json"
    summary.parent.mkdir(parents=True)
    context = CaseContext(TARGET, tmp_path / "output", environment={
        "legacy_source": str(source), "legacy_override": str(override),
        "legacy_report_root": str(report_root),
    })

    def command(*args, **kwargs):
        summary.write_text(json.dumps({"status": legacy_status, "reason": "old reason"}),
                           encoding="utf-8")
        (summary.parent / "report.txt").write_text("Original report\n", encoding="utf-8")
        return CommandResult(exit_code, "", "", 0.1)

    context.command = command
    result = RegressionEngine().run(load_cases().CASES[TARGET], context)
    assert result.verdict == expected
    assert result.evidence == (
        f"artifacts/{result.execution_id}/legacy-summary.json",
        f"artifacts/{result.execution_id}/legacy-report.txt",
    )
    if expected == "FAIL":
        assert result.reason == "old reason"


def test_bulk_cman_bridge_rejects_unchanged_old_summary(tmp_path):
    source = tmp_path / "source"
    (source / "suites").mkdir(parents=True)
    (source / "suites" / "registry.py").write_text("", encoding="utf-8")
    override = tmp_path / "override.yaml"
    override.write_text("framework: {}\n", encoding="utf-8")
    report_root = tmp_path / "reports"
    summary = report_root / "output" / "runs" / "global_cache" / "reuse_single_and_cross_client" / "summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text('{"status":"PASS"}', encoding="utf-8")
    context = CaseContext(TARGET, tmp_path / "output", environment={
        "legacy_source": str(source), "legacy_override": str(override),
        "legacy_report_root": str(report_root),
    })
    context.command = lambda *args, **kwargs: CommandResult(0, "", "", 0.1)
    result = RegressionEngine().run(load_cases().CASES[TARGET], context)
    assert result.verdict == "ERROR"
    assert "未更新" in result.reason


def test_bulk_cman_catalog_registers_all_targets():
    module = load_cases()
    assert len(module.CASES) == len(module.CASE_METADATA) == 211


def test_savepoint_protocol_case_is_native():
    module = load_cases()
    case = module.CASES["sql_parse.savepoint_recovery_after_local_25p02"]
    assert type(case).__name__ == "SavepointRecoveryCase"
