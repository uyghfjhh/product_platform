import json
import psycopg

from fastapi.testclient import TestClient

from platform_app.actions import run_task
from platform_app.api import create_app
from platform_app.providers import command_for
from platform_regress import CaseContext, RegressionEngine
from test_api import settings_for


TARGET = "mmr.installation.runtime_prerequisites"
BASIC_TARGET = "mmr.cluster_verification.basic"
MAC_TARGET = "mac.separation_of_duties.dba_metadata_access_restrictions"


def test_migrated_case_blocks_without_real_cluster_topology(tmp_path):
    settings = settings_for(tmp_path)
    queued = []
    app = create_app(settings, enqueuer=queued.append)
    client = TestClient(app)
    client.post("/api/v1/environments", json={
        "id": "fbase-lab", "product_id": "fbase-database", "title": "FBase Lab",
        "host": "127.0.0.1", "port": 5432, "deployment_target": "mmr.test",
    }).raise_for_status()
    client.put("/api/v1/regression-bindings/fbase-database/mmr", json={
        "environment_id": "fbase-lab",
    }).raise_for_status()
    submitted = client.post("/api/v1/operations", json={
        "environment_id": "fbase-lab", "action": "tests.fbase",
        "target": TARGET, "parameters": {"cluster": "mmr"},
        "acknowledge_change": True,
    })
    assert submitted.status_code == 202, submitted.text
    run_task(app.state.store, settings, queued[0])
    task = app.state.store.get_task(queued[0])
    result = app.state.store.list_results("fbase-lab")[0]
    assert task["status"] == "FAILED"
    assert result["status"] == "BLOCKED"
    payload = json.loads((settings.output_dir / "regression" / "fbase-lab" / TARGET / "result.json").read_text())
    assert payload["operation_id"] == queued[0]
    assert "拓扑" in payload["reason"]


def test_migrated_case_replaces_stale_result_with_error(tmp_path):
    import importlib.util
    from pathlib import Path

    provider_path = Path(__file__).parents[1] / "products" / "fbase-database" / "provider.py"
    spec = importlib.util.spec_from_file_location("fbase_provider_test", provider_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings = settings_for(tmp_path)
    output = settings.output_dir / "regression" / "lab" / TARGET
    output.mkdir(parents=True)
    (output / "result.json").write_text(json.dumps({
        "target": TARGET, "operation_id": "old-task", "verdict": "PASS",
    }), encoding="utf-8")
    store = create_app(settings, enqueuer=lambda task_id: None).state.store
    terminal, reason = module.PROVIDER.publish_result(
        store, settings, {"id": "lab", "product_id": "fbase-database"},
        {"id": "new-task", "action": "tests.fbase", "target": TARGET,
         "parameters": "{}"}, "FAILED", "工具失败",
    )
    assert terminal == "FAILED"
    assert "本次没有生成" in reason
    assert store.list_results("lab")[0]["status"] == "ERROR"


def test_migrated_case_records_failed_extension_assertion(tmp_path):
    import importlib.util
    from pathlib import Path
    from platform_regress.engine import SqlResult

    case_path = Path(__file__).parents[1] / "products" / "fbase-database" / "cases.py"
    spec = importlib.util.spec_from_file_location("fbase_cases_test", case_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = CaseContext(TARGET, tmp_path, environment={"nodes": {"mmr1": {}, "mmr2": {}}})
    calls = []

    def sql(node, query, *, database="postgres"):
        calls.append((node, query))
        return SqlResult((("1",),) if query == "SELECT 1" else (), (), "SELECT 1")

    context.sql = sql
    result = RegressionEngine().run(module.CASES[TARGET], context)
    assert result.verdict == "FAIL"
    assert len(calls) == 3
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(event["kind"] == "step.finished" and event["payload"]["status"] == "FAIL" for event in events)


def test_migrated_case_uses_primary_nodes_from_pgcluster_topology(tmp_path, monkeypatch):
    monkeypatch.setattr("platform_app.topology.configured_topology", lambda settings, environment: {
        "nodes": [
            {"id": "a", "group": "mmr1", "role": "primary", "host": "10.0.0.1", "port": 10011},
            {"id": "a1", "group": "mmr1", "role": "standby", "host": "10.0.0.2", "port": 10012},
            {"id": "b", "group": "mmr2", "role": "primary", "host": "10.0.0.3", "port": 10021},
            {"id": "c", "group": "mmr3", "role": "primary", "host": "10.0.0.4", "port": 10031},
        ],
    })
    spec = command_for(
        settings_for(tmp_path),
        {"id": "lab", "product_id": "fbase-database", "database_user": "tester"},
        "tests.fbase", TARGET, {"cluster": "mmr"},
    )
    context = json.loads(spec.command[spec.command.index("--context-json") + 1])
    assert context["nodes"] == {
        "mmr1": {"host": "10.0.0.1", "port": 10011},
        "mmr2": {"host": "10.0.0.3", "port": 10021},
        "mmr3": {"host": "10.0.0.4", "port": 10031},
    }
    assert context["user"] == "tester"


def test_three_node_case_blocks_when_third_primary_is_missing(tmp_path):
    import importlib.util
    from pathlib import Path

    case_path = Path(__file__).parents[1] / "products" / "fbase-database" / "cases.py"
    spec = importlib.util.spec_from_file_location("fbase_three_node_cases_test", case_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = CaseContext(BASIC_TARGET, tmp_path, environment={"nodes": {}})
    result = RegressionEngine().run(module.CASES[BASIC_TARGET], context)
    assert result.verdict == "BLOCKED"
    assert "mmr1" in result.reason


def test_mac_case_preserves_four_expected_denials(tmp_path):
    import importlib.util
    from pathlib import Path
    from platform_regress.engine import SqlResult

    path = Path(__file__).parents[1] / "products" / "fbase-database" / "cases.py"
    spec = importlib.util.spec_from_file_location("fbase_mac_cases_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = CaseContext(MAC_TARGET, tmp_path, environment={"nodes": {"primary": {}}})
    queries = []

    def sql(node, query, *, database="postgres"):
        queries.append(query)
        if query.startswith("SELECT (NOT pg_is_in_recovery())"):
            return SqlResult((("true", "true", "true", "on"),), (), "SELECT 1")
        if query.startswith("SELECT *"):
            raise psycopg.errors.InsufficientPrivilege("permission denied")
        raise psycopg.Error("rename metadata relation(audit_rule)" if "audit_rule" in query
                            else "rename metadata relation(policy)")

    context.sql = sql
    result = RegressionEngine().run(module.CASES[MAC_TARGET], context)
    assert result.verdict == "PASS", result.reason
    assert len(queries) == 5
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert len([event for event in events if event["kind"] == "step.finished"]) == 5


def test_mac_case_fails_when_protected_table_is_readable(tmp_path):
    import importlib.util
    from pathlib import Path
    from platform_regress.engine import SqlResult

    path = Path(__file__).parents[1] / "products" / "fbase-database" / "cases.py"
    spec = importlib.util.spec_from_file_location("fbase_mac_readable_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = CaseContext(MAC_TARGET, tmp_path, environment={"nodes": {"primary": {}}})

    def sql(node, query, *, database="postgres"):
        if query.startswith("SELECT (NOT pg_is_in_recovery())"):
            return SqlResult((("true", "true", "true", "on"),), (), "SELECT 1")
        return SqlResult((), (), "SELECT 0")

    context.sql = sql
    result = RegressionEngine().run(module.CASES[MAC_TARGET], context)
    assert result.verdict == "FAIL"
    assert "意外成功" in result.reason


def test_mac_platform_case_selects_pgcluster_primary(tmp_path, monkeypatch):
    monkeypatch.setattr("platform_app.topology.configured_topology", lambda settings, environment: {
        "nodes": [
            {"id": "mac_primary", "role": "primary", "host": "10.0.0.5", "port": 15432},
            {"id": "mac_standby", "role": "standby", "host": "10.0.0.6", "port": 15433},
        ],
    })
    spec = command_for(
        settings_for(tmp_path),
        {"id": "lab", "product_id": "fbase-database", "database_user": "postgres"},
        "tests.fbase", MAC_TARGET, {"cluster": "mac"},
    )
    context = json.loads(spec.command[spec.command.index("--context-json") + 1])
    assert context["nodes"] == {"primary": {"host": "10.0.0.5", "port": 15432}}
