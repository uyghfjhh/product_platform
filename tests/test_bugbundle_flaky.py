"""§10 developer features: bug-bundle export, flaky tracking, reset action."""

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from platform_app.actions import ACTIONS, DEPLOYMENT_ACTIONS
from platform_app.api import create_app
from platform_app.config import Settings


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        pgcluster_root=tmp_path / "missing-pgcluster",
        regress_roots={
            "fbasecman": tmp_path / "missing-cman",
            "fbase-database": tmp_path / "missing-fbase",
        },
        license_key_dir=tmp_path / "keys",
        license_vendor="测试厂商",
    )


def make_env(client: TestClient, env_id: str = "lab-cman") -> None:
    payload = {
        "id": env_id,
        "product_id": "fbasecman",
        "title": "代理实验室",
        "host": "127.0.0.1",
        "port": 5432,
        "database_name": "postgres",
        "database_user": "postgres",
    }
    assert client.post("/api/v1/environments", json=payload).status_code == 201


def test_flaky_endpoint_aggregates_history(tmp_path):
    config = settings_for(tmp_path)
    client = TestClient(create_app(config, enqueuer=lambda task_id: None))
    make_env(client)
    history = config.regression_state_dir("fbasecman", "lab-cman")
    history.mkdir(parents=True)
    with (history / "history.jsonl").open("w", encoding="utf-8") as handle:
        for verdict in ("PASS", "FAIL", "PASS", "PASS", "FAIL"):
            handle.write(json.dumps(
                {"target": "suite.case_a", "verdict": verdict}) + "\n")
        handle.write(json.dumps(
            {"target": "suite.case_b", "verdict": "PASS"}) + "\n")
    response = client.get("/api/v1/environments/lab-cman/flaky")
    assert response.status_code == 200
    data = response.json()
    assert data["suite.case_a"]["flaky"] is True
    assert data["suite.case_a"]["recent"] == ["PASS", "FAIL", "PASS", "PASS", "FAIL"]
    assert data["suite.case_b"]["flaky"] is False
    assert client.get("/api/v1/environments/missing/flaky").status_code == 404


def test_bug_bundle_packs_environment_result_and_reports(tmp_path):
    config = settings_for(tmp_path)
    client = TestClient(create_app(config, enqueuer=lambda task_id: None))
    make_env(client)
    regression = config.artifact_dir("fbasecman", "lab-cman") / "runs" / "test-run"
    case_dir = regression / "cases" / "suite.case_a" / "artifacts" / "exec-1"
    case_dir.mkdir(parents=True)
    (case_dir / "steps.json").write_text(
        json.dumps({"steps": [{"title": "s1", "result": "FAIL"}]}),
        encoding="utf-8")
    (regression / "report.html").write_text("<html/>", encoding="utf-8")
    state = config.regression_state_dir("fbasecman", "lab-cman")
    state.mkdir(parents=True)
    (state / "history.jsonl").write_text(
        json.dumps({"target": "suite.case_a", "verdict": "FAIL"}) + "\n",
        encoding="utf-8")
    profile = config.profiles_dir / "lab-cman"
    profile.mkdir(parents=True)
    (profile / "pgcluster.yaml").write_text("hosts: {}\n", encoding="utf-8")

    response = client.get(
        "/api/v1/environments/lab-cman/results/suite.case_a/bundle")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
    assert "bundle.json" in names
    assert "environment.yaml" in names
    assert "profile/pgcluster.yaml" in names
    assert "runs/test-run/cases/suite.case_a/artifacts/exec-1/steps.json" in names
    assert "runs/test-run/report.html" in names
    assert "reports/case-history.json" in names

    whole = client.get("/api/v1/environments/lab-cman/results-bundle")
    assert whole.status_code == 200
    with zipfile.ZipFile(io.BytesIO(whole.content)) as archive:
        assert any(name.startswith("runs/test-run/cases/suite.case_a/")
                   for name in archive.namelist())

    assert client.get(
        "/api/v1/environments/missing/results/suite.case_a/bundle"
    ).status_code == 404
    assert client.get(
        "/api/v1/environments/lab-cman/results/bad target/bundle"
    ).status_code == 404


def test_deployment_reset_action_registered(tmp_path):
    assert "deployment.reset" in DEPLOYMENT_ACTIONS
    action = ACTIONS["deployment.reset"]
    assert action.changes_environment is True

    # Provider maps reset to the kill-strays + restart + health script.
    from platform_app.providers import DATABASE_CLUSTER_PROVIDER
    config = settings_for(tmp_path)
    pgcluster = config.pgcluster_root / "pgcluster"
    pgcluster.parent.mkdir(parents=True)
    pgcluster.write_text("#!/bin/sh\n", encoding="utf-8")
    deploy = config.profiles_dir / "lab-cman" / "pgcluster.yaml"
    deploy.parent.mkdir(parents=True)
    deploy.write_text("hosts: {}\n", encoding="utf-8")
    environment = {
        "id": "lab-cman", "product_id": "fbasecman",
        "deployment_config": str(deploy),
    }
    spec = DATABASE_CLUSTER_PROVIDER.lifecycle(
        config, environment, "deployment.reset", "mmr.cluster").command
    command = list(spec.command)
    assert command[1].endswith("pgcluster_reset.py")
    assert command[-1] == "mmr.cluster"
