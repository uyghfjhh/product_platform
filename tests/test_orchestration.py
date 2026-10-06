from datetime import UTC, datetime, timedelta
from threading import Event, Thread

import pytest
from platform_app.filestore import FileStore
from platform_app.notifications import deliver
from platform_app.orchestration import Orchestrator


@pytest.fixture
def store(tmp_path):
    value = FileStore(tmp_path / "data")
    value.environments.put_environment(
        {
            "id": "lab",
            "product_id": "demo",
            "title": "lab",
            "host": "127.0.0.1",
            "port": 7000,
        }
    )
    return value


class Operations:
    def __init__(self, store):
        self.store = store
        from types import SimpleNamespace

        self.settings = SimpleNamespace(runtime_dir=store.runtime_dir)

    def submit(self, item):
        task, _ = self.store.tasks.create_task_once(
            item.environment_id,
            item.action,
            item.target or "test",
            item.parameters,
            item.submission_key,
        )
        return task


def pipeline(store):
    return store.orchestration.put(
        "pipelines",
        {
            "title": "test",
            "environment_id": "lab",
            "steps": [
                {
                    "action": "tests.demo",
                    "target": "first",
                    "parameters": {},
                    "deployment_plan_id": None,
                },
                {
                    "action": "tests.demo",
                    "target": "second",
                    "parameters": {},
                    "deployment_plan_id": None,
                },
            ],
        },
    )


def finish(store, identity, status):
    store.tasks.transition_task(identity, ("QUEUED",), "RUNNING")
    store.tasks.finish_task(identity, ("RUNNING",), status, "fixture")


def test_pipeline_snapshot_sequential_execution_and_failure_stops_chain(store):
    definition = pipeline(store)
    engine = Orchestrator(store, Operations(store))
    run = engine.start(definition["id"], True)
    store.orchestration.put("pipelines", {**definition, "steps": []}, definition["id"])
    engine.runs()
    row = store.orchestration.get("runs", run["id"])
    assert len(row["tasks"]) == 1
    engine.runs()
    assert len(store.tasks.list_tasks()) == 1
    finish(store, row["tasks"][0], "FAILED")
    engine.runs()
    assert store.orchestration.get("runs", run["id"])["status"] == "FAILED"
    assert len(store.tasks.list_tasks()) == 1


def test_pipeline_cancel_does_not_submit_next_step(store):
    engine = Orchestrator(store, Operations(store))
    run = engine.start(pipeline(store)["id"], True)
    engine.runs()
    row = store.orchestration.get("runs", run["id"])
    finish(store, row["tasks"][0], "SUCCEEDED")
    engine.cancel(run["id"])
    engine.runs()
    assert store.orchestration.get("runs", run["id"])["status"] == "CANCELLED"
    assert len(store.tasks.list_tasks()) == 1


def test_crash_between_task_creation_and_run_checkpoint_is_idempotent(
    store, monkeypatch
):
    engine = Orchestrator(store, Operations(store))
    run = engine.start(pipeline(store)["id"], True)
    original = store.orchestration.put

    def fail(*args, **kwargs):
        if args[0] == "runs":
            raise RuntimeError("simulated process failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store.orchestration, "put", fail)
    with pytest.raises(RuntimeError):
        engine.runs()
    monkeypatch.setattr(store.orchestration, "put", original)
    engine.runs()
    assert len(store.tasks.list_tasks()) == 1
    assert len(store.orchestration.get("runs", run["id"])["tasks"]) == 1


def test_schedule_is_durable_and_does_not_overlap_itself(store):
    definition = pipeline(store)
    engine = Orchestrator(store, Operations(store))
    schedule = store.orchestration.put(
        "schedules",
        {
            "pipeline_id": definition["id"],
            "enabled": True,
            "interval_seconds": 60,
            "next_run_at": (datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
        },
    )
    engine.schedules()
    engine.schedules()
    assert len(store.orchestration.list("runs")) == 1
    schedule["next_run_at"] = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    store.orchestration.put("schedules", schedule, schedule["id"])
    engine.schedules()
    assert len(store.orchestration.list("runs")) == 1


def test_notification_delivery_is_opt_in_and_idempotent(store):
    event = store.orchestration.put(
        "notifications",
        {
            "task_id": "task",
            "environment_id": "lab",
            "action": "tests.demo",
            "target": "case",
            "status": "SUCCEEDED",
        },
    )
    hook = store.orchestration.put(
        "webhooks",
        {"enabled": False, "url": "https://example.invalid", "credential_env": ""},
    )
    calls = []
    deliver(store.orchestration, lambda h, e: calls.append(e["id"]))
    assert calls == []
    hook["enabled"] = True
    store.orchestration.put("webhooks", hook, hook["id"])
    deliver(store.orchestration, lambda h, e: calls.append(e["id"]))
    deliver(store.orchestration, lambda h, e: calls.append(e["id"]))
    assert calls == [event["id"]]


def test_shared_transaction_is_reentrant_and_excludes_other_threads(store):
    entered = Event()

    def other():
        with store.backend.transaction():
            entered.set()

    with store.backend.transaction():
        store.orchestration.put("pipelines", {"title": "nested"})
        worker = Thread(target=other)
        worker.start()
        assert not entered.wait(0.05)
    worker.join(timeout=1)
    assert entered.is_set()


def test_notification_outbox_survives_index_window_and_restart(store):
    old = store.tasks.create_task("lab", "tests.demo", "old", {}, None)
    for index in range(110):
        store.tasks.create_task("other-" + str(index), "tests.demo", "new", {}, None)
    finish(store, old["id"], "SUCCEEDED")
    assert old["id"] not in {row["id"] for row in store.tasks.list_tasks(limit=100)}
    marker = store.root / "notification-outbox" / (old["id"] + ".json")
    marker.unlink()  # crash after task commit, before its delivery projection
    recovered = FileStore(
        store.root, runtime_dir=store.runtime_dir, logs_dir=store.logs_dir
    )
    Orchestrator(recovered, Operations(recovered)).notifications()
    assert (
        recovered.orchestration.get("notifications", old["id"])["status"] == "SUCCEEDED"
    )
    assert recovered.tasks.get_task(old["id"])["notification_recorded"] is True
    assert not marker.exists()
