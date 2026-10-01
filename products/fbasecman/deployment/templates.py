"""Fixed cman regression topology, compiled from its product-owned profile."""

from copy import deepcopy
from pathlib import Path

import yaml

from .profile import _merge, build_profile


def templates(settings):
    return [
        {
            "id": "cman",
            "title": "fbasecman：两组多活，各六个备库",
            "nodes": 14,
            "base_port": 11011,
            "target": "mmr.fbasecman_regress",
        }
    ]


def compile_template(settings, spec, environment_id):
    environment = {"id": environment_id, "host": spec.host}
    config, regression = build_profile(
        settings,
        environment,
        mmr1_port=spec.base_port,
        data_root=spec.data_root,
        license_file=spec.license_file,
    )
    config["postgresql_installations"]["regress_postgres"]["home"] = spec.home
    config["postgresql_config"]["parameters"].update(spec.parameters)
    overrides = {node.name: node for node in spec.nodes}
    if overrides and set(overrides) != set(config["instances"]):
        raise ValueError("节点覆盖必须包含模板全部节点")
    if spec.hosts:
        hosts_section = {}
        for host in spec.hosts:
            entry = {"address": host.address}
            ssh = host.ssh_options()
            if ssh:
                entry["ssh"] = ssh
            hosts_section[host.name] = entry
        default_host = next(
            host.name for host in spec.hosts if host.address == spec.host
        )
        base_installation = deepcopy(config["postgresql_installations"]["regress_postgres"])
        install_by_home = {}
        install_for_host = {}
        installations = {}
        for host_name in hosts_section:
            home = next(
                host.home or spec.home
                for host in spec.hosts
                if host.name == host_name
            )
            if home not in install_by_home:
                install_name = (
                    "regress_postgres"
                    if not install_by_home
                    else "regress_postgres_" + host_name
                )
                install_by_home[home] = install_name
                installations[install_name] = {**base_installation, "home": home}
            install_for_host[host_name] = install_by_home[home]
        config["hosts"] = hosts_section
        config["postgresql_installations"] = installations
        for name, node in config["instances"].items():
            host_name = (
                overrides[name].host
                if name in overrides and overrides[name].host
                else default_host
            )
            if host_name not in hosts_section:
                raise ValueError(f"节点 {name} 的主机 {host_name} 未在主机资源中声明")
            node["host"] = host_name
            node["installation"] = install_for_host[host_name]
        regression["database"]["mmr_host"] = hosts_section[
            config["instances"]["test_mmr1"]["host"]
        ]["address"]
    for name, node in overrides.items():
        config["instances"][name].update(port=node.port, data_dir=node.data_dir)
    # cman 回归代码按 <数据根目录>/<节点名> 约定定位数据目录（rebuild/备份等），
    # 自定义目录布局会破坏该约定；端口可以自定义，目录暂不支持。
    posix_root = spec.data_root.rstrip("/")
    for name, node in config["instances"].items():
        if node["data_dir"] != f"{posix_root}/{name}":
            raise ValueError(
                "cman 回归约定节点数据目录为 <数据根目录>/<节点名>，暂不支持自定义目录布局"
            )
    regression["database"].update(mmr_postgres_dir=spec.home)
    for group in ("mmr1", "mmr2"):
        regression["database"]["ports"][group] = config["instances"]["test_" + group][
            "port"
        ]
        standbys = [
            config["instances"][row["instance"]]["port"]
            for row in config["streaming_clusters"][group]["standbys"]
        ]
        ports = regression["database"]["ports"]
        ports[group + "_standbys"] = standbys
        # 历史键 mmr<i>_standby<j> 与列表同步，避免端口覆盖后标量失配。
        for index, port in enumerate(standbys, 1):
            key = f"{group}_standby{index}"
            if key in ports:
                ports[key] = port
    old = yaml.safe_load(
        (settings.product_regress_root("fbasecman") / "regress.yaml").read_text()
    )
    _merge(old, regression)
    return (
        config,
        "mmr.fbasecman_regress",
        {
            "regress.override.yaml": yaml.safe_dump(regression, allow_unicode=True),
            "regress.yaml": yaml.safe_dump(old, allow_unicode=True),
        },
    )


def _base_regress(settings):
    source = settings.product_regress_root("fbasecman") / "regress.yaml"
    base = yaml.safe_load(source.read_text(encoding="utf-8"))
    local = source.with_name("regress.local.yaml")
    if local.is_file():
        _merge(base, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    return base


def import_files(settings, facts, target, environment_id=None):
    """既有 cman 集群导入时，按实测拓扑重新派生回归辅助配置。

    只支持回归约定的 14 节点固定形态（两组主备、目录 <root>/<name>），
    不满足约定时显式拒绝而不是生成会静默失配的上下文。
    """
    if target != "mmr.fbasecman_regress":
        return {}
    nodes = {row["name"]: row for row in facts["nodes"]}
    expected = ["test_mmr1", "test_mmr2"] + [
        f"test_{group}_s{index}"
        for group in ("mmr1", "mmr2")
        for index in range(1, 7)
    ]
    missing = [name for name in expected if name not in nodes]
    if missing:
        raise ValueError(
            "导入的拓扑缺少 cman 回归节点: " + ", ".join(missing)
        )
    roots = set()
    for name in expected:
        directory = Path(nodes[name]["data_dir"])
        if directory.name != name:
            raise ValueError(
                "cman 回归约定节点数据目录为 <数据根目录>/<节点名>，"
                f"节点 {name} 的目录 {nodes[name]['data_dir']} 不满足该约定"
            )
        roots.add(str(directory.parent))
    if len(roots) != 1:
        raise ValueError("cman 回归要求全部节点位于同一数据根目录")
    base = _base_regress(settings)
    db = base["database"]
    extensions = (facts.get("cluster_extensions") or {}).get(
        "mmr.fbasecman_regress", []
    )
    ports = {}
    for group in ("mmr1", "mmr2"):
        ports[group] = nodes["test_" + group]["port"]
        standbys = [nodes[f"test_{group}_s{index}"]["port"] for index in range(1, 7)]
        ports[group + "_standbys"] = standbys
        for index, port in enumerate(standbys[:3], 1):
            ports[f"{group}_standby{index}"] = port
    installation = facts["installations"][nodes["test_mmr1"]["installation"]]
    regression = {
        "database": {
            "enable_citus": "citus" in extensions,
            "mmr_host": nodes["test_mmr1"]["host"],
            "mmr_pg_user": db["mmr_pg_user"],
            "mmr_postgres_dir": installation["home"],
            "mmr_data_root": roots.pop(),
            "ports": ports,
        },
        "framework": {
            "output_dir": str(settings.artifact_dir("fbasecman", environment_id)),
            "runtime_dir": str(settings.runtime_dir / "products" / "fbasecman" / environment_id),
            "state_dir": str(settings.regression_state_dir("fbasecman", environment_id)),
            "environment_output_dir": str(settings.profile_dir(environment_id) / "fixture"),
        },
    }
    merged = deepcopy(base)
    _merge(merged, regression)
    return {
        "regress.override.yaml": yaml.safe_dump(regression, allow_unicode=True),
        "regress.yaml": yaml.safe_dump(merged, allow_unicode=True),
    }
