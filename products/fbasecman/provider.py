"""fbasecman test, stability, and runtime observation provider."""

import json
import os
import socket
import sys
from pathlib import Path

import yaml

from platform_app.product_catalog import discover_products
from platform_app.providers import CommandSpec
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


class FbasecmanProvider:
    """Product rules and commands; task lifecycle belongs to the platform."""

    def validate_target(self, settings, target):
        if target == "failed":
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
            native_target = target in {
                "sql_parse.savepoint_recovery_after_local_25p02",
                "sql_parse.heartbeat_bind_normal",
                "sql_parse.heartbeat_bind_invalid",
                "sql_parse.heartbeat_bind_unsupported",
                "ha_commands.sql_parse_extended_protocol",
            }
            legacy_context = legacy_root(settings, environment["id"]) / "output" / "env" / "test_context.yaml"
            if not native_target and not legacy_context.is_file():
                raise RuntimeError("pgcluster 部署后仍需准备 fbasecman 测试夹具和 test_context.yaml")
            if target in CASE_TARGETS:
                output = settings.environment_dir / "regression" / environment["id"] / target
                case_context = {
                    "legacy_source": str(settings.fbasecman_regress_root),
                    "legacy_override": str(override),
                    "legacy_report_root": str(legacy_root(settings, environment["id"])),
                }
                if native_target:
                    topology = configured_topology(settings, environment)
                    primaries = {node.get("group"): node for node in topology["nodes"]
                                 if node.get("role") == "primary"}
                    if "mmr1" not in primaries or "mmr2" not in primaries:
                        raise RuntimeError("fbasecman 原生用例需要 mmr1/mmr2 两个主节点")
                    runtime_file = settings.fbasecman_regress_root / "regress.yaml"
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
                    case_context.update({
                        "nodes": {"mmr1": {"host": primaries["mmr1"]["host"],
                                            "port": primaries["mmr1"]["port"]},
                                  "mmr2": {"host": primaries["mmr2"]["host"],
                                            "port": primaries["mmr2"]["port"]}},
                        "user": environment.get("database_user") or "postgres",
                        "fbasecman_bin": os.environ.get("PRODUCT_PLATFORM_FBASECMAN_BIN")
                        or fbasecman.get("fbasecman_bin"),
                        "license_dir": os.environ.get("PRODUCT_PLATFORM_FBASECMAN_LICENSE_DIR")
                        or fbasecman.get("license_dir"),
                        "proxy_port": proxy_port,
                    })
                    if target == "ha_commands.sql_parse_extended_protocol":
                        case_context.update({
                            "sql_parse_java_asset": str(settings.fbasecman_regress_root /
                                "suites/ha_commands/assets/jdbc/HaSqlParseExtended.java"),
                            "jdbc_jar": str(settings.fbasecman_regress_root /
                                "lib_jdbc/postgresql-42.7.7.jar"),
                        })
                return CommandSpec([
                    sys.executable, "-m", "platform_regress.cli",
                    "--product-dir", str(Path(__file__).resolve().parent),
                    "--output-dir", str(output),
                    "--context-json", json.dumps(case_context), target,
                ], settings.data_dir)
            if target not in {"all", "failed"} and "." not in target:
                output = settings.environment_dir / "regression" / environment["id"] / target
                case_context = {
                    "legacy_source": str(settings.fbasecman_regress_root),
                    "legacy_override": str(override),
                    "legacy_report_root": str(legacy_root(settings, environment["id"])),
                }
                return CommandSpec([
                    sys.executable, "-m", "platform_regress.cli",
                    "--product-dir", str(Path(__file__).resolve().parent),
                    "--output-dir", str(output), "--context-json", json.dumps(case_context),
                    "--suite", target,
                ], settings.data_dir)
            return CommandSpec([
                sys.executable, str(Path(__file__).resolve().parent / "regression" / "run.py"),
                "--source", str(settings.fbasecman_regress_root),
                "--override", str(override), target,
            ], settings.fbasecman_regress_root)
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
            return CommandSpec(command, settings.fbasecman_regress_root)
        raise ValueError("fbasecman 未注册操作: %s" % action)


PROVIDER = FbasecmanProvider()
