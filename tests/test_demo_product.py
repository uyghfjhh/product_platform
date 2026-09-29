"""Installed demo product exercises the documented product integration path."""

import json

from fastapi.testclient import TestClient

from platform_app.actions import run_task
from platform_app.api import create_app
from test_api import settings_for


def test_demo_product_api_worker_and_evidence(tmp_path):
    settings = settings_for(tmp_path)
    queued = []
    app = create_app(settings, enqueuer=queued.append)
    client = TestClient(app)
    assert "demo" in {item["id"] for item in client.get("/api/v1/products").json()}
    cases = client.get("/api/v1/cases?product_id=demo")
    assert cases.status_code == 200
    assert [item["target"] for item in cases.json()] == ["smoke.context"]

    environment = {
        "id": "demo-lab", "product_id": "demo", "title": "Demo lab",
        "host": "127.0.0.1", "port": 5432,
        "deployment_target": "demo.local",
    }
    client.post("/api/v1/environments", json=environment).raise_for_status()
    assert client.put("/api/v1/regression-bindings/demo/smoke",
                      json={"environment_id": "demo-lab"}).status_code == 200
    response = client.post("/api/v1/operations", json={
        "environment_id": "demo-lab", "action": "tests.demo", "target": "smoke.context",
    })
    assert response.status_code == 202, response.text
    task_id = response.json()["id"]
    assert queued == [task_id]

    run_task(app.state.store, settings, task_id)
    assert client.get(f"/api/v1/operations/{task_id}").json()["status"] == "SUCCEEDED"
    output = settings.data_dir / "regression" / "demo-lab" / "smoke.context"
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["verdict"] == "PASS"
    assert result["operation_id"] == task_id
    assert (output / result["evidence"][0]).read_text(encoding="utf-8") == "demo-lab\n"
