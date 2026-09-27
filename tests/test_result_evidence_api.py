import json

from fastapi.testclient import TestClient

from platform_app.api import create_app
from test_api import settings_for


def test_archived_result_evidence_is_readable_without_product_code(tmp_path):
    settings = settings_for(tmp_path)
    (settings.data_dir / "environments").mkdir(parents=True)
    app = create_app(settings, enqueuer=lambda task_id: None)
    client = TestClient(app)
    client.post("/api/v1/environments", json={
        "id": "lab", "product_id": "fbase-database", "title": "Lab",
        "host": "127.0.0.1", "port": 5432,
    }).raise_for_status()
    target = "mac.audit.log_access_restrictions"
    output = settings.environment_dir / "regression" / "lab" / target
    evidence = output / "artifacts" / "execution" / "report.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("original verdict\n", encoding="utf-8")
    reference = "artifacts/execution/report.txt"
    (output / "result.json").write_text(json.dumps({
        "target": target, "execution_id": "execution", "verdict": "FAIL",
        "evidence": [reference],
    }), encoding="utf-8")
    app.state.store.put_result("fbase-database", "lab", target, "default", "FAIL",
                               "old failure", str(output))

    route = f"/api/v1/environments/lab/results/{target}/evidence"
    listed = client.get(route)
    assert listed.status_code == 200
    assert listed.json()["evidence"] == [reference]
    content = client.get(route + "/" + reference)
    assert content.status_code == 200
    assert content.content == b"original verdict\n"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert client.get(route + "/artifacts/execution/unlisted.txt").status_code == 404


def test_result_evidence_rejects_external_artifact_directory(tmp_path):
    settings = settings_for(tmp_path)
    app = create_app(settings, enqueuer=lambda task_id: None)
    client = TestClient(app)
    client.post("/api/v1/environments", json={
        "id": "lab", "product_id": "fbase-database", "title": "Lab",
        "host": "127.0.0.1", "port": 5432,
    }).raise_for_status()
    target = "mac.audit.log_access_restrictions"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "result.json").write_text(json.dumps({
        "target": target, "evidence": ["secret.txt"],
    }), encoding="utf-8")
    app.state.store.put_result("fbase-database", "lab", target, "default", "PASS",
                               "", str(outside))
    response = client.get(f"/api/v1/environments/lab/results/{target}/evidence")
    assert response.status_code == 404
