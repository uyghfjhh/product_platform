import json
import time

from fastapi.testclient import TestClient

from platform_app.actions import command_for_task
from platform_app.api import create_app
from platform_app.cman_artifacts import (
    CaseProgressObserver,
    case_artifacts,
    case_log,
    sync_current_results,
)
from platform_app.fbasecman_profile import legacy_root

from test_fbasecman_profile import settings_for


def _write_case(settings, environment_id, target, status):
    suite, case = target.split(".", 1)
    directory = legacy_root(settings, environment_id) / "output" / "runs" / suite / case
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(json.dumps({"target": target, "status": status, "reason": "断言结果"}))
    (directory / "report.txt").write_text(
        f"用例: {target}\n结论: {status}\n测试开始时间: 2026-09-23 12:00:00\n"
        "测试结束时间: 2026-09-23 12:00:01\n验证目的:\n验证行为\n\n"
        "步骤 1: 执行检查\n实际执行: SELECT 1\n预期: 返回结果\n实际: 错误结果\n判定: FAIL\n\n"
        "检测项 1: 检查返回值\n预期: 1\n实际: 0\n判定: FAIL\n",
        encoding="utf-8",
    )
    (directory / "logs").mkdir()
    (directory / "logs" / "case.log").write_text("INFO start\nERROR failure\n", encoding="utf-8")
    return directory


def test_report_endpoints_read_environment_isolated_artifacts(tmp_path):
    settings = settings_for(tmp_path)
    client = TestClient(create_app(settings, enqueuer=lambda task_id: None))
    client.post("/api/v1/environments", json={
        "id": "lab-cman", "product_id": "fbasecman", "title": "测试环境",
        "host": "127.0.0.1", "port": 5432,
    }).raise_for_status()
    root = legacy_root(settings, "lab-cman")
    root.mkdir(parents=True)
    (root / "regress.yaml").write_text((settings.fbasecman_regress_root / "regress.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    _write_case(settings, "lab-cman", "ha_commands.sample", "FAIL")

    response = client.get("/api/v1/fbasecman/cases/ha_commands.sample/artifacts?environment_id=lab-cman")
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["summary"]["status"] == "FAIL"
    assert value["parsed"]["steps"][0]["title"].startswith("步骤 1")
    assert value["logs"][0]["name"] == "logs/case.log"
    assert client.get("/api/v1/fbasecman/case-statuses?environment_id=lab-cman").json()["ha_commands.sample"]["status"] == "FAIL"
    assert client.get("/api/v1/fbasecman/cases/ha_commands.sample/logs?filename=logs/case.log&environment_id=lab-cman").json()["lines"][-1] == "ERROR failure"
    assert client.get("/api/v1/fbasecman/cases/ha_commands.sample/logs?filename=../private.log&environment_id=lab-cman").status_code in {404, 422}

    junit = client.get("/api/v1/fbasecman/environments/lab-cman/reports/junit")
    assert junit.status_code == 200, junit.text
    assert 'failures="1"' in junit.text
    html = client.get("/api/v1/fbasecman/environments/lab-cman/reports/html")
    assert html.status_code == 200, html.text[:500]
    assert "ha_commands.sample" in html.text


def test_current_result_sync_keeps_other_environment_and_rejects_old_fallback(tmp_path):
    settings = settings_for(tmp_path)
    store = create_app(settings, enqueuer=lambda task_id: None).state.store
    _write_case(settings, "lab-a", "guc.case_one", "PASS")
    environment = {"id": "lab-a", "product_id": "fbasecman"}
    assert sync_current_results(store, settings, environment, "guc", "2020-01-01T00:00:00+00:00") == 1
    assert store.list_results("lab-a")[0]["status"] == "PASS"
    assert case_artifacts(settings, "guc.case_one", "lab-b")["available"] is False
    assert case_log(settings, "guc.case_one", "logs/case.log", environment_id="lab-a")["lines"][-1] == "ERROR failure"
    try:
        command_for_task(settings, environment, "tests.fbasecman", "guc.case_one", {})
    except RuntimeError as exc:
        assert "pgcluster" in str(exc)
    else:
        raise AssertionError("fbasecman 测试不应回退到旧部署环境")


def test_running_step_updates_emit_ordered_events_once(tmp_path):
    settings = settings_for(tmp_path)
    store = create_app(settings, enqueuer=lambda task_id: None).state.store
    store.put_environment({
        "id": "lab-a", "product_id": "fbasecman", "title": "隔离环境",
        "host": "127.0.0.1", "port": 15432, "database_name": "postgres",
        "database_user": "postgres", "deployment_config": None,
        "deployment_target": None,
    })
    task = store.create_task("lab-a", "tests.fbasecman", "guc.sample", {}, None)
    started_at = time.time()
    directory = legacy_root(settings, "lab-a") / "output" / "runs" / "guc" / "sample"
    directory.mkdir(parents=True)
    steps = directory / "steps.json"
    steps.write_text(json.dumps({"steps": [{
        "title": "检查路由", "status": "RUNNING", "expected": "可读",
    }]}), encoding="utf-8")

    observer = CaseProgressObserver(settings, "lab-a", "guc.sample", started_at)
    observer.poll(store, task["id"])
    observer.poll(store, task["id"])
    assert [event["event_type"] for event in store.list_events(task["id"])] == ["step.started"]

    steps.write_text(json.dumps({"steps": [{
        "title": "检查路由", "status": "PASS", "expected": "可读", "actual": "可读",
    }]}), encoding="utf-8")
    observer.poll(store, task["id"])
    observer.poll(store, task["id"])
    events = store.list_events(task["id"])
    assert [event["sequence"] for event in events] == [1, 2]
    assert events[1]["event_type"] == "assertion.checked"
    assert events[1]["payload"]["actual"] == "可读"
