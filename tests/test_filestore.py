import json

import pytest

from platform_app.filestore import ConflictError, FileStore


def _env(env_id="lab", **overrides):
    env = {
        "id": env_id, "product_id": "fbasecman", "title": "Lab",
        "host": "127.0.0.1", "port": 5432, "database_name": "postgres",
        "database_user": "postgres", "deployment_config": None,
        "deployment_target": None,
    }
    env.update(overrides)
    return env


def test_environment_lifecycle(tmp_path):
    store = FileStore(tmp_path)
    store.put_environment(_env("lab"))
    assert store.get_environment("lab")["title"] == "Lab"
    assert [e["id"] for e in store.list_environments()] == ["lab"]
    with pytest.raises(ConflictError):
        store.put_environment(_env("lab"))
    store.update_environment("lab", _env("lab", title="Renamed"))
    assert store.get_environment("lab")["title"] == "Renamed"
    assert store.delete_environment("lab") is True
    assert store.get_environment("lab") is None
    assert store.delete_environment("lab") is False


def test_binding_rules(tmp_path):
    store = FileStore(tmp_path)
    store.put_environment(_env("lab"))
    store.put_environment(_env("other", product_id="fbase-database"))
    store.put_regression_binding("fbasecman", "default", "lab")
    assert store.get_regression_binding("fbasecman", "default")["environment_id"] == "lab"
    assert store.get_regression_binding("fbasecman", "nightly") is None
    # 跨产品环境拒绝
    with pytest.raises(ConflictError):
        store.put_regression_binding("fbase-database", "default", "lab")
    # 环境上有活动任务时禁止换绑/解绑
    store.create_task("lab", "tests.fbasecman", "suite.case", {}, None)
    with pytest.raises(ConflictError):
        store.put_regression_binding("fbasecman", "default", "other")
    with pytest.raises(ConflictError):
        store.delete_regression_binding("fbasecman", "default")
    with pytest.raises(ConflictError):
        store.delete_environment("lab")


def test_task_lifecycle(tmp_path):
    store = FileStore(tmp_path)
    store.put_environment(_env("lab"))
    task, created = store.create_task_once(
        "lab", "tests.fbasecman", "suite.case", {"a": 1}, "key-1")
    assert created and task["status"] == "QUEUED"
    again, created = store.create_task_once(
        "lab", "tests.fbasecman", "suite.case", {"a": 1}, "key-1")
    assert not created and again["id"] == task["id"]
    with pytest.raises(ConflictError):
        store.create_task_once("lab", "tests.fbasecman", "other", {}, "key-1")
    assert [t["id"] for t in store.unfinished_tasks()] == [task["id"]]

    assert store.transition_task(task["id"], ("QUEUED",), "RUNNING", process_id=42)
    assert not store.transition_task(task["id"], ("QUEUED",), "RUNNING")
    store.add_event(task["id"], "log", {"line": "a"})
    store.add_event(task["id"], "log", {"line": "b"})
    assert [e["payload"]["line"] for e in store.list_events(task["id"])] == ["a", "b"]
    assert [e["payload"]["line"] for e in store.list_events(task["id"], after=1)] == ["b"]

    assert store.request_cancel(task["id"]) is True
    assert store.get_task(task["id"])["status"] == "CANCELLING"
    assert store.finish_task(task["id"], ("CANCELLING",), "CANCELLED", "手动取消")
    finished = store.get_task(task["id"])
    assert finished["status"] == "CANCELLED" and finished["finished_at"]
    events = store.list_events(task["id"])
    assert events[-1]["event_type"] == "operation.finished"
    assert store.unfinished_tasks() == []


def test_result_and_diagnosis(tmp_path):
    store = FileStore(tmp_path)
    store.put_environment(_env("lab"))
    store.put_result("fbasecman", "lab", "suite.case", "default",
                     "PASS", None, "/art/1")
    store.put_result("fbasecman", "lab", "suite.case", "default",
                     "FAIL", "boom", "/art/2")
    assert len(store.list_results("lab")) == 1
    result = store.get_result("fbasecman", "lab", "suite.case")
    assert result["status"] == "FAIL" and result["reason"] == "boom"

    store.put_diagnosis(result, "hash1", "model", {"verdict": "bad"})
    diagnosis = store.get_diagnosis("fbasecman", "lab", "suite.case")
    assert diagnosis["content"] == {"verdict": "bad"}
    assert diagnosis["stale"] is False
    store.put_result("fbasecman", "lab", "suite.case", "default",
                     "PASS", None, "/art/3")
    assert store.get_diagnosis("fbasecman", "lab", "suite.case")["stale"] is True
    assert store.get_diagnosis("fbasecman", "lab", "missing") is None


def test_environment_delete_cascades(tmp_path):
    store = FileStore(tmp_path)
    store.put_environment(_env("lab"))
    task, _ = store.create_task_once("lab", "tests.fbasecman", "c", {}, None)
    store.finish_task(task["id"], ("QUEUED",), "SUCCEEDED", None)
    store.put_result("fbasecman", "lab", "suite.case", "default", "PASS", None, None)
    assert store.delete_environment("lab") is True
    assert store.get_task(task["id"]) is None
    assert store.list_results("lab") == []
    assert store.list_events(task["id"]) == []


def test_task_meta_is_json_and_events_jsonl(tmp_path):
    store = FileStore(tmp_path)
    store.put_environment(_env("lab"))
    task = store.create_task("lab", "tests.fbasecman", "suite.case", {"a": 1}, None)
    meta = json.loads((tmp_path / "tasks" / task["id"] / "meta.json").read_text())
    assert meta["parameters"] == '{"a": 1}'
    store.add_event(task["id"], "log", {"x": 1})
    lines = (tmp_path / "tasks" / task["id"] / "events.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["sequence"] == 1


def test_concurrent_writers(tmp_path):
    import multiprocessing

    def writer(data_dir, n):
        store = FileStore(data_dir)
        env_id = f"env-{n}"
        store.put_environment(_env(env_id))
        task = store.create_task(env_id, "a", "t", {}, None)
        for i in range(5):
            store.add_event(task["id"], "log", {"n": n, "i": i})

    procs = [multiprocessing.Process(target=writer, args=(str(tmp_path), n))
             for n in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    store = FileStore(tmp_path)
    assert len(store.list_environments()) == 4
    for task in store.list_tasks():
        assert len(store.list_events(task["id"])) == 5
