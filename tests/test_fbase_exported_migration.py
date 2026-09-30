"""Migration-fidelity tests for the exported FBase regression cases.

These pin the platform contract the legacy executor relied on: selector
resolution, {run_id}/port expansion, psql assertion semantics and the
catalog-to-native coverage guarantee (no case may silently stay legacy).
"""
import importlib.util
import json
from pathlib import Path

import pytest

from platform_regress.sdk import CaseContext, RegressionEngine
from platform_regress.sdk import resolve_selector
from platform_regress.steps import (
    StepExecutionResult, evaluate_assertion, execute_psql,
    meaningful_lines, format_psql_output,
)


CASES_PATH = Path(__file__).parents[1] / "products" / "fbase-database" / "cases.py"


def _load_cases():
    spec = importlib.util.spec_from_file_location("fbase_exported_cases", CASES_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cases_module():
    return _load_cases()


def _environment():
    return {
        "cluster": "mmr", "cluster_name": "mmr", "user": "postgres",
        "nodes": {
            "mmr1_primary": {"host": "127.0.0.1", "port": 10011, "data_dir": "", "role": "mmr_primary:mmr1"},
            "mmr1_standby": {"host": "127.0.0.1", "port": 10012, "data_dir": "", "role": "mmr_standby:mmr1"},
            "mmr2_primary": {"host": "127.0.0.1", "port": 10021, "data_dir": "", "role": "mmr_primary:mmr2"},
            "mmr3_primary": {"host": "127.0.0.1", "port": 10031, "data_dir": "", "role": "mmr_primary:mmr3"},
        },
        "node_order": ["mmr1_primary", "mmr1_standby", "mmr2_primary", "mmr3_primary"],
        "node_groups": {
            "mmr": {"members": {
                "mmr1": {"primary": "mmr1_primary", "standbys": ["mmr1_standby"]},
                "mmr2": {"primary": "mmr2_primary", "standbys": []},
                "mmr3": {"primary": "mmr3_primary", "standbys": []},
            }},
        },
        "plugins": ["fdd_mmr"], "plugins_detail": {},
    }


def test_every_catalog_case_runs_on_platform_natively(cases_module):
    catalog = json.loads(
        (CASES_PATH.parent / "regression" / "cases.json").read_text(encoding="utf-8"))
    for case in catalog["cases"]:
        impl = cases_module.CASES.get(case["id"])
        assert impl is not None, case["id"]
        assert type(impl).__name__ != "LegacyFbaseCase", case["id"]


def test_selector_resolution_matches_legacy_topology_order():
    environment = _environment()
    assert resolve_selector(environment, "primary") == "mmr1_primary"
    assert resolve_selector(environment, "writable") == "mmr1_primary"
    assert resolve_selector(environment, "mmr:mmr2") == "mmr2_primary"
    assert resolve_selector(environment, "mmr:mmr1:standby") == "mmr1_standby"
    assert resolve_selector(environment, "mmr1_standby") == "mmr1_standby"
    with pytest.raises(Exception):
        resolve_selector(environment, "mmr:nope")


def test_logical_subscriber_resolves_through_groups():
    environment = _environment()
    environment["nodes"]["logical_subscriber"] = {
        "host": "127.0.0.1", "port": 15434, "data_dir": "", "role": "logical_subscriber"}
    environment["node_groups"]["logical"] = {
        "publisher": "mmr1_primary", "subscribers": {"logical_subscriber": {}}}
    assert resolve_selector(environment, "subscriber") == "logical_subscriber"
    assert resolve_selector(environment, "logical_subscriber") == "logical_subscriber"


def test_expand_remaps_declared_ports_and_run_id(tmp_path):
    context = CaseContext("t", tmp_path, environment=_environment(), run_id="runX")
    context.values["isolated_mmr_port_mapping"] = {"15651": "23456", "15652": "23457"}
    assert context.expand("psql -p 15651") == "psql -p 23456"
    assert context.expand("port=15652") == "port=23457"
    assert context.expand("host=127.0.0.1:15651") == "host=127.0.0.1:23456"
    assert context.expand("15651") == "23456"
    assert context.expand("x-{run_id}") == "x-runX"


def test_sql_assertion_semantics_match_legacy():
    ok = StepExecutionResult(0, output="out", rows=[["1", "a"]], columns=["id", "n"])
    assert evaluate_assertion({"type": "rows_equal", "rows": [["1", "a"]]}, ok)[0]
    assert not evaluate_assertion({"type": "rows_equal", "rows": [["1", "b"]]}, ok)[0]
    assert evaluate_assertion(
        {"type": "rows_with_output_contains", "rows": [["1", "a"]],
         "value": "ou"}, ok)[0]
    assert evaluate_assertion({"type": "scalar_equals", "value": "1"},
                              StepExecutionResult(0, rows=[["1"]]))[0]
    assert evaluate_assertion({"type": "query_equals", "value": "1|a"}, ok)[0]
    err = StepExecutionResult(1, output="ERROR:  42501: permission denied",
                              sqlstate="42501", error_message="permission denied")
    assert evaluate_assertion({"type": "sql_error", "sqlstate": "42501",
                               "message_contains": "permission"}, err)[0]
    assert not evaluate_assertion({"type": "sql_error", "sqlstate": "42502",
                                   "message_contains": "permission"}, err)[0]
    assert evaluate_assertion({"type": "sql_fails",
                               "message_contains": "permission"}, err)[0]
    assert evaluate_assertion({"type": "command_succeeds"}, ok)[0]
    assert not evaluate_assertion({"type": "command_succeeds"}, err)[0]
    assert evaluate_assertion({"type": "command_fails",
                               "message_contains": "denied"}, err)[0]
    assert evaluate_assertion({"type": "output_contains", "values": ["out"]}, ok)[0]
    assert evaluate_assertion({"type": "output_contains_text", "values": ["out"]}, ok)[0]


def test_output_helpers_strip_password_expiry_noise():
    assert meaningful_lines("x\n\n  Password will expire at 2026\n y ") == ["x", "y"]
    assert "Password will expire" not in format_psql_output(
        "Password will expire at 2026\nrow")


def test_session_cases_share_port_mapping_shape(cases_module):
    grouped = {}
    for target, case in cases_module.CASES.items():
        key = getattr(case, "session_key", None)
        if key:
            grouped.setdefault(key, []).append(target)
    assert grouped, "expected at least one shared session"
    assert any(len(members) > 1 for members in grouped.values())
    for members in grouped.values():
        for member in members:
            impl = cases_module.CASES[member]
            assert type(impl).__name__ == "ExportedCommandCase"


def test_exported_case_blocks_cleanly_without_environment(cases_module, tmp_path):
    target = sorted(cases_module.EXPORTED_COMMAND_CASES)[0]
    context = CaseContext(target, tmp_path,
                          environment={"cluster": "mmr", "cluster_name": "mmr",
                                       "nodes": {}, "plugins": []})
    result = RegressionEngine().run(cases_module.CASES[target], context)
    assert result.verdict == "BLOCKED"
    assert result.business_verdict == "BLOCKED"


def test_isolated_case_skips_shared_cluster_gate(cases_module, tmp_path):
    isolated = [
        target for target, case in cases_module.EXPORTED_COMMAND_CASES.items()
        if any(isinstance(f, dict) and f.get("type") == "isolated_mmr_node_creation"
               for f in case.get("fixtures") or [])
    ]
    assert isolated
    target = isolated[0]
    context = CaseContext(target, tmp_path,
                          environment={"cluster": "mmr", "cluster_name": "mmr",
                                       "nodes": {}, "plugins": ["fdd_mmr"]})
    result = RegressionEngine().run(cases_module.CASES[target], context)
    # The disposable topology skips shared-cluster health gates; the first
    # failure must come from its own commands, not the environment blocker.
    assert "没有健康节点" not in result.reason
    assert result.verdict in ("FAIL", "BLOCKED", "ERROR")
