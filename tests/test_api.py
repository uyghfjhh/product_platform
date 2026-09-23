import socket
from pathlib import Path

from fastapi.testclient import TestClient

from platform_app.actions import run_task
from platform_app.api import create_app
from platform_app.config import Settings


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        pgcluster_root=tmp_path / "missing-pgcluster",
        fbasecman_regress_root=tmp_path / "missing-cman",
        fbase_regress_root=tmp_path / "missing-fbase",
        license_key_dir=tmp_path / "keys",
        license_vendor="测试厂商",
    )


def test_catalog_and_environment_persist_without_external_services(tmp_path):
    config = settings_for(tmp_path)
    client = TestClient(create_app(config, enqueuer=lambda task_id: None))
    products = client.get("/api/v1/products").json()
    assert {item["id"] for item in products} == {"fbasecman", "fbase-database"}

    environment = {
        "id": "lab-cman",
        "product_id": "fbasecman",
        "title": "代理实验室",
        "host": "127.0.0.1",
        "port": 5432,
        "database_name": "postgres",
        "database_user": "postgres",
    }
    assert client.post("/api/v1/environments", json=environment).status_code == 201
    assert client.post("/api/v1/environments", json=environment).status_code == 409
    restarted = TestClient(create_app(config, enqueuer=lambda task_id: None))
    assert (
        restarted.get("/api/v1/environments/lab-cman").json()["title"] == "代理实验室"
    )


def test_actual_tcp_check_publishes_events_and_current_status(tmp_path):
    config = settings_for(tmp_path)
    app = create_app(
        config, enqueuer=lambda task_id: run_task(app.state.store, config, task_id)
    )
    client = TestClient(app)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        client.post(
            "/api/v1/environments",
            json={
                "id": "lab-cman",
                "product_id": "fbasecman",
                "title": "代理实验室",
                "host": "127.0.0.1",
                "port": port,
            },
        ).raise_for_status()
        first = client.post(
            "/api/v1/operations",
            json={
                "environment_id": "lab-cman",
                "action": "database.check",
                "submission_key": "click-1",
            },
        )
        assert first.status_code == 202
        task_id = first.json()["id"]
        assert (
            client.get("/api/v1/operations/" + task_id).json()["status"] == "SUCCEEDED"
        )
        event_types = [
            event["event_type"]
            for event in client.get("/api/v1/operations/" + task_id + "/events").json()
        ]
        assert event_types == [
            "step.started",
            "observation.captured",
            "operation.finished",
        ]
        retry = client.post(
            "/api/v1/operations",
            json={
                "environment_id": "lab-cman",
                "action": "database.check",
                "submission_key": "click-1",
            },
        )
        assert retry.json()["id"] == task_id
        assert (
            client.get("/api/v1/operations/" + task_id + "/events?after=2").json()[0][
                "sequence"
            ]
            == 3
        )


def test_mutating_action_requires_explicit_confirmation(tmp_path):
    config = settings_for(tmp_path)
    client = TestClient(create_app(config, enqueuer=lambda task_id: None))
    client.post(
        "/api/v1/environments",
        json={
            "id": "lab-db",
            "product_id": "fbase-database",
            "title": "数据库实验室",
            "host": "127.0.0.1",
            "port": 5432,
            "deployment_config": str(tmp_path / "pgcluster.yaml"),
            "deployment_target": "streaming.demo",
        },
    ).raise_for_status()
    result = client.post(
        "/api/v1/operations",
        json={
            "environment_id": "lab-db",
            "action": "deployment.create",
        },
    )
    assert result.status_code == 422
    assert client.get("/api/v1/operations").json() == []
