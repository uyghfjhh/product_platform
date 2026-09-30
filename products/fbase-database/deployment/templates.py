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
    overrides = {node.name: node for node in spec.nodes}
    if overrides and set(overrides) != set(nodes):
        raise ValueError("节点覆盖必须包含模板全部节点，不能增删固定回归节点")
    for name, node in overrides.items():
        nodes[name].update(port=node.port, data_dir=node.data_dir)
    plugins = {
        name: {"required": True, "extension": name} for name in cluster["plugins"]
    }
    if spec.template_id == "mmr":
        plugins["fbase_mac"] = {"required": True, "extension": "fbase_mac"}
    parameters = {
        **cluster["postgresql"]["settings"],
        **spec.parameters,
        "listen_addresses": "*",
        "logging_collector": "on",
        "log_destination": "stderr,csvlog",
        "log_directory": "log",
        "wal_level": "logical",
        "hot_standby": "on",
        "shared_preload_libraries": [
            name
            for name, opts in cluster["plugins"].items()
            if opts.get("preload", True)
        ],
    }
    if spec.template_id == "mmr":
        parameters.update(
            {"track_commit_timestamp": "on", "fdd.running_databases": "postgres"}
        )
    config = {
        "hosts": {"deploy_host": {"address": spec.host}},
        "postgresql_installations": {
            "deploy_postgres": {
                "provider": "fbase",
                "home": spec.home,
                "plugins": plugins,
                "license": {
                    "source_file": spec.license_file,
                    "data_file": "license.dat",
                },
            }
        },
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
                "host": "deploy_host",
                "installation": "deploy_postgres",
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


def import_files(settings, facts, target):
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
