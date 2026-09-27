"""Shared provider contracts and dispatch for installed product packages.

Product commands, discovery scripts, and observations belong in each product
adapter. This module owns only common types, process isolation, and dispatch.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Settings
from .product_catalog import discover_products


@dataclass(frozen=True)
class CommandSpec:
    command: list[str]
    cwd: Path


@dataclass(frozen=True)
class Observation:
    kind: str
    state: str
    details: dict


@dataclass(frozen=True)
class DeploymentPlan:
    config_file: Path
    target: str
    action: str
    command: CommandSpec


class DatabaseClusterProvider(Protocol):
    """Platform-owned database environment contract.

    Products declare topology and binaries; this provider owns the pgcluster
    lifecycle and returns a command plan without changing task state.
    """

    def validate(self, settings: Settings, environment: dict, target: str) -> DeploymentPlan: ...
    def plan(self, settings: Settings, environment: dict, action: str, target: str) -> DeploymentPlan: ...
    def apply(self, plan: DeploymentPlan) -> CommandSpec: ...
    def inspect(self, settings: Settings, environment: dict, target: str) -> dict: ...
    def lifecycle(self, settings: Settings, environment: dict, action: str, target: str) -> DeploymentPlan: ...


class ProductProvider(Protocol):
    def command(self, settings: Settings, environment: dict, action: str,
                target: str, parameters: dict) -> CommandSpec: ...

    def discover(self, settings: Settings) -> list[dict]: ...

    def observe_database(self, environment: dict) -> list[Observation]: ...

    def observe_runtime(self, settings: Settings, environment: dict) -> list: ...

    def validate_target(self, settings: Settings, target: str) -> bool: ...


def _discover(settings: Settings, root: Path, script: str) -> list[dict]:
    """Run legacy suite discovery in a separate interpreter.

    Both migrated source trees currently expose a top-level ``framework``
    package. Importing them into the API process would mix incompatible
    modules, so discovery remains isolated until their suites use the SDK.
    """
    if not root.is_dir():
        raise FileNotFoundError("用例来源目录不存在: %s" % root)
    repo_root = settings.products_root.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((
        str(root), str(repo_root), str(repo_root / "backend"), env.get("PYTHONPATH", ""),
    ))
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root, capture_output=True,
        text=True, env=env, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("读取用例目录失败: %s" % (result.stderr.strip() or result.returncode))
    return json.loads(result.stdout)


class PgclusterDatabaseProvider:
    """The single database cluster implementation used by the platform."""

    @staticmethod
    def _command(settings: Settings, environment: dict, action: str,
                 target: str) -> CommandSpec:
        """Translate the platform database lifecycle action to pgcluster CLI."""
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
        command = [sys.executable, str(pgcluster), "-f", str(config_file), cli_action]
        if cli_action != "doctor":
            command.append(target)
        if cli_action in {"clean", "failover", "rejoin"}:
            command.append("--yes")
        return CommandSpec(command, settings.pgcluster_root)

    def plan(self, settings: Settings, environment: dict, action: str,
             target: str) -> DeploymentPlan:
        command = self._command(settings, environment, action, target)
        return DeploymentPlan(Path(environment["deployment_config"]).resolve(), target, action, command)

    def validate(self, settings: Settings, environment: dict, target: str) -> DeploymentPlan:
        return self.plan(settings, environment, "deployment.validate", target)

    def lifecycle(self, settings: Settings, environment: dict, action: str,
                  target: str) -> DeploymentPlan:
        if not action.startswith("deployment."):
            raise ValueError(f"不是数据库生命周期动作: {action}")
        return self.plan(settings, environment, action, target)

    @staticmethod
    def apply(plan: DeploymentPlan) -> CommandSpec:
        return plan.command

    def inspect(self, settings: Settings, environment: dict, target: str) -> dict:
        """Return the shared topology/status shape consumed by API and Web."""
        from .topology import configured_topology, observed_status
        return {"topology": configured_topology(settings, environment),
                "status": observed_status(settings, environment)}


DATABASE_CLUSTER_PROVIDER = PgclusterDatabaseProvider()


def provider_for(settings: Settings, product_id: str) -> ProductProvider:
    """Load only the adapter belonging to an installed product manifest."""
    manifest = discover_products(settings.products_root).get(product_id)
    if manifest is None:
        raise ValueError("产品未安装: %s" % product_id)
    if manifest.provider_path is None:
        raise ValueError("产品未注册提供者: %s" % product_id)
    provider_path = manifest.package_root / manifest.provider_path
    if not provider_path.is_file():
        raise ValueError("产品未注册提供者: %s" % product_id)
    module_name = "_platform_product_" + product_id.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, provider_path)
    if spec is None or spec.loader is None:
        raise ValueError("无法加载产品提供者: %s" % product_id)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = getattr(module, "PROVIDER", None)
    required = ("command", "discover", "observe_database", "observe_runtime", "validate_target")
    if provider is None or any(not callable(getattr(provider, name, None)) for name in required):
        raise ValueError("产品提供者接口不完整: %s" % product_id)
    return provider


def command_for(settings: Settings, environment: dict, action: str,
                target: str, parameters: dict) -> CommandSpec:
    # A queued deployment can reach the worker after its product is removed.
    if environment["product_id"] not in discover_products(settings.products_root):
        raise ValueError("产品未安装: %s" % environment["product_id"])
    if action.startswith("deployment."):
        return DATABASE_CLUSTER_PROVIDER.lifecycle(settings, environment, action, target).command
    return provider_for(settings, environment["product_id"]).command(
        settings, environment, action, target, parameters,
    )


def observe_database(settings: Settings, environment: dict) -> list[Observation]:
    return provider_for(settings, environment["product_id"]).observe_database(environment)


def observe_runtime(settings: Settings, environment: dict) -> list:
    return provider_for(settings, environment["product_id"]).observe_runtime(settings, environment)


def validate_target(settings: Settings, product_id: str, target: str) -> bool:
    return provider_for(settings, product_id).validate_target(settings, target)
