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
    enqueued = []

    def enqueue(task_id):
        enqueued.append(task_id)
        run_task(app.state.store, config, task_id)

    app = create_app(config, enqueuer=enqueue)
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
            "scene.topology.configured",
            "scene.action.started",
            "step.started",
            "scene.entity.observed",
            "observation.captured",
            "scene.action.finished",
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
        assert enqueued == [task_id]
        assert (
            client.get("/api/v1/operations/" + task_id + "/events?after=6").json()[0][
                "sequence"
            ]
            == 7
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


def test_environment_delete_waits_for_tasks_and_removes_their_events(tmp_path):
    config = settings_for(tmp_path)
    app = create_app(config, enqueuer=lambda task_id: None)
    client = TestClient(app)
    client.post("/api/v1/environments", json={
        "id": "lab", "product_id": "fbasecman", "title": "隔离环境",
        "host": "127.0.0.1", "port": 5432,
    }).raise_for_status()
    task = app.state.store.create_task("lab", "database.check", "lab", {}, None)
    app.state.store.add_event(task["id"], "observation.captured", {"state": "ready"})

    assert client.delete("/api/v1/environments/lab").status_code == 409
    assert app.state.store.get_environment("lab") is not None

    app.state.store.transition_task(task["id"], ("QUEUED",), "SUCCEEDED")
    assert client.delete("/api/v1/environments/lab").status_code == 200
    assert app.state.store.get_task(task["id"]) is None
    assert app.state.store.list_events(task["id"]) == []


def test_product_test_action_requires_matching_environment(tmp_path):
    config = settings_for(tmp_path)
    client = TestClient(create_app(config, enqueuer=lambda task_id: None))
    client.post("/api/v1/environments", json={
        "id": "cman", "product_id": "fbasecman", "title": "代理环境",
        "host": "127.0.0.1", "port": 5432,
    }).raise_for_status()
    response = client.post("/api/v1/operations", json={
        "environment_id": "cman", "action": "tests.fbase", "target": "mmr",
        "parameters": {"cluster": "mmr"}, "acknowledge_change": True,
    })
    assert response.status_code == 422
    assert client.get("/api/v1/operations").json() == []


def test_test_result_is_published_before_task_finishes(tmp_path, monkeypatch):
    from platform_app import actions

    config = settings_for(tmp_path)
    store = create_app(config, enqueuer=lambda task_id: None).state.store
    store.put_environment({
        "id": "database", "product_id": "fbase-database", "title": "数据库环境",
        "host": "127.0.0.1", "port": 5432, "database_name": "postgres",
        "database_user": "postgres", "deployment_config": None,
        "deployment_target": None,
    })
    task = store.create_task("database", "tests.fbase", "mmr", {"cluster": "mmr"}, None)
    monkeypatch.setattr(actions, "command_for_task", lambda *_: (["true"], tmp_path))
    monkeypatch.setattr(actions, "_run_command", lambda *_args, **_kwargs: (True, "执行完成"))
    original_put_result = store.put_result
    statuses_at_publication = []

    def record_publication(*args):
        statuses_at_publication.append(store.get_task(task["id"])["status"])
        original_put_result(*args)

    monkeypatch.setattr(store, "put_result", record_publication)
    run_task(store, config, task["id"])

    assert statuses_at_publication == ["RUNNING"]
    assert store.get_task(task["id"])["status"] == "SUCCEEDED"
    assert store.list_results("database")[0]["status"] == "PASS"
    assert store.list_events(task["id"])[-1]["event_type"] == "operation.finished"
