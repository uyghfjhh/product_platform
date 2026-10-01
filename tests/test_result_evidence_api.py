import json
import os
import time

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
    output = settings.artifact_dir("fbase-database", "lab") / "runs" / "test-run" / "cases" / target
    evidence = output / "artifacts" / "execution" / "report.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("original verdict\n", encoding="utf-8")
    reference = "artifacts/execution/report.txt"
    (output / "result.json").write_text(json.dumps({
        "target": target, "execution_id": "execution", "verdict": "FAIL",
        "evidence": [reference],
    }), encoding="utf-8")
    app.state.store.results.put_result("fbase-database", "lab", target, "default", "FAIL",
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
    app.state.store.results.put_result("fbase-database", "lab", target, "default", "PASS",
                               "", str(outside))
    response = client.get(f"/api/v1/environments/lab/results/{target}/evidence")
    assert response.status_code == 404


def report_setup(tmp_path, target="mac.audit.log_access_restrictions"):
    settings = settings_for(tmp_path)
    app = create_app(settings, enqueuer=lambda task_id: None)
    client = TestClient(app)
    client.post("/api/v1/environments", json={
        "id": "lab", "product_id": "fbase-database", "title": "Lab",
        "host": "127.0.0.1", "port": 5432,
    }).raise_for_status()
    # Suite invocation paths must work as well as flat single-case directories.
    base = settings.artifact_dir("fbase-database", "lab") / "runs" / "test-run" / "cases" / "mac" / target
    base.mkdir(parents=True)
    payload = {"schema_version": "1.0", "target": target, "execution_id": "run",
               "verdict": "FAIL", "reason": "assertion failed", "evidence": [],
               "duration_seconds": 1.25}
    (base / "result.json").write_text(json.dumps(payload))
    return client, base, f"/api/v1/environments/lab/results/{target}"


def test_common_report_preserves_steps_and_exports_fact_verdict(tmp_path):
    client, base, route = report_setup(tmp_path)
    steps = [{"title": "检查审计", "result": "FAIL", "expected": "enabled",
              "actual": "disabled", "evidence": ["audit.log"]}]
    (base / "steps.json").write_text(json.dumps({"steps": steps}))
    # Rendered text cannot override the authoritative result verdict.
    (base / "report.txt").write_text("结论: PASS\n原始报告", encoding="utf-8")
    report = client.get(route + "/report")
    assert report.status_code == 200
    assert report.json()["verdict"] == "FAIL"
    assert report.json()["steps"] == steps
    assert report.json()["report"] == "结论: PASS\n原始报告"
    downloaded = client.get(route + "/report.txt")
    assert downloaded.text == report.json()["report"]
    assert "attachment" in downloaded.headers["content-disposition"]
    xml = client.get("/api/v1/environments/lab/reports/junit")
    assert xml.status_code == 200 and '<failure' in xml.text
    html = client.get("/api/v1/environments/lab/reports/html")
    assert html.status_code == 200 and "检查审计" in html.text
    assert client.get("/api/v1/environments/lab/reports/unknown").status_code == 404


def test_common_report_supports_mmr_result_without_sidecars(tmp_path):
    client, _, route = report_setup(tmp_path, "mmr.background.maintenance_lifecycle")
    response = client.get(route + "/report")
    assert response.status_code == 200
    assert response.json()["steps"] == [] and response.json()["report"] is None
    assert client.get(route + "/report.txt").status_code == 404
    assert client.get("/api/v1/environments/missing/reports/html").status_code == 404


def test_common_report_handles_corrupt_steps_and_rejects_symlinks(tmp_path):
    client, base, route = report_setup(tmp_path)
    (base / "steps.json").write_text("broken json")
    assert client.get(route + "/report").json()["warning"]
    outside = tmp_path / "private.txt"
    outside.write_text("secret")
    (base / "report.txt").symlink_to(outside)
    assert client.get(route + "/report").status_code == 404
    assert client.get(route + "/report.txt").status_code == 404


def test_common_report_rejects_archive_symlink_outside_output(tmp_path):
    client, base, route = report_setup(tmp_path)
    outside = tmp_path / "outside-run"
    base.rename(outside)
    base.symlink_to(outside, target_is_directory=True)
    assert client.get(route + "/report").status_code == 404


def test_report_and_result_list_select_same_latest_cli_run(tmp_path):
    client, base, route = report_setup(tmp_path)
    store = client.app.state.store
    target = "mac.audit.log_access_restrictions"
    old = base.parent / "old"
    old.mkdir()
    payload = json.loads((base / "result.json").read_text())
    payload["verdict"] = "PASS"
    (old / "result.json").write_text(json.dumps(payload))
    store.results.put_result("fbase-database", "lab", target, "default", "PASS", "", str(old))
    os.utime(base / "result.json", (time.time() + 1, time.time() + 1))
    result = client.get("/api/v1/environments/lab/results").json()[0]
    assert result["status"] == "FAIL"
    assert client.get(route + "/report").json()["verdict"] == "FAIL"


def test_exported_html_keeps_script_like_sql_output_as_data(tmp_path):
    client, base, _ = report_setup(tmp_path)
    (base / "steps.json").write_text(json.dumps({"steps": [
        {"title": "SQL output", "result": "FAIL", "actual": "</script><script>alert(1)</script>"},
    ]}))
    html = client.get("/api/v1/environments/lab/reports/html")
    assert html.status_code == 200
    assert "</script><script>alert(1)" not in html.text
    assert r"\u003c/script\u003e" in html.text
