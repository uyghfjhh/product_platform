"""将 fbasecman 回归拓扑映射为 pgcluster 部署配置。"""

from pathlib import Path

import yaml

from .config import Settings


def profile_paths(settings: Settings, environment_id: str) -> tuple[Path, Path]:
    root = settings.data_dir / "profiles" / environment_id
    return root / "pgcluster.yaml", root / "regress.override.yaml"


def legacy_root(settings: Settings, environment_id: str) -> Path:
    return settings.data_dir / "legacy_cman" / environment_id


def build_profile(
    settings: Settings,
    environment: dict,
    *,
    mmr1_port: int,
    data_root: str,
    license_file: str,
) -> tuple[dict, dict]:
    source = settings.fbasecman_regress_root / "regress.yaml"
    old = yaml.safe_load(source.read_text(encoding="utf-8"))
    local = source.with_name("regress.local.yaml")
    if local.is_file():
        _merge(old, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    db = old["database"]
    first_port = int(db["ports"]["mmr1"])
    offset = mmr1_port - first_port
    all_ports = {name: value + offset if isinstance(value, int) else [port + offset for port in value]
                 for name, value in db["ports"].items()}
    if any(port < 1024 or port > 65535 for value in all_ports.values()
           for port in (value if isinstance(value, list) else [value])):
        raise ValueError("生成的数据库端口超出有效范围")
    if not Path(data_root).is_absolute() or data_root == "/":
        raise ValueError("数据根目录需要非根绝对路径")
    if not Path(license_file).is_absolute():
        raise ValueError("License 文件需要绝对路径")

    use_citus = bool(db.get("enable_citus"))
    preloads = ["fdd_mmr"] + (["citus"] if use_citus else [])
    installations = {
        "regress_postgres": {
            "provider": "fbase",
            "home": db["mmr_postgres_dir"],
            "license": {"source_file": license_file, "data_file": "license.dat"},
            "plugins": {name: {"required": True, "extension": name} for name in preloads},
        }
    }
    instances = {}
    streaming = {}
    for group in ("mmr1", "mmr2"):
        primary_name = "test_" + group
        instances[primary_name] = {
            "host": "regress_host", "installation": "regress_postgres",
            "port": all_ports[group], "data_dir": str(Path(data_root) / primary_name),
        }
        standbys = []
        for index, port in enumerate(all_ports[group + "_standbys"], 1):
            name = f"{primary_name}_s{index}"
            instances[name] = {
                "host": "regress_host", "installation": "regress_postgres",
                "port": port, "data_dir": str(Path(data_root) / name),
            }
            standbys.append({
                "instance": name,
                "slot": f"regress_{group}_s{index}",
                "application_name": f"pg_{239 + index if group == 'mmr1' else 249 + index}",
            })
        streaming[group] = {"primary": primary_name, "standbys": standbys,
                            "replication": {"mode": "async"}}

    auth_rules = [
        {"type": "local", "database": "all", "user": "all", "auth_method": "trust"},
        {"type": "host", "database": "all", "user": "postgres", "address": "0.0.0.0/0", "auth_method": "trust"},
    ]
    for role, method in (
        ("u1", "scram-sha-256"), ("u2", "trust"), ("u11", "scram-sha-256"),
        ("u3", "scram-sha-256"), ("u22", "md5"),
        ("clear_text_user_1", "password"), ("clear_text_user_2", "password"),
        ("clear_text_user_3", "password"),
        ("md5_user_1", "md5"), ("md5_user_2", "md5"),
        ("scram256_user_1", "scram-sha-256"),
        ("scram256_user_2", "scram-sha-256"),
    ):
        auth_rules.append({"type": "host", "database": "all", "user": role,
                           "address": "0.0.0.0/0", "auth_method": method})
    auth_rules.extend([
        {"type": "host", "database": "replication", "user": "all", "address": "0.0.0.0/0", "auth_method": "trust"},
    ])
    deployment = {
        "hosts": {"regress_host": {"address": environment["host"]}},
        "postgresql_installations": installations,
        "postgresql_config": {
            "parameters": {
                "wal_level": "logical", "hot_standby": "on",
                "shared_preload_libraries": preloads,
                "track_commit_timestamp": "on",
                "fdd.running_databases": "postgres,test_db",
                "fdd.exclude_schema": "fdd,pg_catalog,information_schema",
                "max_prepared_transactions": 200,
                "logging_collector": "on", "log_directory": "pg_log",
            },
            "replication_capacity": {"wal_senders": "auto", "replication_slots": "auto"},
            "hba": auth_rules,
        },
        "instances": instances,
        "streaming_clusters": streaming,
        "mmr_clusters": {
            "fbasecman_regress": {
                "database": "postgres", "group_name": "g1",
                "extensions": preloads,
                "members": {
                    "node1": {"streaming_cluster": "mmr1", "node_name": "node1",
                              "mmr_node": {"streaming": "off", "two_phase": False}},
                    "node2": {"streaming_cluster": "mmr2", "node_name": "node2",
                              "mmr_node": {"streaming": "off", "two_phase": False},
                              "join": {"synchronize_structure": "all"}},
                },
            }
        },
    }
    output_root = legacy_root(settings, environment["id"]) / "output"
    regression = {
        "database": {
            "enable_citus": use_citus,
            "mmr_host": environment["host"],
            "mmr_pg_user": db["mmr_pg_user"],
            "mmr_postgres_dir": db["mmr_postgres_dir"],
            "mmr_data_root": data_root,
            "ports": all_ports,
        },
        "framework": {
            "output_dir": str(output_root),
            "environment_output_dir": str(output_root / "env"),
        },
    }
    return deployment, regression


def save_profile(settings: Settings, environment: dict, **options) -> tuple[Path, Path]:
    deployment, regression = build_profile(settings, environment, **options)
    path, override = profile_paths(settings, environment["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(deployment, allow_unicode=True, sort_keys=False), encoding="utf-8")
    override.write_text(yaml.safe_dump(regression, allow_unicode=True, sort_keys=False), encoding="utf-8")
    # 旧报告解析器从根目录读有效配置；此副本只供展示，不参与测试执行。
    legacy = legacy_root(settings, environment["id"])
    legacy.mkdir(parents=True, exist_ok=True)
    source = settings.fbasecman_regress_root / "regress.yaml"
    merged = yaml.safe_load(source.read_text(encoding="utf-8"))
    local = source.with_name("regress.local.yaml")
    if local.is_file():
        _merge(merged, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    _merge(merged, regression)
    (legacy / "regress.yaml").write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path, override


def _merge(base: dict, changes: dict) -> None:
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
