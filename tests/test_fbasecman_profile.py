import subprocess
import sys
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from platform_app.api import create_app
from platform_app.config import Settings
from platform_app.fbasecman_profile import profile_paths
from platform_app.topology import configured_topology


def settings_for(tmp_path: Path) -> Settings:
    fly = Path("/home/postgres/fly_dev")
    return Settings(
        data_dir=tmp_path / "data",
        pgcluster_root=fly / "pgcluster",
        fbasecman_regress_root=fly / "fbasecman_dev" / "fbasecman_regress_v2",
        fbase_regress_root=fly / "postgresql_for_fbase_dev" / "fbase_regress",
        license_key_dir=tmp_path / "keys",
        license_vendor="测试厂商",
    )


def test_profile_maps_pgcluster_and_legacy_tests_to_same_topology(tmp_path):
    settings = settings_for(tmp_path)
    client = TestClient(create_app(settings, enqueuer=lambda task_id: None))
    client.post("/api/v1/environments", json={
        "id": "cman-profile", "product_id": "fbasecman", "title": "代理测试环境",
        "host": "192.168.1.24", "port": 15011,
    }).raise_for_status()
    created = client.post("/api/v1/environments/cman-profile/fbasecman-profile", json={
        "mmr1_port": 15011,
        "data_root": "/home/postgres/product_platform/fbasecman_regress/cman-profile",
        "license_file": "/home/postgres/license/license.dat",
    })
    assert created.status_code == 200, created.text
    profile, override = profile_paths(settings, "cman-profile")
    generated = yaml.safe_load(profile.read_text(encoding="utf-8"))
    assert generated["mmr_clusters"]["fbasecman_regress"]["extensions"] == ["fdd_mmr"]
    environment = client.get("/api/v1/environments/cman-profile").json()
    assert environment["deployment_config"] == str(profile)
    assert environment["deployment_target"] == "mmr.fbasecman_regress"
    graph = configured_topology(settings, environment)
    assert len(graph["nodes"]) == 14
    assert len(graph["edges"]) == 13
    assert {node["port"] for node in graph["nodes"]}.issuperset({15011, 15021, 15017, 15027})
    foreign = client.post("/api/v1/operations", json={
        "environment_id": "cman-profile", "action": "deployment.start",
        "target": "some_other_cluster", "acknowledge_change": True,
    })
    assert foreign.status_code == 422
    assert "不属于当前环境" in foreign.json()["detail"]
    check = subprocess.run([
        sys.executable, "-m", "platform_app.legacy_cman_runner",
        "--source", str(settings.fbasecman_regress_root),
        "--override", str(override), "--check-profile", "guc",
    ], capture_output=True, text=True, timeout=20)
    assert check.returncode == 0, check.stdout + check.stderr
    assert not (settings.data_dir / "legacy_cman" / "cman-profile" / "output" / "env" / "test_context.yaml").exists()
    case_task = client.post("/api/v1/operations", json={
        "environment_id": "cman-profile", "action": "tests.fbasecman",
        "target": "guc.search_path_reuse_sql_parse", "acknowledge_change": True,
    })
    assert case_task.status_code == 202, case_task.text
