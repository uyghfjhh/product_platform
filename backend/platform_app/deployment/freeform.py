"""Free-topology compiler: user-modeled single streaming cluster -> pgcluster config.

Product-neutral: fixed regression topologies stay in product templates; this
covers the generic primary + standbys case the workbench models freely.
"""

import re
from pathlib import Path

_IDENTIFIER = re.compile(r"[a-zA-Z_]\w*")
ROOT = Path(__file__).resolve().parents[3]


def compile_freeform(spec):
    if not spec.nodes:
        raise ValueError("自由拓扑至少需要一个节点")
    primaries = [node for node in spec.nodes if node.role == "primary"]
    if len(primaries) != 1:
        raise ValueError("自由拓扑需要且只能有一个主节点")
    names = [node.name for node in spec.nodes]
    if len(set(names)) != len(names):
        raise ValueError("节点名称不能重复")
    if not _IDENTIFIER.fullmatch(spec.cluster_name):
        raise ValueError("集群名称无效")
    for name in names:
        if not _IDENTIFIER.fullmatch(name):
            raise ValueError(f"节点名称 {name} 无效")
    if not spec.home:
        raise ValueError("请填写数据库安装目录")
    if Path(spec.home).is_relative_to(ROOT / "data"):
        raise ValueError("数据库安装目录不能位于平台控制面目录")

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
        homes = {host.name: host.home or spec.home for host in spec.hosts}
    else:
        hosts_section = {"deploy_host": {"address": spec.host}}
        default_host = "deploy_host"
        homes = {default_host: spec.home}

    install_by_home = {}
    installations = {}
    install_for_host = {}
    for host_name in hosts_section:
        home = homes[host_name]
        if home not in install_by_home:
            install_name = (
                "deploy_postgres"
                if not install_by_home
                else "deploy_postgres_" + host_name
            )
            install_by_home[home] = install_name
            installation = {
                "provider": "fbase" if spec.license_file else "postgres",
                "home": home,
            }
            if spec.license_file:
                installation["license"] = {
                    "source_file": spec.license_file,
                    "data_file": "license.dat",
                }
            installations[install_name] = installation
        install_for_host[host_name] = install_by_home[home]

    instances = {}
    for node in spec.nodes:
        host_name = node.host or default_host
        if host_name not in hosts_section:
            raise ValueError(f"节点 {node.name} 的主机 {host_name} 未在主机资源中声明")
        instances[node.name] = {
            "host": host_name,
            "installation": install_for_host[host_name],
            "port": node.port,
            "data_dir": node.data_dir,
        }

    parameters = {
        "wal_level": "logical",
        "hot_standby": "on",
        "listen_addresses": "*",
        "logging_collector": "on",
        "log_destination": "stderr,csvlog",
        "log_directory": "log",
        **spec.parameters,
    }
    config = {
        "hosts": hosts_section,
        "postgresql_installations": installations,
        "postgresql_config": {
            "parameters": parameters,
            "replication_capacity": {
                "wal_senders": "auto",
                "replication_slots": "auto",
            },
            "hba": [
                {
                    "type": "local",
                    "database": "all",
                    "user": "all",
                    "auth_method": "trust",
                },
                {
                    "type": "host",
                    "database": "all",
                    "user": "all",
                    "address": "0.0.0.0/0",
                    "auth_method": "trust",
                },
                {
                    "type": "host",
                    "database": "replication",
                    "user": "all",
                    "address": "0.0.0.0/0",
                    "auth_method": "trust",
                },
            ],
        },
        "instances": instances,
        "streaming_clusters": {
            spec.cluster_name: {
                "primary": primaries[0].name,
                "standbys": [
                    {"instance": node.name}
                    for node in spec.nodes
                    if node.role == "standby"
                ],
                "replication": {"mode": "async"},
            }
        },
    }
    return config, "streaming." + spec.cluster_name, {}
