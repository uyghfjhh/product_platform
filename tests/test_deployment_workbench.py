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
    inspect_plan,
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


def test_drifted_import_is_blocked_and_context_preserved(workbench, tmp_path):
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
    # 把草稿里的拓扑改到另一组端口——差异计划会如实列出，但不可执行，
    # 不允许把平台无法落地的配置发布为环境当前配置。
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
    assert plan["mode"] == "diff" and not plan["executable"]
    assert plan["ready"], plan["checks"]
    assert (
        client.post(f"/api/v1/deployment/plans/{plan['id']}/associate").status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/deployment/plans/{plan['id']}/apply",
            json={"acknowledge_change": True},
        ).status_code
        == 422
    )
    # 未发布任何变更，旧回归上下文保持有效。
    assert stale.read_text() == "stale identifiers"


def test_associate_invalidates_context_on_republished_canonical_config(
    workbench, tmp_path
):
    client, service, spec, _ = workbench
    config, _ = _cman_compiled(service, spec, tmp_path, 45690)
    environment_id = "cman-canonical"
    path = Path(service.settings.data_dir) / (environment_id + ".yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    # 语义相同但文本不同的旧配置文件（注释、键序差异）：关联会重发规范形式，
    # 摘要随之变化，旧上下文必须作废。
    path.write_text("# 既有部署配置\n" + yaml.safe_dump(config, allow_unicode=True))
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
    stale = (
        service.settings.output_dir
        / "fbasecman"
        / environment_id
        / "output"
        / "env"
        / "test_context.yaml"
    )
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("stale identifiers")
    client.post(f"/api/v1/environments/{environment_id}/deployment-draft")
    plan = client.post(f"/api/v1/deployment/drafts/{environment_id}/plan").json()
    assert plan.get("mode") != "diff"  # 语义无差异，是纯接管计划
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


def _register_fbase_environment(service, config, environment_id):
    path = Path(service.settings.data_dir) / (environment_id + ".yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    service.store.put_environment(
        {
            "id": environment_id,
            "product_id": "fbase-database",
            "title": "既有 FBase 集群",
            "host": "127.0.0.1",
            "port": config["instances"]["mac_primary"]["port"],
            "database_name": "postgres",
            "database_user": "postgres",
            "deployment_config": str(path),
            "deployment_target": "logical.fbase_regress",
        }
    )


def _import_plan(client, spec, environment_id, edited_config):
    client.post(f"/api/v1/environments/{environment_id}/deployment-draft")
    draft = client.get(f"/api/v1/deployment/drafts/{environment_id}").json()
    updated = client.put(
        f"/api/v1/deployment/drafts/{environment_id}",
        json={
            "expected_revision": draft["revision"],
            "spec": {**draft["spec"], "source_yaml": yaml.safe_dump(edited_config)},
        },
    )
    assert updated.status_code == 200, updated.text
    result = client.post(f"/api/v1/deployment/drafts/{environment_id}/plan")
    assert result.status_code == 200, result.text
    return result.json()


def _materialize_cluster(config, major="15"):
    for node in config["instances"].values():
        path = Path(node["data_dir"])
        path.mkdir(parents=True, exist_ok=True)
        (path / "PG_VERSION").write_text(major)


def test_multi_host_spec_compiles_hosts_ssh_and_installations(workbench):
    client, _, spec, _ = workbench
    two_hosts = [
        {
            "name": "local_a",
            "address": "127.0.0.1",
            "ssh_user": "",
            "ssh_port": None,
            "ssh_identity_file": "",
            "ssh_connect_timeout": 10,
            "home": "",
        },
        {
            "name": "local_b",
            "address": "localhost",
            "ssh_user": "postgres",
            "ssh_port": 22,
            "ssh_identity_file": "",
            "ssh_connect_timeout": 10,
            "home": "",
        },
    ]
    config, target, _ = compile_spec(
        workbench[1].settings, DeploymentSpec(**{**spec, "hosts": two_hosts}), "m"
    )
    assert set(config["hosts"]) == {"local_a", "local_b"}
    assert config["hosts"]["local_b"]["ssh"] == {"user": "postgres", "port": 22}
    assert all(
        node["host"] in {"local_a", "local_b"}
        for node in config["instances"].values()
    )


def test_multi_host_per_node_assignment_and_per_home_installation(workbench):
    _, service, spec, _ = workbench
    alt_home = spec["home"] + "-b"
    spec = {
        **spec,
        "hosts": [
            {"name": "h1", "address": "127.0.0.1"},
            {"name": "h2", "address": "localhost", "home": alt_home},
        ],
        "nodes": [
            {"name": "mac_primary", "host": "h1", "port": spec["base_port"],
             "data_dir": spec["data_root"] + "/mac_primary"},
            {"name": "mac_standby", "host": "h2", "port": spec["base_port"] + 1,
             "data_dir": spec["data_root"] + "/mac_standby"},
            {"name": "logical_subscriber", "host": "h2", "port": spec["base_port"] + 2,
             "data_dir": spec["data_root"] + "/logical_subscriber"},
        ],
    }
    config, _, _ = compile_spec(
        service.settings, DeploymentSpec(**spec), "m"
    )
    assert config["instances"]["mac_primary"]["host"] == "h1"
    assert config["instances"]["mac_standby"]["host"] == "h2"
    installs = config["postgresql_installations"]
    assert set(installs) == {"deploy_postgres", "deploy_postgres_h2"}
    assert installs["deploy_postgres_h2"]["home"] == alt_home
    assert (
        config["instances"]["mac_standby"]["installation"]
        == "deploy_postgres_h2"
    )


def test_host_resource_validation_rejects_duplicates_and_orphan(workbench):
    _, service, spec, _ = workbench
    for hosts in (
        [{"name": "h1", "address": "127.0.0.1"}, {"name": "h1", "address": "127.0.0.2"}],
        [{"name": "h1", "address": "127.0.0.1"}, {"name": "h2", "address": "127.0.0.1"}],
        [{"name": "h1", "address": "10.0.0.9"}],  # spec.host 不在声明的主机资源中
    ):
        with pytest.raises(ValueError):
            compile_spec(
                service.settings, DeploymentSpec(**{**spec, "hosts": hosts}), "m"
            )


def test_cman_multi_host_compiles_with_ssh_and_host_assignment(workbench, tmp_path):
    _, service, spec, _ = workbench
    config, _ = _cman_compiled(service, spec, tmp_path, 45800)
    cman_spec = {
        **spec,
        "product_id": "fbasecman",
        "template_id": "cman",
        "base_port": 45800,
        "data_root": str(tmp_path / "cman-data"),
        "hosts": [
            {"name": "h1", "address": "127.0.0.1"},
            {
                "name": "h2",
                "address": "localhost",
                "ssh_user": "postgres",
                "ssh_port": 22,
            },
        ],
        "nodes": [
            {
                "name": name,
                "host": "h2" if name.startswith("test_mmr2") else "h1",
                "port": node["port"],
                "data_dir": node["data_dir"],
            }
            for name, node in config["instances"].items()
        ],
    }
    compiled, target, _ = compile_spec(
        service.settings, DeploymentSpec(**cman_spec), "cman-env"
    )
    assert compiled["hosts"]["h2"]["ssh"] == {"user": "postgres", "port": 22}
    assert compiled["instances"]["test_mmr2"]["host"] == "h2"
    assert compiled["instances"]["test_mmr1"]["host"] == "h1"
    assert target == "mmr.fbasecman_regress"
    # pgcluster 引擎本身接受声明的 ssh 主机段
    import tempfile

    from platform_app.deployment.workbench import validate_config

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "pgcluster.yaml"
        path.write_text(yaml.safe_dump(compiled, allow_unicode=True))
        facts = validate_config(service.settings, path, target)
    assert set(facts["hosts"]) == {"h1", "h2"}


def test_diff_plan_scale_out_new_standby_is_executable(workbench, tmp_path):
    client, service, spec, _ = workbench
    config, target, _ = compile_spec(
        service.settings, DeploymentSpec(**spec), "fixture"
    )
    _materialize_cluster(config)
    _register_fbase_environment(service, config, "fbase-scale")
    edited = yaml.safe_load(yaml.safe_dump(config))
    edited["instances"]["mac_s2"] = {
        **config["instances"]["mac_standby"],
        "port": spec["base_port"] + 90,
        "data_dir": str(Path(spec["data_root"]) / "mac_s2"),
    }
    standbys = edited["streaming_clusters"]["mac"]["standbys"]
    standbys.append({"instance": "mac_s2"})
    plan = _import_plan(client, spec, "fbase-scale", edited)
    assert plan["mode"] == "diff"
    assert plan["executable"], plan["operations"]
    kinds = {op["kind"] for op in plan["operations"]}
    assert kinds == {"add_standby"}
    assert plan["action"] == "deployment.create"
    assert plan["ready"], plan["checks"]
    # 新节点按"新实例"探测（空目录+端口可用），既有节点按接管口径探测。
    linked = client.post(f"/api/v1/deployment/plans/{plan['id']}/associate")
    assert linked.status_code == 200, linked.text
    applied = client.post(
        f"/api/v1/deployment/plans/{plan['id']}/apply",
        json={"acknowledge_change": True},
    )
    assert applied.status_code == 202, applied.text


def test_diff_plan_scale_in_is_listed_but_blocked(workbench, tmp_path):
    client, service, spec, _ = workbench
    config, target, _ = compile_spec(
        service.settings, DeploymentSpec(**spec), "fixture"
    )
    # 当前环境带一个回归拓扑之外的额外备库；导入配置把它移除。
    # （移除产品声明节点会在更早的拓扑校验阶段被拒绝，这里覆盖的是差异计划层。）
    current = yaml.safe_load(yaml.safe_dump(config))
    current["instances"]["mac_s2"] = {
        **config["instances"]["mac_standby"],
        "port": spec["base_port"] + 90,
        "data_dir": str(Path(spec["data_root"]) / "mac_s2"),
    }
    current["streaming_clusters"]["mac"]["standbys"].append(
        {"instance": "mac_s2"}
    )
    _materialize_cluster(current)
    _register_fbase_environment(service, current, "fbase-shrink")
    edited = yaml.safe_load(yaml.safe_dump(config))
    plan = _import_plan(client, spec, "fbase-shrink", edited)
    assert plan["mode"] == "diff"
    assert not plan["executable"]
    assert any(
        op["kind"] == "remove_node" and op["node"] == "mac_s2"
        for op in plan["operations"]
    )
    assert (
        client.post(f"/api/v1/deployment/plans/{plan['id']}/associate").status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/deployment/plans/{plan['id']}/apply",
            json={"acknowledge_change": True},
        ).status_code
        == 422
    )


def test_diff_plan_structural_and_parameter_changes_blocked(workbench, tmp_path):
    client, service, spec, _ = workbench
    config, target, _ = compile_spec(
        service.settings, DeploymentSpec(**spec), "fixture"
    )
    _materialize_cluster(config)
    _register_fbase_environment(service, config, "fbase-edit")
    edited = yaml.safe_load(yaml.safe_dump(config))
    edited["instances"]["mac_standby"]["port"] += 500
    plan = _import_plan(client, spec, "fbase-edit", edited)
    assert plan["mode"] == "diff" and not plan["executable"]
    assert any(op["kind"] == "change_node" for op in plan["operations"])
    edited = yaml.safe_load(yaml.safe_dump(config))
    edited["postgresql_config"]["parameters"]["max_connections"] = 500
    plan = _import_plan(client, spec, "fbase-edit", edited)
    assert plan["mode"] == "diff" and not plan["executable"]
    assert any(op["kind"] == "parameters" for op in plan["operations"])


def test_partially_applied_plan_resumes_via_managed_markers(workbench, tmp_path):
    client, service, spec, queued = workbench
    draft, plan = create_plan(workbench, spec)
    assert plan["ready"] and plan["action"] == "deployment.create"
    # 第一次 apply：任务排队后被标记失败，模拟 create 中途失败
    applied = client.post(
        f"/api/v1/deployment/plans/{plan['id']}/apply",
        json={"acknowledge_change": True},
    )
    assert applied.status_code == 202, applied.text
    first_task = applied.json()["id"]
    service.store.transition_task(first_task, ("QUEUED",), "RUNNING")
    service.store.finish_task(first_task, ("RUNNING",), "FAILED", "simulated")
    # 部分节点已被 pgcluster 创建（受管标记 + PG_VERSION）
    facts = client.get(f"/api/v1/deployment/plans/{plan['id']}").json()["facts"]
    partial = facts["nodes"][0]
    path = Path(partial["data_dir"])
    path.mkdir(parents=True, exist_ok=True)
    (path / "PG_VERSION").write_text("15")
    (path / ".pgcluster-managed").write_text('{"node": "%s"}' % partial["name"])
    # 重新检查：受管节点按既有口径通过，其余仍要求空目录
    fresh = inspect_plan(service.plan(plan["id"]))
    assert all(check["ok"] for check in fresh), fresh
    # 重放同一计划 → 新的尝试任务而非去重返回旧任务
    replayed = client.post(
        f"/api/v1/deployment/plans/{plan['id']}/apply",
        json={"acknowledge_change": True},
    )
    assert replayed.status_code == 202, replayed.text
    assert replayed.json()["id"] != first_task
    assert queued  # tasks were enqueued
    detail = client.get(f"/api/v1/deployment/plans/{plan['id']}").json()
    assert len(detail["attempts"]) == 2


def test_foreign_managed_dir_does_not_count_as_resume_checkpoint(workbench):
    _, service, spec, _ = workbench
    draft, plan = create_plan(workbench, spec)
    facts = service.plan(plan["id"])["facts"]
    node = facts["nodes"][0]
    path = Path(node["data_dir"])
    path.mkdir(parents=True, exist_ok=True)
    (path / "PG_VERSION").write_text("15")
    # 标记声明属于别的节点——不算本计划的断点
    (path / ".pgcluster-managed").write_text('{"node": "other_cluster_node"}')
    checks = [
        check
        for check in inspect_plan(service.plan(plan["id"]))
        if check["title"].startswith(node["name"] + " 数据目录")
    ]
    assert checks and not checks[0]["ok"]


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
