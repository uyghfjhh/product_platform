"""FBase database test and PostgreSQL runtime observation provider."""

import json
import importlib.util
import sys
from pathlib import Path

import psycopg

from platform_app.providers import CommandSpec, Observation
from platform_app.replication_observations import parse_replication
from platform_app.event_contracts import SceneObservationError
from platform_app.scene import emit_observation, endpoint_id
from platform_app.topology import configured_topology


SHARED_ENGINE_TARGETS = frozenset({
    "mmr.installation.runtime_prerequisites",
    "mmr.cluster_verification.basic",
    "mmr.cluster_verification.same_priority_errors",
    "mac.separation_of_duties.dba_metadata_access_restrictions",
    "mac.separation_of_duties.dba_metadata_function_restrictions",
    "mac.separation_of_duties.dba_metadata_index_restrictions",
    "mac.separation_of_duties.dba_metadata_sequence_restrictions",
    "mac.separation_of_duties.dba_metadata_view_restrictions",
    "mac.separation_of_duties.dba_security_configuration_restrictions",
    "mac.tlcp.server_client_generation",
    "mmr.node_function_control.two_phase_change_unsupported",
    "mac.password.account_rename",
    "mac.separation_of_duties.role_membership_restrictions",
    "mac.separation_of_duties.sao_role_membership_restrictions",
    "mac.separation_of_duties.sso_role_membership_restrictions",
    "mac.mac.table_creation_and_grants",
    "mac.separation_of_duties.dba_object_privilege_separation",
    "mac.audit.server_audit_logs",
})


def migrated_cases():
    """Load metadata from this product's new SDK case module."""
    path = Path(__file__).with_name("cases.py")
    spec = importlib.util.spec_from_file_location("_fbase_platform_cases", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 FBase 平台用例")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CASE_METADATA


def exported_cases():
    """Discover the full product catalog without importing the old executor."""
    path = Path(__file__).parent / "regression" / "cases.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("FBase 用例目录版本无效")
    suites = payload["suites"]
    return [{
        "suite": case["id"].split(".", 1)[0],
        "suite_title": suites[case["id"].split(".", 1)[0]]["title"],
        "suite_description": suites[case["id"].split(".", 1)[0]]["description"],
        "target": case["id"], "name": case["id"],
        "title": case.get("name", case["id"]),
        "core_id": case.get("core_id", ""),
        "summary": case.get("name", case["id"]),
        "enabled": True, "tags": [case.get("group", "")],
    } for case in payload["cases"]]


ALL_CASE_TARGETS = frozenset(case["target"] for case in exported_cases())


class FbaseProvider:
    """FBase target validation, test command, and database observations."""

    def validate_target(self, settings, target):
        targets = {case["target"] for case in exported_cases()}
        return target == "all" or target in targets or any(item.startswith(target + ".") for item in targets)

    def discover(self, settings):
        return [case for case in exported_cases() if case["target"] not in SHARED_ENGINE_TARGETS] + migrated_cases()

    def after_command(self, store, settings, environment, task_id, action, success):
        """Capture database replication facts without changing test verdict."""
        if action != "tests.fbase" or not success:
            return
        try:
            for observation in self.observe_runtime(settings, environment):
                emit_observation(
                    store, task_id,
                    observation.details.get("entity_id", endpoint_id(environment)),
                    observation.state, observation.kind, observation.details,
                )
        except Exception as exc:  # noqa: BLE001 - observation is supplemental
            store.add_event(task_id, "scene.observation.error", SceneObservationError(
                source="product.runtime", message=str(exc),
            ).model_dump())

    def publish_result(self, store, settings, environment, task, terminal, reason):
        if task["action"] != "tests.fbase":
            return terminal, reason
        parameters = json.loads(task["parameters"])
        if "." not in task["target"] and task["target"] != "all":
            aggregate = settings.environment_dir / "regression" / environment["id"] / task["target"] / "suite-result.json"
            try:
                payload = json.loads(aggregate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = {}
            rows = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows:
                fallback = "批量回归未生成聚合结果"
                store.put_result(environment["product_id"], environment["id"], task["target"],
                                 parameters.get("profile", "default"),
                                 "PASS" if terminal == "SUCCEEDED" else "ERROR", fallback,
                                 str(settings.platform_dir / "operations" / (task["id"] + ".log")))
                return terminal, fallback
            for row in rows:
                target = row.get("target")
                if target not in ALL_CASE_TARGETS or row.get("operation_id") != task["id"]:
                    continue
                artifact = settings.environment_dir / "regression" / environment["id"] / target
                store.put_result(environment["product_id"], environment["id"], target,
                                 parameters.get("profile", "default"), row.get("verdict", "ERROR"),
                                 row.get("reason"), str(artifact))
            failed = payload.get("counts", {}).get("FAIL", 0) + payload.get("counts", {}).get("ERROR", 0)
            return ("FAILED" if failed else terminal), reason
        if task["target"] in ALL_CASE_TARGETS:
            output = settings.environment_dir / "regression" / environment["id"] / task["target"]
            result_path = output / "result.json"
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result = {}
            if result.get("target") != task["target"] or result.get("operation_id") != task["id"]:
                reason = "本次没有生成可核对的平台回归结果；见命令日志"
                store.put_result(
                    environment["product_id"], environment["id"], task["target"],
                    parameters.get("profile", "default"), "ERROR", reason,
                    str(settings.platform_dir / "operations" / (task["id"] + ".log")),
                )
                return ("FAILED" if terminal == "SUCCEEDED" else terminal), reason
            reason = result.get("reason") or reason
            store.put_result(
                environment["product_id"], environment["id"], task["target"],
                parameters.get("profile", "default"), result["verdict"], reason,
                str(output),
            )
            return terminal, reason
        store.put_result(
            environment["product_id"], environment["id"], task["target"],
            parameters.get("profile", "default"),
            "PASS" if terminal == "SUCCEEDED" else "ERROR", reason,
            str(settings.platform_dir / "operations" / (task["id"] + ".log")),
        )
        return terminal, reason

    def observe_runtime(self, settings, environment):
        connection = psycopg.connect(
            host=environment["host"], port=environment["port"],
            dbname=environment.get("database_name") or "postgres",
            user=environment.get("database_user") or "postgres",
            connect_timeout=4, options="-c statement_timeout=4000",
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT application_name, client_addr::text, state, sync_state "
                    "FROM pg_stat_replication ORDER BY application_name"
                )
                rows = cursor.fetchall()
            text = "application_name\tclient_addr\tstate\tsync_state\n" + "\n".join(
                "\t".join("" if value is None else str(value) for value in row)
                for row in rows
            )
            return self.parse_runtime("replication", text)
        finally:
            connection.close()

    def parse_runtime(self, name, text):
        if name != "replication":
            raise ValueError("FBase 未注册观测: %s" % name)
        return parse_replication(text)

    def observe_database(self, environment):
        connection = psycopg.connect(
            host=environment["host"], port=environment["port"],
            dbname=environment["database_name"], user=environment["database_user"],
            connect_timeout=4, options="-c statement_timeout=4000",
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
            return [Observation("sql.version", "ready", {"version": version})]
        finally:
            connection.close()

    def command(self, settings, environment, action, target, parameters):
        if action != "tests.fbase":
            raise ValueError("FBase 未注册操作: %s" % action)
        cluster = parameters.get("cluster")
        if cluster not in {"mac", "mmr"}:
            raise ValueError("FBase 测试需要选择 mac 或 mmr 集群")
        if target in ALL_CASE_TARGETS:
            if cluster != target.split(".", 1)[0]:
                raise ValueError("用例目标与所选集群不一致")
            nodes, topology_error = {}, None
            if target in SHARED_ENGINE_TARGETS:
                try:
                    topology = configured_topology(settings, environment)
                    if cluster == "mmr":
                        primary = {
                            node.get("group"): node for node in topology["nodes"]
                            if node.get("role") == "primary"
                        }
                        nodes = {
                            name: {"host": primary[name]["host"], "port": primary[name]["port"]}
                            for name in ("mmr1", "mmr2", "mmr3") if name in primary
                        }
                    else:
                        primary = [node for node in topology["nodes"] if node.get("role") == "primary"]
                        if len(primary) != 1:
                            raise ValueError("等保测试需要唯一的可写主节点")
                        nodes = {"primary": {"host": primary[0]["host"], "port": primary[0]["port"]}}
                except (ValueError, FileNotFoundError) as exc:
                    topology_error = str(exc)
            context = {"nodes": nodes, "user": environment.get("database_user") or "postgres",
                       "users": environment.get("database_users") or {},
                       "legacy_source": str(settings.fbase_regress_root)}
            if topology_error:
                context["topology_error"] = topology_error
            output = settings.environment_dir / "regression" / environment["id"] / target
            target_args = ["--suite", target] if "." not in target else [target]
            return CommandSpec([
                sys.executable, "-m", "platform_regress.cli",
                "--product-dir", str(Path(__file__).resolve().parent),
                "--output-dir", str(output),
                "--context-json", json.dumps(context, ensure_ascii=False), *target_args,
            ], settings.data_dir)
        if "." not in target and target != "all":
            output = settings.environment_dir / "regression" / environment["id"] / target
            return CommandSpec([
                sys.executable, "-m", "platform_regress.cli",
                "--product-dir", str(Path(__file__).resolve().parent),
                "--output-dir", str(output), "--context-json", json.dumps({
                    "legacy_source": str(settings.fbase_regress_root),
                }, ensure_ascii=False), "--suite", target,
            ], settings.data_dir)
        script = settings.fbase_regress_root / "run.sh"
        if not script.is_file():
            raise FileNotFoundError("FBase 测试入口不存在: %s" % script)
        command = [str(script), "run", cluster]
        if target != "all":
            command.append(target)
        return CommandSpec(command, settings.fbase_regress_root)


PROVIDER = FbaseProvider()
