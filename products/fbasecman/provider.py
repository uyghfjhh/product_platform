"""fbasecman test, stability, and runtime observation provider."""

import json
import os
import socket
import sys
from pathlib import Path

import yaml

from platform_app.product_catalog import discover_products
from platform_app.providers import CommandSpec
from platform_regress.clients import jdbc as jdbc_client
from platform_app.topology import configured_topology
from products.fbasecman.observations import (
    parse_group_members,
    parse_group_routing,
    parse_groups,
    parse_monitor_config,
    parse_node_monitor,
    parse_node_status,
    parse_nodes,
    parse_replication,
)
from products.fbasecman.deployment.profile import legacy_root, profile_paths
from products.fbasecman.reports.artifacts import CaseProgressObserver, sync_current_results


CASE_TARGETS = frozenset(
    item["target"] for item in json.loads(
        (Path(__file__).parent / "regression" / "catalog.json").read_text(encoding="utf-8")
    )["cases"]
)


def native_case_context(settings, environment):
    """Shared context for native fbasecman cases (binary/license/topology/assets)."""
    topology = configured_topology(settings, environment)
    primaries = {node.get("group"): node for node in topology["nodes"]
                 if node.get("role") == "primary"}
    if "mmr1" not in primaries or "mmr2" not in primaries:
        raise RuntimeError("fbasecman 原生用例需要 mmr1/mmr2 两个主节点")
    runtime_file = settings.product_regress_root("fbasecman") / "regress.yaml"
    runtime = yaml.safe_load(runtime_file.read_text(encoding="utf-8"))
    fbasecman = runtime.get("fbasecman", {})
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    proxy_port = listener.getsockname()[1]
    listener.close()
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", proxy_port + 2))
    except OSError as exc:
        raise RuntimeError("代理端口相邻的 metrics 端口已占用，请重试") from exc
    finally:
        probe.close()
    context = {
        "nodes": {"mmr1": {"host": primaries["mmr1"]["host"],
                            "port": primaries["mmr1"]["port"]},
                  "mmr2": {"host": primaries["mmr2"]["host"],
                            "port": primaries["mmr2"]["port"]}},
        "local_host": os.environ.get("FBCMAN_LOCAL_HOST") or "127.0.0.1",
        "user": environment.get("database_user") or "postgres",
        "fbasecman_bin": os.environ.get("PRODUCT_PLATFORM_FBASECMAN_BIN")
        or fbasecman.get("fbasecman_bin"),
        "license_dir": os.environ.get("PRODUCT_PLATFORM_FBASECMAN_LICENSE_DIR")
        or fbasecman.get("license_dir"),
        "proxy_port": proxy_port,
    }
    extras = {}
    for alias, group in (("pg_3", "mmr1"), ("pg_4", "mmr2")):
        standby = next((node for node in topology["nodes"]
                        if node.get("group") == group and node.get("role") == "standby"), None)
        if standby:
            extras[alias] = {"host": standby["host"], "port": standby["port"]}
    context["extra_nodes"] = extras
    # JDBC assets are keyed per case; inject both unconditionally so suite and
    # failed runs carry them for any native member that needs one.
    context.update({
        "sql_parse_java_asset": str(settings.product_regress_root("fbasecman") /
            "suites/ha_commands/assets/jdbc/HaSqlParseExtended.java"),
        "ha_console_java_asset": str(settings.product_regress_root("fbasecman") /
            "suites/ha_commands/assets/jdbc/HaConsoleCommands.java"),
        "jdbc_jar": str(jdbc_client.resolve_jar(
            settings.product_regress_root("fbasecman") / "lib_jdbc", None)),
    })
    # 节点远程停/起与本地 psql 路径（对齐 legacy env.config database/local 段）：
    # 环境 override 优先，产品 regress.yaml 兜底。
    try:
        _, override = profile_paths(settings, environment["id"])
        override_db = {}
        if override.is_file():
            override_db = (yaml.safe_load(override.read_text(encoding="utf-8"))
                           or {}).get("database") or {}
    except (OSError, ValueError, RuntimeError):
        override_db = {}
    merged_db = dict(runtime.get("database") or {})
    merged_db.update(override_db)
    local_pg_dir = (runtime.get("local") or {}).get("postgres_dir")
    optional = {
        "psql_bin": str(Path(local_pg_dir) / "bin" / "psql")
        if local_pg_dir else None,
        "mmr_host": merged_db.get("mmr_host"),
        "mmr_pg_user": merged_db.get("mmr_pg_user"),
        "mmr_data_root": merged_db.get("mmr_data_root")
        or merged_db.get("mmr_postgres_dir"),
        "mmr_bin_dir": str(Path(merged_db["mmr_postgres_dir"]) / "bin")
        if merged_db.get("mmr_postgres_dir") else None,
    }
    context.update({key: value for key, value in optional.items()
                    if value is not None})
    return context


def suite_case_context(settings, environment):
    """Best-effort native context for suite/failed runs containing native cases.

    Suites mix legacy and native members; when the topology or binary inputs
    are missing the native members report BLOCKED themselves, so failures here
    degrade to an empty mapping instead of failing the whole run.
    """
    try:
        return native_case_context(settings, environment)
    except (RuntimeError, ValueError, FileNotFoundError, OSError):
        return {}


class FbasecmanProvider:
    """Product rules and commands; task lifecycle belongs to the platform."""

    def validate_target(self, settings, target):
        if target in {"all", "failed"}:
            return True
        valid = {case["target"] for case in self.discover(settings)}
        valid.update(case.split(".", 1)[0] for case in tuple(valid))
        return target in valid

    def discover(self, settings):
        path = Path(__file__).parent / "regression" / "catalog.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise ValueError("fbasecman 用例目录版本无效")
        return payload["cases"]

    def observe_database(self, environment):
        # The common executor checks the configured endpoint. Proxy-specific
        # monitor and routing facts are collected by case progress observers.
        return []

    def observe_runtime(self, settings, environment):
        return []

    def progress_observer(self, settings, environment, action, target, started_at):
        if action != "tests.fbasecman":
            return None
        return CaseProgressObserver(settings, environment["id"], target, started_at)

    def publish_result(self, store, settings, environment, task, terminal, reason):
        """Publish only reports updated by this execution before task terminal."""
        if task["action"] != "tests.fbasecman":
            return terminal, reason
        if "." not in task["target"] and task["target"] not in {"all", "failed"}:
            aggregate = settings.environment_dir / "regression" / environment["id"] / task["target"] / "suite-result.json"
            try:
                payload = json.loads(aggregate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = {}
            rows = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows:
                fallback = "批量回归未生成聚合结果"
                store.put_result(environment["product_id"], environment["id"], task["target"],
                                 "default", "PASS" if terminal == "SUCCEEDED" else "ERROR", fallback,
                                 str(settings.platform_dir / "operations" / (task["id"] + ".log")))
                return terminal, fallback
            for row in rows:
                target = row.get("target")
                if target not in CASE_TARGETS or row.get("operation_id") != task["id"]:
                    continue
                artifact = settings.environment_dir / "regression" / environment["id"] / target
                store.put_result(environment["product_id"], environment["id"], target,
                                 "default", row.get("verdict", "ERROR"), row.get("reason"), str(artifact))
            failed = payload.get("counts", {}).get("FAIL", 0) + payload.get("counts", {}).get("ERROR", 0)
            return ("FAILED" if failed else terminal), reason
        if task["target"] in CASE_TARGETS:
            output = settings.environment_dir / "regression" / environment["id"] / task["target"]
            result_path = output / "result.json"
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result = {}
            if result.get("target") != task["target"] or result.get("operation_id") != task["id"]:
                reason = "本次没有生成可核对的平台回归结果；见命令日志"
                store.put_result(environment["product_id"], environment["id"], task["target"],
                                 "default", "ERROR", reason,
                                 str(settings.platform_dir / "operations" / (task["id"] + ".log")))
                return ("FAILED" if terminal == "SUCCEEDED" else terminal), reason
            reason = result.get("reason") or reason
            store.put_result(environment["product_id"], environment["id"], task["target"],
                             "default", result["verdict"], reason, str(output))
            return terminal, reason
        count = sync_current_results(
            store, settings, environment, task["target"],
            task["started_at"] or task["created_at"],
            operation_id=task["id"],
        )
        if count:
            return terminal, reason
        reason = "本次没有生成可核对的用例报告；见命令日志"
        store.put_result(
            environment["product_id"], environment["id"], task["target"],
            "default", "ERROR", reason,
                str(settings.platform_dir / "operations" / (task["id"] + ".log")),
        )
        return ("FAILED" if terminal == "SUCCEEDED" else terminal), reason

    def parse_runtime(self, name, text):
        parsers = {
            "node_status": parse_node_status,
            "group_routing": parse_group_routing,
            "node_monitor": parse_node_monitor,
            "monitor_config": parse_monitor_config,
            "group_members": parse_group_members,
            "nodes": parse_nodes,
            "groups": parse_groups,
            "replication": parse_replication,
        }
        parser = parsers.get(name)
        if parser is None:
            raise ValueError("fbasecman 未注册观测: %s" % name)
        return parser(text)

    def command(self, settings, environment, action, target, parameters):
        if action == "tests.fbasecman":
            profile, override = profile_paths(settings, environment["id"])
            if not profile.is_file() or not override.is_file():
                raise RuntimeError("请先生成 pgcluster 回归部署方案")
            from products.fbasecman.cases import NATIVE_CASES
            native_target = target in NATIVE_CASES
            legacy_context = legacy_root(settings, environment["id"]) / "output" / "env" / "test_context.yaml"
            if not native_target and not legacy_context.is_file():
                raise RuntimeError("pgcluster 部署后仍需准备 fbasecman 测试夹具和 test_context.yaml")
            if target in CASE_TARGETS:
                output = settings.environment_dir / "regression" / environment["id"] / target
                case_context = {
                    "legacy_source": str(settings.product_regress_root("fbasecman")),
                    "legacy_override": str(override),
                    "legacy_report_root": str(legacy_root(settings, environment["id"])),
                    "history_root": str(settings.environment_dir / "regression" / environment["id"]),
                }
                if native_target:
                    case_context.update(
                        native_case_context(settings, environment))
                return CommandSpec([
                    sys.executable, "-m", "platform_regress.cli",
                    "--product-dir", str(Path(__file__).resolve().parent),
                    "--output-dir", str(output),
                    "--context-json", json.dumps(case_context), target,
                ], settings.data_dir)
            if target not in {"all", "failed"} and "." not in target:
                output = settings.environment_dir / "regression" / environment["id"] / target
                case_context = {
                    "legacy_source": str(settings.product_regress_root("fbasecman")),
                    "legacy_override": str(override),
                    "legacy_report_root": str(legacy_root(settings, environment["id"])),
                    "history_root": str(settings.environment_dir / "regression" / environment["id"]),
                }
                case_context.update(suite_case_context(settings, environment))
                return CommandSpec([
                    sys.executable, "-m", "platform_regress.cli",
                    "--product-dir", str(Path(__file__).resolve().parent),
                    "--output-dir", str(output), "--context-json", json.dumps(case_context),
                    "--suite", target,
                ], settings.data_dir)
            if target == "failed":
                output = settings.environment_dir / "regression" / environment["id"]
                case_context = {
                    "legacy_source": str(settings.product_regress_root("fbasecman")),
                    "legacy_override": str(override),
                    "legacy_report_root": str(legacy_root(settings, environment["id"])),
                    "history_root": str(settings.environment_dir / "regression" / environment["id"]),
                }
                case_context.update(suite_case_context(settings, environment))
                return CommandSpec([
                    sys.executable, "-m", "platform_regress.cli",
                    "--product-dir", str(Path(__file__).resolve().parent),
                    "--output-dir", str(output), "--context-json", json.dumps(case_context),
                    "failed",
                ], settings.data_dir)
            # `all` runs through the platform engine too: the legacy run.py
            # entrypoint does not know the native cases, so dropping it here is
            # what lets suite-level coverage reach all 212 catalog targets.
            output = settings.environment_dir / "regression" / environment["id"]
            case_context = {
                "legacy_source": str(settings.product_regress_root("fbasecman")),
                "legacy_override": str(override),
                "legacy_report_root": str(legacy_root(settings, environment["id"])),
                "history_root": str(settings.environment_dir / "regression" / environment["id"]),
            }
            case_context.update(suite_case_context(settings, environment))
            return CommandSpec([
                sys.executable, "-m", "platform_regress.cli",
                "--product-dir", str(Path(__file__).resolve().parent),
                "--output-dir", str(output), "--context-json", json.dumps(case_context),
            ], settings.data_dir)
        if action == "tests.prepare_fbasecman":
            profile, override = profile_paths(settings, environment["id"])
            if not profile.is_file() or not override.is_file():
                raise RuntimeError("请先生成 pgcluster 测试部署方案")
            return CommandSpec([
                sys.executable, "-m", "products.fbasecman.deployment.fixture",
                "--profile", str(profile), "--override", str(override),
            ], settings.data_dir)
        if action == "stability.fbasecman":
            product = discover_products(settings.products_root)["fbasecman"]
            script = product.cli_path("stable")
            command = [str(script), "run"]
            if target != "all":
                command.append(target)
            return CommandSpec(command, settings.product_regress_root("fbasecman"))
        raise ValueError("fbasecman 未注册操作: %s" % action)


PROVIDER = FbasecmanProvider()
