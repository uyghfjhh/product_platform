"""Shared provider contracts and dispatch for installed product packages.

Product commands, discovery scripts, and observations belong in each product
adapter. This module owns only common types, process isolation, and dispatch.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

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
        if cli_action == "change":
            plan_id = environment.get("_deployment_plan_id")
            if not plan_id:
                raise ValueError("集群变更缺少已审阅计划")
            return CommandSpec([
                sys.executable, str(Path(__file__).parent / "deployment" / "change_cli.py"),
                "--engine-root", str(settings.pgcluster_root),
                "--plan", str(settings.data_dir / "deployment-plans" / plan_id / "plan.json"),
                "--checkpoint", str(settings.data_dir / "deployment-checkpoints" / (plan_id + ".json")),
            ], settings.pgcluster_root)
        if cli_action == "heal":
            return CommandSpec([
                sys.executable, str(Path(__file__).with_name("pgcluster_heal.py")),
                str(pgcluster), str(config_file),
                *(["--control-root", str(settings.data_dir), "--environment", environment["id"]] if environment.get("id") else []),
                target,
            ], settings.pgcluster_root)
        if cli_action == "reset":
            return CommandSpec([
                sys.executable, str(Path(__file__).with_name("pgcluster_reset.py")),
                str(pgcluster), str(config_file),
                *(["--control-root", str(settings.data_dir), "--environment", environment["id"]] if environment.get("id") else []),
                target,
            ], settings.pgcluster_root)
        command = [sys.executable, str(Path(__file__).with_name("pgcluster_entry.py")), str(pgcluster)]
        if environment.get("id"):
            command += ["--control-root", str(settings.data_dir), "--environment", environment["id"]]
        command += ["-f", str(config_file), cli_action]
        if cli_action != "doctor":
            command.append(target)
        if cli_action in {"clean", "failover", "rejoin", "switchover", "restore"}:
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


def _load_provider(manifest) -> ProductProvider:
    """Load only the adapter belonging to an installed product manifest."""
    product_id = manifest.id
    # Product-owned modules import their package dependencies lazily as well.
    # The registry owns that import root, independent of the process cwd.
    import_root = str(manifest.package_root.parent.parent)
    if import_root not in sys.path:
        sys.path.append(import_root)
    if manifest.provider_path is None:
        raise ValueError("产品未注册提供者: %s" % product_id)
    provider_path = manifest.package_root / manifest.provider_path
    if not provider_path.is_file():
        raise ValueError("产品未注册提供者: %s" % product_id)
    module_name = "_platform_product_" + hashlib.sha256(str(provider_path.resolve()).encode()).hexdigest()[:24]
    spec = importlib.util.spec_from_file_location(module_name, provider_path)
    if spec is None or spec.loader is None:
        raise ValueError("无法加载产品提供者: %s" % product_id)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        exec(compile(provider_path.read_bytes(), str(provider_path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    provider = getattr(module, "PROVIDER", None)
    required = ("command", "discover", "observe_database", "observe_runtime", "validate_target")
    if provider is None or any(not callable(getattr(provider, name, None)) for name in required):
        raise ValueError("产品提供者接口不完整: %s" % product_id)
    return provider


class RegressionProvider(Protocol):
    def publish_result(self, store, settings, environment, task, terminal, reason): ...


class DeploymentTemplateProvider(Protocol):
    def deployment_templates(self, settings): ...
    def compile_deployment(self, settings, spec, environment_id): ...


@dataclass(frozen=True)
class ProviderExtensions:
    deployment: DeploymentTemplateProvider | None = None
    regression: RegressionProvider | None = None
    import_files: Callable | None = None
    invalidate: Callable | None = None
    after_command: Callable | None = None
    progress_observer: Callable | None = None
    workload_catalog: Callable | None = None
    workload_runtime: Callable | None = None


def _extensions(manifest, provider):
    def hook(name):
        value = getattr(provider, name, None)
        if value is not None and not callable(value):
            raise ValueError(f"产品钩子不可调用: {manifest.id}.{name}")
        return value
    templates = hook("deployment_templates")
    compiler = hook("compile_deployment")
    if bool(templates) != bool(compiler):
        raise ValueError(f"部署能力必须同时提供模板和编译器: {manifest.id}")
    publisher = hook("publish_result")
    if "tests" in manifest.capabilities and not publisher:
        raise ValueError(f"回归能力缺少 publish_result: {manifest.id}")
    return ProviderExtensions(
        deployment=provider if templates else None,
        regression=provider if publisher else None,
        import_files=hook("deployment_import_files"),
        invalidate=hook("deployment_invalidate"),
        after_command=hook("after_command"),
        progress_observer=hook("progress_observer"),
        workload_catalog=hook('workload_catalog'),
        workload_runtime=hook('workload_runtime'),
    )


class ProductRegistry:
    """One provider instance per installed package revision, with explicit refresh."""
    def __init__(self, root):
        self.root = Path(root)
        self._entries = {}
        self._lock = threading.RLock()

    def validate_installed(self):
        for manifest in discover_products(self.root).values():
            if manifest.provider_path is not None:
                self.entry(manifest.id)

    def refresh(self):
        with self._lock:
            self._entries.clear()

    def entry(self, product_id):
        with self._lock:
            manifest = discover_products(self.root).get(product_id)
            if manifest is None:
                self._entries.pop(product_id, None)
                raise ValueError("产品未安装: " + product_id)
            paths = [manifest.package_root / "product.yaml"]
            if manifest.provider_path:
                paths.append(manifest.package_root / manifest.provider_path)
            revision = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size, p.stat().st_ino)
                             for p in paths if p.is_file())
            cached = self._entries.get(product_id)
            if cached is None or cached[0] != revision:
                if cached is not None:
                    for name, module in list(sys.modules.items()):
                        path=getattr(module,'__file__',None)
                        if path and Path(path).resolve().is_relative_to(manifest.package_root.resolve()):
                            sys.modules.pop(name,None)
                provider = _load_provider(manifest)
                cached = (revision, provider, _extensions(manifest, provider))
                self._entries[product_id] = cached
            return cached[1:]


_REGISTRIES = {}
_REGISTRY_LOCK = threading.Lock()


def registry_for(settings):
    root = settings.products_root.resolve()
    with _REGISTRY_LOCK:
        return _REGISTRIES.setdefault(root, ProductRegistry(root))


def provider_for(settings: Settings, product_id: str) -> ProductProvider:
    return registry_for(settings).entry(product_id)[0]


def provider_extensions(settings, product_id) -> ProviderExtensions:
    return registry_for(settings).entry(product_id)[1]




def command_for(settings: Settings, environment: dict, action: str,
                target: str, parameters: dict) -> CommandSpec:
    # A queued deployment can reach the worker after its product is removed.
    if environment["product_id"] not in discover_products(settings.products_root):
        raise ValueError("产品未安装: %s" % environment["product_id"])
    if action.startswith("deployment."):
        return DATABASE_CLUSTER_PROVIDER.lifecycle(settings, environment, action, target).command
    if action in {'workload.pgbench','workload.jdbc'}:
        from .workloads import command
        return command(settings,environment,action,parameters)
    return provider_for(settings, environment["product_id"]).command(
        settings, environment, action, target, parameters,
    )


def observe_database(settings: Settings, environment: dict) -> list[Observation]:
    return provider_for(settings, environment["product_id"]).observe_database(environment)


def observe_runtime(settings: Settings, environment: dict) -> list:
    return provider_for(settings, environment["product_id"]).observe_runtime(settings, environment)


def validate_target(settings: Settings, product_id: str, target: str) -> bool:
    return provider_for(settings, product_id).validate_target(settings, target)
