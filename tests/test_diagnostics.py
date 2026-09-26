import json

from fastapi.testclient import TestClient
from platform_app import actions
from platform_app.api import create_app
from platform_app.diagnostics import build_evidence_bundle
from test_api import settings_for


def _environment(store):
    store.put_environment({
        "id": "lab", "product_id": "fbase-database", "title": "数据库测试",
        "host": "127.0.0.1", "port": 5432, "database_name": "postgres",
        "database_user": "postgres", "deployment_config": None,
        "deployment_target": None,
    })


def test_diagnostic_bundle_redacts_secrets_and_keeps_verdict_separate(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    monkeypatch.setenv("PRODUCT_PLATFORM_FBASE_SOURCE_ROOT", str(tmp_path / "no-source"))
    artifact = tmp_path / "case"
    artifact.mkdir()
    (artifact / "summary.json").write_text(json.dumps({
        "status": "FAIL", "reason": "route mismatch", "password": "password=private-value",
    }))
    result = {
        "product_id": "fbase-database", "environment_id": "lab", "target": "mmr.case",
        "profile": "default", "status": "FAIL", "reason": "route mismatch",
        "updated_at": "2026-09-24T00:00:00+00:00", "artifact_dir": str(artifact),
    }

    bundle = build_evidence_bundle(settings, result)
    assert bundle["running_binary_revision"] is None
    assert bundle["commit_attribution"] == "unverified"
    assert "private-value" not in json.dumps(bundle)
    assert bundle["evidence"][1]["id"] == "summary.json"


def test_diagnosis_is_separate_from_result_and_becomes_stale(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    app = create_app(settings, enqueuer=lambda _: None)
    store = app.state.store
    _environment(store)
    store.put_result("fbase-database", "lab", "mmr.case", "default", "FAIL", "断言失败", None)
    client = TestClient(app)
    monkeypatch.setattr(actions, "diagnose", lambda store, _settings, result: (
        store.put_diagnosis(result, "hash", "test-model", {
            "analysis": {"summary": "检查复制状态", "facts": []}, "evidence": [],
        }) or {"analysis": {"facts": []}}
    ))
    response = client.post("/api/v1/operations", json={
        "environment_id": "lab", "action": "diagnostics.analyze", "target": "mmr.case",
    })
    assert response.status_code == 202
    actions.run_task(store, settings, response.json()["id"])
    diagnosis = client.get("/api/v1/environments/lab/diagnostics/mmr.case").json()
    assert diagnosis["content"]["analysis"]["summary"] == "检查复制状态"
    assert diagnosis["stale"] is False
    assert store.get_result("fbase-database", "lab", "mmr.case")["status"] == "FAIL"

    store.put_result("fbase-database", "lab", "mmr.case", "default", "PASS", None, None)
    assert client.get("/api/v1/environments/lab/diagnostics/mmr.case").json()["stale"] is True
