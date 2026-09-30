"""Product-owned MAC/MMR deployment templates and regression endpoint mapping."""

from copy import deepcopy

import yaml


def templates(settings):
    return [
        {
            "id": "mac",
            "title": "等保：主备＋逻辑订阅",
            "nodes": 3,
            "base_port": 15432,
            "target": "logical.fbase_regress",
        },
        {
            "id": "mmr",
            "title": "多活：三组主备",
            "nodes": 6,
            "base_port": 10011,
            "target": "mmr.fbase_regress",
        },
    ]


def compile_template(settings, spec, environment_id):
    raw = yaml.safe_load(
        (settings.product_regress_root("fbase-database") / "regress.yaml").read_text()
    )
    cluster = deepcopy(raw["clusters"][spec.template_id])
    first = next(iter(cluster["nodes"].values()))["port"]
    delta = spec.base_port - first
    nodes = {
        name: {
            **node,
            "host": spec.host,
            "port": node["port"] + delta,
            "data_dir": f"{spec.data_root}/{name}",
        }
        for name, node in cluster["nodes"].items()
    }
    host_entries = list(spec.hosts)
    if host_entries:
        hosts_section = {}
        for host in host_entries:
            entry = {"address": host.address}
            ssh = host.ssh_options()
            if ssh:
                entry["ssh"] = ssh
            hosts_section[host.name] = entry
        default_host = next(
            host.name for host in host_entries if host.address == spec.host
        )
        homes = {
            host.name: host.home or spec.home for host in host_entries
        }
    else:
        hosts_section = {"deploy_host": {"address": spec.host}}
        default_host = "deploy_host"
        homes = {default_host: spec.home}
    overrides = {node.name: node for node in spec.nodes}
    if overrides and set(overrides) != set(nodes):
        raise ValueError("节点覆盖必须包含模板全部节点，不能增删固定回归节点")
    node_hosts = {name: default_host for name in nodes}
    for name, node in overrides.items():
        host_name = node.host or default_host
        if host_name not in hosts_section:
            raise ValueError(f"节点 {name} 的主机 {host_name} 未在主机资源中声明")
        node_hosts[name] = host_name
        nodes[name].update(
            host=hosts_section[host_name]["address"],
            port=node.port,
            data_dir=node.data_dir,
        )
    plugins = {
        name: {"required": True, "extension": name} for name in cluster["plugins"]
    }
    if spec.template_id == "mmr":
        plugins["fbase_mac"] = {"required": True, "extension": "fbase_mac"}
    preloads = [
        name
        for name, opts in cluster["plugins"].items()
        if opts.get("preload", True)
    ]
    for name in preloads:
        # 探测同时核对预加载库的 .so 是否真实存在。
        plugins[name]["preload_library"] = name
    parameters = {
        **cluster["postgresql"]["settings"],
        **spec.parameters,
        "listen_addresses": "*",
        "logging_collector": "on",
        "log_destination": "stderr,csvlog",
        "log_directory": "log",
        "wal_level": "logical",
        "hot_standby": "on",
        "shared_preload_libraries": preloads,
    }
    if spec.template_id == "mmr":
        parameters.update(
            {"track_commit_timestamp": "on", "fdd.running_databases": "postgres"}
        )
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
            installations[install_name] = {
                "provider": "fbase",
                "home": home,
                "plugins": plugins,
                "license": {
                    "source_file": spec.license_file,
                    "data_file": "license.dat",
                },
            }
        install_for_host[host_name] = install_by_home[home]
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
        "instances": {
            name: {
                "host": node_hosts[name],
                "installation": install_for_host[node_hosts[name]],
                "port": node["port"],
                "data_dir": node["data_dir"],
            }
            for name, node in nodes.items()
        },
    }
    if spec.template_id == "mac":
        config["streaming_clusters"] = {
            "mac": {
                "primary": "mac_primary",
                "standbys": [{"instance": "mac_standby"}],
                "extensions": list(plugins),
            },
            "mac_subscriber": {
                "primary": "logical_subscriber",
                "standbys": [],
                "extensions": list(plugins),
            },
        }
        config["logical_replications"] = {
            "fbase_regress": {
                "pub": {
                    "streaming_cluster": "mac",
                    "database": "postgres",
                    "tables": "all",
                },
                "sub": {
                    "streaming_cluster": "mac_subscriber",
                    "database": "postgres",
                    "slot": {"name": "fbase_regress_slot"},
                },
            }
        }
        target = "logical.fbase_regress"
    else:
        members = cluster["groups"]["mmr"]["members"]
        config["streaming_clusters"] = {
            name: {
                "primary": rel["primary"],
                "standbys": [{"instance": node} for node in rel["standbys"]],
                "extensions": list(plugins),
            }
            for name, rel in members.items()
        }
        config["mmr_clusters"] = {
            "fbase_regress": {
                "database": "postgres",
                "group_name": cluster["groups"]["mmr"]["group_name"],
                "extensions": list(plugins),
                "members": {
                    name: {
                        "streaming_cluster": name,
                        "node_name": name,
                        "mmr_node": {
                            "failover_slot": True,
                            "streaming": "parallel",
                            "two_phase": True,
                        },
                    }
                    for name in members
                },
            }
        }
        target = "mmr.fbase_regress"
    auxiliary = {
        "regress.override.yaml": yaml.safe_dump(
            {
                "postgres": {"home": spec.home},
                "clusters": {spec.template_id: {"nodes": nodes}},
            },
            allow_unicode=True,
        )
    }
    return config, target, auxiliary


def import_files(settings, facts, target, environment_id=None):
    cluster = (
        "mac"
        if target in {"logical.fbase_regress", "streaming.mac"}
        else ("mmr" if target == "mmr.fbase_regress" else None)
    )
    if cluster is None:
        return {}
    raw = yaml.safe_load(
        (settings.product_regress_root("fbase-database") / "regress.yaml").read_text()
    )
    expected = set(raw["clusters"][cluster]["nodes"])
    actual = {row["name"]: row for row in facts["nodes"]}
    if not expected.issubset(actual):
        raise ValueError("导入的回归拓扑缺少产品声明的节点，不能绑定该回归环境")
    nodes = {
        name: {
            "host": actual[name]["host"],
            "port": actual[name]["port"],
            "data_dir": actual[name]["data_dir"],
        }
        for name in expected
    }
    home = facts["installations"][next(iter(actual.values()))["installation"]]["home"]
    return {
        "regress.override.yaml": yaml.safe_dump(
            {"postgres": {"home": home}, "clusters": {cluster: {"nodes": nodes}}},
            allow_unicode=True,
        )
    }
