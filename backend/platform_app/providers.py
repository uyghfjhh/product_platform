"""Product action providers.

The operation runner owns lifecycle and evidence publication. Providers own
product-specific command construction and precondition checks.
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Settings
from .product_adapters.fbasecman import legacy_root, profile_paths
from .product_adapters.fbasecman.observations import (
    ParsedObservation,
    parse_group_routing,
    parse_node_monitor,
    parse_monitor_config,
    parse_group_members,
    parse_nodes,
    parse_groups,
    parse_node_status,
    parse_replication,
)


@dataclass(frozen=True)
class CommandSpec:
    command: list[str]
    cwd: Path


@dataclass(frozen=True)
class Observation:
    kind: str
    state: str
    details: dict


class ProductProvider(Protocol):
    def command(self, settings: Settings, environment: dict, action: str,
                target: str, parameters: dict) -> CommandSpec: ...

    def discover(self, settings: Settings) -> list[dict]: ...

    def observe_database(self, environment: dict) -> list[Observation]: ...

    def parse_runtime(self, name: str, text: str) -> list[ParsedObservation]: ...

    def observe_runtime(self, settings: Settings, environment: dict) -> list[ParsedObservation]: ...

    def validate_target(self, settings: Settings, target: str) -> bool: ...


CMAN_DISCOVERY = """import json
from suites.registry import get_default_registry
items = []
for suite in get_default_registry().all_suites():
    for case in suite.get_cases():
        summary = getattr(case, "summary", "") or (case.notes[0] if getattr(case, "notes", None) else "")
        items.append({"suite": suite.id, "suite_title": getattr(suite, "title", suite.id),
            "suite_description": getattr(suite, "description", ""), "target": case.target,
            "name": getattr(case, "name", case.target.split(".")[-1]), "title": summary or case.target,
            "core_id": getattr(case, "core_id", "") or "", "summary": summary,
            "enabled": bool(getattr(case, "enabled", True)), "tags": list(getattr(case, "tags", []) or [])})
print(json.dumps(items, ensure_ascii=False))
"""

FBASE_DISCOVERY = """import json
from suites import SUITES
items = []
for suite_id, suite in SUITES.items():
    for case in suite["cases"]:
        items.append({"suite": suite_id, "suite_title": suite.get("name", suite_id),
            "suite_description": suite.get("description", ""), "target": case["id"],
            "name": case.get("id"), "title": case.get("name", case["id"]),
            "core_id": case.get("core_id", ""), "summary": case.get("name", case["id"]),
            "enabled": True, "tags": [case.get("group", "")]})
print(json.dumps(items, ensure_ascii=False))
"""


def _discover(settings: Settings, root: Path, script: str) -> list[dict]:
    if not root.is_dir():
        raise FileNotFoundError("用例来源目录不存在: %s" % root)
    repo_root = root.parents[1] if root.name == "fbasecman" else root.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{root}{os.pathsep}{repo_root}{os.pathsep}{repo_root / 'backend'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    result = subprocess.run([sys.executable, "-c", script], cwd=root,
                            capture_output=True, text=True, env=env, timeout=60,
                            check=False)
    if result.returncode != 0:
        raise RuntimeError("读取用例目录失败: %s" % (result.stderr.strip() or result.returncode))
    return json.loads(result.stdout)


def _deployment_command(settings: Settings, environment: dict, action: str,
                        target: str) -> CommandSpec:
    config_file = Path(environment.get("deployment_config") or "")
    if not config_file.is_file():
        raise FileNotFoundError("部署配置不存在: %s" % config_file)
    pgcluster = settings.pgcluster_root / "pgcluster"
    if not pgcluster.is_file():
        raise FileNotFoundError("pgcluster 入口不存在: %s" % pgcluster)
    cli_action = action.partition(".")[2]
    if cli_action == "heal":
        return CommandSpec([
            sys.executable, str(Path(__file__).with_name("pgcluster_heal.py")),
            str(pgcluster), str(config_file), target,
        ], settings.pgcluster_root)
    command = [sys.executable, str(pgcluster), "-f", str(config_file),
               cli_action]
    if cli_action != "doctor":
        command.append(target)
    if cli_action in {"clean", "failover", "rejoin"}:
        command.append("--yes")
    return CommandSpec(command, settings.pgcluster_root)


class FbasecmanProvider:
    def validate_target(self, settings: Settings, target: str) -> bool:
        if target == "failed":
            return True
        valid = {case["target"] for case in self.discover(settings)}
        valid.update(case.split(".", 1)[0] for case in tuple(valid))
        return target in valid

    def observe_runtime(self, settings: Settings, environment: dict) -> list[ParsedObservation]:
        # The regression case owns its temporary proxy. Its console output is
        # captured by CaseProgressObserver while the case is running.
        return []

    def parse_runtime(self, name: str, text: str) -> list[ParsedObservation]:
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

    def observe_database(self, environment: dict) -> list[Observation]:
        # The proxy endpoint is intentionally probed by the common executor;
        # product-specific monitor and routing probes will be added here.
        return []

    def discover(self, settings: Settings) -> list[dict]:
        return _discover(settings, settings.fbasecman_regress_root, CMAN_DISCOVERY)

    def command(self, settings: Settings, environment: dict, action: str,
                target: str, parameters: dict) -> CommandSpec:
        if action == "tests.fbasecman":
            profile, override = profile_paths(settings, environment["id"])
            if not profile.is_file() or not override.is_file():
                raise RuntimeError("请先生成 pgcluster 回归部署方案")
            context = legacy_root(settings, environment["id"]) / "output" / "env" / "test_context.yaml"
            if not context.is_file():
                raise RuntimeError("pgcluster 部署后仍需准备 fbasecman 测试夹具和 test_context.yaml")
            return CommandSpec([
                sys.executable, "-m", "platform_app.product_adapters.fbasecman.legacy_runner",
                "--source", str(settings.fbasecman_regress_root),
                "--override", str(override), target,
            ], settings.fbasecman_regress_root)
        if action == "tests.prepare_fbasecman":
            profile, override = profile_paths(settings, environment["id"])
            if not profile.is_file() or not override.is_file():
                raise RuntimeError("请先生成 pgcluster 测试部署方案")
            return CommandSpec([
                sys.executable, "-m", "platform_app.product_adapters.fbasecman.fixture",
                "--profile", str(profile), "--override", str(override),
            ], settings.data_dir)
        if action == "stability.fbasecman":
            script = settings.fbasecman_regress_root / "stable.sh"
            if not script.is_file():
                raise FileNotFoundError("fbasecman 常稳入口不存在: %s" % script)
            command = [str(script), "run"]
            if target != "all":
                command.append(target)
            return CommandSpec(command, settings.fbasecman_regress_root)
        raise ValueError("fbasecman 未注册操作: %s" % action)


class FbaseProvider:
    def validate_target(self, settings: Settings, target: str) -> bool:
        return target == "all" or any(case["target"] == target for case in self.discover(settings))

    def observe_runtime(self, settings: Settings, environment: dict) -> list[ParsedObservation]:
        import psycopg

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

    def parse_runtime(self, name: str, text: str) -> list[ParsedObservation]:
        if name != "replication":
            raise ValueError("FBase 未注册观测: %s" % name)
        return parse_replication(text)

    def observe_database(self, environment: dict) -> list[Observation]:
        import psycopg

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

    def discover(self, settings: Settings) -> list[dict]:
        return _discover(settings, settings.fbase_regress_root, FBASE_DISCOVERY)

    def command(self, settings: Settings, environment: dict, action: str,
                target: str, parameters: dict) -> CommandSpec:
        if action != "tests.fbase":
            raise ValueError("FBase 未注册操作: %s" % action)
        script = settings.fbase_regress_root / "run.sh"
        if not script.is_file():
            raise FileNotFoundError("FBase 测试入口不存在: %s" % script)
        cluster = parameters.get("cluster")
        if cluster not in {"mac", "mmr"}:
            raise ValueError("FBase 测试需要选择 mac 或 mmr 集群")
        command = [str(script), "run", cluster]
        if target != "all":
            command.append(target)
        return CommandSpec(command, settings.fbase_regress_root)


PROVIDERS: dict[str, ProductProvider] = {
    "fbasecman": FbasecmanProvider(),
    "fbase-database": FbaseProvider(),
}


def command_for(settings: Settings, environment: dict, action: str,
                target: str, parameters: dict) -> CommandSpec:
    if action.startswith("deployment."):
        return _deployment_command(settings, environment, action, target)
    provider = PROVIDERS.get(environment["product_id"])
    if provider is None:
        raise ValueError("产品未注册提供者: %s" % environment["product_id"])
    return provider.command(settings, environment, action, target, parameters)


def observe_database(environment: dict) -> list[Observation]:
    provider = PROVIDERS.get(environment["product_id"])
    if provider is None:
        raise ValueError("产品未注册提供者: %s" % environment["product_id"])
    return provider.observe_database(environment)


def observe_runtime(settings: Settings, environment: dict) -> list[ParsedObservation]:
    provider = PROVIDERS.get(environment["product_id"])
    if provider is None:
        raise ValueError("产品未注册提供者: %s" % environment["product_id"])
    return provider.observe_runtime(settings, environment)


def validate_target(settings: Settings, product_id: str, target: str) -> bool:
    provider = PROVIDERS.get(product_id)
    if provider is None:
        raise ValueError("产品未注册提供者: %s" % product_id)
    return provider.validate_target(settings, target)
