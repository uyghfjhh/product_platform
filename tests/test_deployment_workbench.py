"""Deployment workbench failure boundaries; no real database operations."""

import socket
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from platform_app.api import create_app
from platform_app.config import ROOT
from platform_app.deployment.models import DeploymentSpec
from platform_app.deployment.probe_agent import run as probe_run
from platform_app.deployment.workbench import (
    Workbench,
    compile_spec,
    worker_environment,
)
from test_api import settings_for


@pytest.fixture
def workbench(tmp_path):
    engine = ROOT.parent / "pgcluster"
    if not engine.is_dir():
        pytest.skip("Integration contract requires sibling pgcluster checkout")
    settings = replace(settings_for(tmp_path), pgcluster_root=engine, regress_roots={})
    home = tmp_path / "installation"
    (home / "bin").mkdir(parents=True)
    for name in ("postgres", "pg_ctl", "psql", "pg_basebackup", "pg_config"):
        tool = home / "bin" / name
        tool.write_text('#!/bin/sh\necho "PostgreSQL 15.15"\n')
        tool.chmod(0o755)
    for name in ("fbase_mac", "fb_license", "fdd_mmr", "citus"):
        control = home / "share/extension" / (name + ".control")
        control.parent.mkdir(parents=True, exist_ok=True)
        control.write_text("fixture")
        library = home / "lib" / (name + ".so")
        library.parent.mkdir(parents=True, exist_ok=True)
        library.write_text("fixture")
    license_file = tmp_path / "license.dat"
    license_file.write_text("fixture")
    queued = []
    app = create_app(settings, enqueuer=queued.append)
    spec = {
        "title": "部署验收",
        "product_id": "fbase-database",
        "template_id": "mac",
        "mode": "new",
        "host": "127.0.0.1",
        "home": str(home),
        "data_root": str(tmp_path / "instances"),
        "license_file": str(license_file),
        "base_port": 45120,
    }
    return TestClient(app), Workbench(settings, app.state.store), spec, queued


def create_plan(workbench, spec=None):
    client, service, default, _ = workbench
    created = client.post("/api/v1/deployment/drafts", json={"spec": spec or default})
    assert created.status_code == 201, created.text
    draft = created.json()
    result = client.post(f"/api/v1/deployment/drafts/{draft['id']}/plan")
    assert result.status_code == 200, result.text
    return draft, result.json()


@pytest.mark.parametrize(
    "product,template,port,count",
    [
        ("fbase-database", "mac", 45120, 3),
        ("fbase-database", "mmr", 45220, 6),
        ("fbasecman", "cman", 45320, 14),
    ],
)
def test_three_templates_generate_and_validate(
    workbench, product, template, port, count
):
    client, _, spec, _ = workbench
    _, plan = create_plan(
        workbench,
        {**spec, "product_id": product, "template_id": template, "base_port": port},
    )
    assert len(plan["operations"]) == count
    assert plan["ready"], plan["checks"]
    assert all(
        node["data_dir"].startswith(spec["data_root"]) for node in plan["operations"]
    )
    assert (
        client.get(
            f"/api/v1/deployment/plans/{plan['id']}/files/pgcluster.yaml"
        ).status_code
        == 200
    )


def test_generated_id_draft_resume_and_automatic_environment_link(workbench):
    client, service, spec, _ = workbench
    draft, plan = create_plan(workbench)
    assert draft["id"].startswith("env-")
    assert client.get("/api/v1/deployment/drafts").json()[0]["spec"] == draft["spec"]
    linked = client.post(f"/api/v1/deployment/plans/{plan['id']}/associate")
    assert linked.status_code == 200, linked.text
    environment = service.store.get_environment(draft["id"])
    assert environment["deployment_config"] == plan["config_path"]
    assert Path(environment["deployment_config"]).is_file()
    assert (
        service.settings.data_dir / "profiles" / draft["id"] / "regress.override.yaml"
    ).is_file()
    import importlib

    provider = importlib.import_module("products.fbase-database.provider")
    context = provider._cluster_context(
        service.settings, "mac", environment=environment
    )
    assert context["nodes"]["mac_primary"]["port"] == spec["base_port"]
    assert context["nodes"]["mac_primary"]["data_dir"].startswith(spec["data_root"])


def test_plan_invalidated_by_draft_or_file_edits(workbench):
    client, _, spec, _ = workbench
    draft, plan = create_plan(workbench)
    changed = client.put(
        f"/api/v1/deployment/drafts/{draft['id']}",
        json={"expected_revision": 1, "spec": {**spec, "title": "改名"}},
    )
    assert changed.status_code == 200
    assert (
        client.post(f"/api/v1/deployment/plans/{plan['id']}/associate").status_code
        == 409
    )
    assert (
        client.put(
            f"/api/v1/deployment/drafts/{draft['id']}",
            json={"expected_revision": 1, "spec": spec},
        ).status_code
        == 409
    )
    _, fresh = create_plan(workbench, {**spec, "base_port": 45420})
    Path(fresh["config_path"]).write_text("tampered")
    assert client.get(f"/api/v1/deployment/plans/{fresh['id']}").status_code == 409


def test_new_plan_rejects_existing_data_and_port_collisions(workbench):
    client, _, spec, _ = workbench
    existing = Path(spec["data_root"]) / "mac_primary"
    existing.mkdir(parents=True)
    (existing / "PG_VERSION").write_text("15")
    listener = socket.socket()
    listener.bind(("127.0.0.1", spec["base_port"]))
    listener.listen()
    try:
        _, plan = create_plan(workbench)
        assert not plan["ready"]
        assert any(
            not check["ok"] and "端口" in check["title"] for check in plan["checks"]
        )
        assert (
            client.post(f"/api/v1/deployment/plans/{plan['id']}/associate").status_code
            == 422
        )
    finally:
        listener.close()


def test_apply_requires_review_and_freezes_task_config(workbench):
    client, service, _, queued = workbench
    draft, plan = create_plan(workbench)
    route = f"/api/v1/deployment/plans/{plan['id']}/apply"
    assert client.post(route, json={}).status_code == 422
    applied = client.post(route, json={"acknowledge_change": True})
    assert applied.status_code == 202, applied.text
    assert queued == [applied.json()["id"]]
    import json

    task = service.store.get_task(applied.json()["id"])
    snapshot = json.loads(task["parameters"])["_deployment_snapshot"]
    assert snapshot["sha256"] == plan["config_sha256"]
    environment = service.store.get_environment(draft["id"])
    selected = worker_environment(
        service.settings, service.store, environment, snapshot
    )
    assert selected["deployment_config"] == plan["config_path"]
    Path(plan["config_path"]).write_text("changed before worker")
    with pytest.raises(Exception, match="变化"):
        worker_environment(service.settings, service.store, environment, snapshot)


def test_adoption_is_read_only_and_import_target_selection(workbench):
    client, service, spec, _ = workbench
    config, target, _ = compile_spec(
        service.settings, DeploymentSpec(**spec), "fixture"
    )
    for node in config["instances"].values():
        path = Path(node["data_dir"])
        path.mkdir(parents=True)
        (path / "PG_VERSION").write_text("15")
    text = yaml.safe_dump(config)
    targets = client.post(
        "/api/v1/deployment/import-targets", json={"source_yaml": text}
    )
    assert targets.status_code == 200
    assert target in {row["value"] for row in targets.json()}
    _, plan = create_plan(
        workbench, {**spec, "mode": "import", "source_yaml": text, "target": target}
    )
    assert plan["ready"], plan["checks"]
    assert plan["action"] == "deployment.health"
    assert (
        client.post(f"/api/v1/deployment/plans/{plan['id']}/apply", json={}).status_code
        == 202
    )


def test_node_override_rejects_duplicate_ports_and_protected_parameters(workbench):
    client, service, spec, _ = workbench
    cfg, _, _ = compile_spec(service.settings, DeploymentSpec(**spec), "fixture")
    nodes = [
        {"name": name, "port": spec["base_port"], "data_dir": row["data_dir"]}
        for name, row in cfg["instances"].items()
    ]
    draft = client.post(
        "/api/v1/deployment/drafts", json={"spec": {**spec, "nodes": nodes}}
    ).json()
    assert (
        client.post(f"/api/v1/deployment/drafts/{draft['id']}/plan").status_code == 422
    )
    draft = client.post(
        "/api/v1/deployment/drafts",
        json={"spec": {**spec, "parameters": {"shared_preload_libraries": "wrong"}}},
    ).json()
    assert (
        client.post(f"/api/v1/deployment/drafts/{draft['id']}/plan").status_code == 422
    )


def test_install_discovery_reads_previous_startup_without_database_changes(workbench):
    _, _, spec, _ = workbench
    path = Path(spec["data_root"])
    path.mkdir()
    (path / "postmaster.opts").write_text(
        str(Path(spec["home"]) / "bin/postgres") + ' "-D" "ignored"'
    )
    discovered = probe_run(
        {"operation": "discover", "home": spec["home"], "data_dir": str(path)}
    )
    chosen = next(
        item for item in discovered["installations"] if item["home"] == spec["home"]
    )
    assert chosen["complete"]
    assert "历史启动参数" in chosen["sources"]
    assert not (path / "PG_VERSION").exists()


def test_worker_checks_deployment_health_before_succeeding(workbench, monkeypatch):
    client, service, _, _ = workbench
    _, plan = create_plan(workbench)
    applied = client.post(
        f"/api/v1/deployment/plans/{plan['id']}/apply",
        json={"acknowledge_change": True},
    )
    commands = []

    def simulate(store, task_id, command, cwd, changes_environment, **kwargs):
        commands.append(command)
        return len(commands) == 1, "health failed"

    monkeypatch.setattr("platform_app.actions._run_command", simulate)
    monkeypatch.setattr(
        "platform_app.actions.emit_configured_scene", lambda *args: None
    )
    monkeypatch.setattr(
        "platform_app.actions.emit_pgcluster_status", lambda *args: None
    )
    from platform_app.actions import run_task

    run_task(service.store, service.settings, applied.json()["id"])
    assert "create" in commands[0] and "health" in commands[1]
    task = service.store.get_task(applied.json()["id"])
    assert task["status"] == "FAILED" and "健康验收失败" in task["reason"]


def test_symlinked_data_root_inside_platform_is_rejected(workbench, tmp_path):
    client, _, spec, _ = workbench
    inside = ROOT / "data" / "symlinked-instances"
    inside.mkdir(parents=True, exist_ok=True)
    link = tmp_path / "linked-root"
    link.symlink_to(inside)
    # The literal path passes compile_spec; only the resolved canonical path
    # lands inside the platform checkout, which inspect_plan must reject.
    _, plan = create_plan(workbench, {**spec, "data_root": str(link)})
    assert not plan["ready"]
    assert any(
        not check["ok"] and check["title"] == "实际数据目录位于平台项目内"
        for check in plan["checks"]
    )
    assert (
        client.post(
            f"/api/v1/deployment/plans/{plan['id']}/associate"
        ).status_code
        == 422
    )


def test_symlinked_node_dirs_resolving_together_are_rejected(workbench, tmp_path):
    client, service, spec, _ = workbench
    shared = tmp_path / "shared-actual"
    shared.mkdir()
    links = []
    for name in ("link-a", "link-b"):
        link = tmp_path / name
        link.symlink_to(shared)
        links.append(link)
    cfg, _, _ = compile_spec(service.settings, DeploymentSpec(**spec), "fixture")
    names = list(cfg["instances"])
    nodes = [
        {
            "name": name,
            "port": cfg["instances"][name]["port"],
            "data_dir": cfg["instances"][name]["data_dir"],
        }
        for name in names
    ]
    nodes[0]["data_dir"] = str(links[0] / "node")
    nodes[1]["data_dir"] = str(links[1] / "node")
    draft = client.post(
        "/api/v1/deployment/drafts", json={"spec": {**spec, "nodes": nodes}}
    ).json()
    result = client.post(f"/api/v1/deployment/drafts/{draft['id']}/plan")
    assert result.status_code == 200, result.text
    plan = result.json()
    assert not plan["ready"]
    assert any(
        not check["ok"] and check["title"] == "实际数据目录冲突"
        for check in plan["checks"]
    )


def _cman_compiled(service, spec, tmp_path, port):
    """Compile the cman template and materialize node PG_VERSION files."""
    from products.fbasecman.deployment.templates import compile_template

    cman_spec = DeploymentSpec(
        **{
            **spec,
            "product_id": "fbasecman",
            "template_id": "cman",
            "base_port": port,
            "data_root": str(tmp_path / "cman-data"),
        }
    )
    config, target, _ = compile_template(service.settings, cman_spec, "fixture")
    for node in config["instances"].values():
        path = Path(node["data_dir"])
        path.mkdir(parents=True)
        (path / "PG_VERSION").write_text("15")
    return config, target


def test_cman_import_rederives_regression_files(workbench, tmp_path):
    client, service, spec, _ = workbench
    config, target = _cman_compiled(service, spec, tmp_path, 45620)
    text = yaml.safe_dump(config, sort_keys=False)
    _, plan = create_plan(
        workbench,
        {
            **spec,
            "product_id": "fbasecman",
            "template_id": "cman",
            "mode": "import",
            "source_yaml": text,
            "target": target,
        },
    )
    assert plan["ready"], plan["checks"]
    assert "regress.override.yaml" in plan["files"]
    assert "regress.yaml" in plan["files"]
    override = yaml.safe_load(
        (
            Path(plan["config_path"]).parent / "regress.override.yaml"
        ).read_text()
    )
    primary = config["instances"]["test_mmr1"]
    standby = config["instances"]["test_mmr1_s1"]
    assert override["database"]["ports"]["mmr1"] == primary["port"]
    assert override["database"]["ports"]["mmr1_standby1"] == standby["port"]
    assert override["database"]["ports"]["mmr1_standbys"][0] == standby["port"]
    assert override["database"]["mmr_data_root"] == str(tmp_path / "cman-data")
    assert override["database"]["mmr_postgres_dir"] == spec["home"]
    # cman 编译产物的 mmr 集群声明了 citus 扩展。
    assert override["database"]["enable_citus"] is True
    merged = yaml.safe_load(
        (Path(plan["config_path"]).parent / "regress.yaml").read_text()
    )
    assert merged["database"]["ports"]["mmr1"] == primary["port"]
    assert merged["fbasecman"]["fbasecman_bin"]


def test_cman_import_rejects_nonconforming_layout(workbench, tmp_path):
    client, service, spec, _ = workbench
    config, target = _cman_compiled(service, spec, tmp_path, 45640)
    moved = tmp_path / "elsewhere" / "renamed-node"
    moved.mkdir(parents=True)
    (moved / "PG_VERSION").write_text("15")
    config["instances"]["test_mmr1_s1"]["data_dir"] = str(moved)
    draft = client.post(
        "/api/v1/deployment/drafts",
        json={
            "spec": {
                **spec,
                "product_id": "fbasecman",
                "template_id": "cman",
                "mode": "import",
                "source_yaml": yaml.safe_dump(config),
                "target": target,
            }
        },
    ).json()
    result = client.post(f"/api/v1/deployment/drafts/{draft['id']}/plan")
    assert result.status_code == 422
    assert "节点名" in result.json()["detail"]


def test_cman_node_overrides_restricted_to_conventional_layout(workbench, tmp_path):
    client, service, spec, _ = workbench
    cfg, _ = _cman_compiled(service, spec, tmp_path, 45660)
    nodes = [
        {
            "name": name,
            "port": row["port"],
            "data_dir": row["data_dir"],
        }
        for name, row in cfg["instances"].items()
    ]
    nodes[0]["data_dir"] = str(tmp_path / "custom" / "dir")
    draft = client.post(
        "/api/v1/deployment/drafts",
        json={
            "spec": {
                **spec,
                "product_id": "fbasecman",
                "template_id": "cman",
                "data_root": str(tmp_path / "cman-data"),
                "nodes": nodes,
            }
        },
    ).json()
    result = client.post(f"/api/v1/deployment/drafts/{draft['id']}/plan")
    assert result.status_code == 422
    assert "目录" in result.json()["detail"]


def _register_imported_environment(service, client, config, environment_id):
    path = Path(service.settings.data_dir) / "imported.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    service.store.put_environment(
        {
            "id": environment_id,
            "product_id": "fbasecman",
            "title": "既有集群",
            "host": "127.0.0.1",
            "port": config["instances"]["test_mmr1"]["port"],
            "database_name": "postgres",
            "database_user": "postgres",
            "deployment_config": str(path),
            "deployment_target": "mmr.fbasecman_regress",
        }
    )
    return path


def test_associate_invalidates_stale_test_context(workbench, tmp_path):
    client, service, spec, _ = workbench
    config, _ = _cman_compiled(service, spec, tmp_path, 45680)
    _register_imported_environment(service, client, config, "cman-imported")
    stale = (
        service.settings.output_dir
        / "fbasecman"
        / "cman-imported"
        / "output"
        / "env"
        / "test_context.yaml"
    )
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("stale identifiers")
    client.post("/api/v1/environments/cman-imported/deployment-draft")
    # 把草稿里的拓扑改到另一组端口——导入的是不同集群，旧上下文必须作废。
    moved = dict(config)
    moved["instances"] = dict(config["instances"])
    moved["instances"]["test_mmr1"] = {
        **config["instances"]["test_mmr1"],
        "port": config["instances"]["test_mmr1"]["port"] + 400,
    }
    draft = client.get("/api/v1/deployment/drafts/cman-imported").json()
    updated = client.put(
        "/api/v1/deployment/drafts/cman-imported",
        json={
            "expected_revision": draft["revision"],
            "spec": {**draft["spec"], "source_yaml": yaml.safe_dump(moved)},
        },
    )
    assert updated.status_code == 200, updated.text
    result = client.post("/api/v1/deployment/drafts/cman-imported/plan")
    assert result.status_code == 200, result.text
    plan = result.json()
    assert plan["ready"], plan["checks"]
    linked = client.post(f"/api/v1/deployment/plans/{plan['id']}/associate")
    assert linked.status_code == 200, linked.text
    assert not stale.exists()


def test_associate_keeps_test_context_when_config_unchanged(workbench, tmp_path):
    client, service, spec, _ = workbench
    config, _ = _cman_compiled(service, spec, tmp_path, 45700)
    _register_imported_environment(service, client, config, "cman-same")
    context = (
        service.settings.output_dir
        / "fbasecman"
        / "cman-same"
        / "output"
        / "env"
        / "test_context.yaml"
    )
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text("current identifiers")
    client.post("/api/v1/environments/cman-same/deployment-draft")
    plan = client.post("/api/v1/deployment/drafts/cman-same/plan").json()
    linked = client.post(f"/api/v1/deployment/plans/{plan['id']}/associate")
    assert linked.status_code == 200, linked.text
    # 部署配置逐字节一致时，既有夹具上下文仍然有效。
    assert context.read_text() == "current identifiers"


def test_import_rejects_version_mismatched_cluster(workbench):
    client, service, spec, _ = workbench
    config, target, _ = compile_spec(
        service.settings, DeploymentSpec(**spec), "fixture"
    )
    for node in config["instances"].values():
        path = Path(node["data_dir"])
        path.mkdir(parents=True)
        # 目标集群是 PG 14，但安装目录的工具是 15.x——接管必须拒绝。
        (path / "PG_VERSION").write_text("14")
    draft = client.post(
        "/api/v1/deployment/drafts",
        json={
            "spec": {
                **spec,
                "mode": "import",
                "source_yaml": yaml.safe_dump(config),
                "target": target,
            }
        },
    ).json()
    plan = client.post(f"/api/v1/deployment/drafts/{draft['id']}/plan").json()
    assert not plan["ready"]
    assert any(
        not check["ok"] and "数据目录" in check["title"]
        for check in plan["checks"]
    )


def test_plan_reports_unreachable_remote_host(workbench):
    client, _, spec, _ = workbench
    _, plan = create_plan(
        workbench, {**spec, "host": "no-such-host.invalid"}
    )
    assert not plan["ready"]
    assert any(
        check["title"] == "目标主机探测" and not check["ok"]
        for check in plan["checks"]
    )


def test_mixed_tool_versions_make_installation_incomplete(workbench):
    client, _, spec, _ = workbench
    psql = Path(spec["home"]) / "bin" / "psql"
    psql.write_text('#!/bin/sh\necho "PostgreSQL 14.0"\n')
    psql.chmod(0o755)
    _, plan = create_plan(workbench)
    assert not plan["ready"]
    tool_check = next(
        check for check in plan["checks"] if check["title"] == "数据库工具完整性"
    )
    assert not tool_check["ok"]


def test_preload_library_file_must_exist(workbench):
    client, _, spec, _ = workbench
    (Path(spec["home"]) / "lib" / "fdd_mmr.so").unlink()
    _, plan = create_plan(workbench, {**spec, "template_id": "mmr"})
    assert not plan["ready"]
    assert any(
        check["title"] == "扩展 fdd_mmr" and not check["ok"]
        for check in plan["checks"]
    )


def test_worker_rechecks_data_created_after_queueing(workbench):
    client, service, _, _ = workbench
    draft, plan = create_plan(workbench)
    applied = client.post(
        f"/api/v1/deployment/plans/{plan['id']}/apply",
        json={"acknowledge_change": True},
    )
    import json

    snapshot = json.loads(service.store.get_task(applied.json()["id"])["parameters"])[
        "_deployment_snapshot"
    ]
    directory = Path(plan["operations"][0]["data_dir"])
    directory.mkdir(parents=True)
    (directory / "PG_VERSION").write_text("15")
    with pytest.raises(ValueError, match="执行前检查失败"):
        worker_environment(
            service.settings,
            service.store,
            service.store.get_environment(draft["id"]),
            snapshot,
        )
