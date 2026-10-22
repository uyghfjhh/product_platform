"""Declarative action registry, environment action resolution, and CLI command wiring."""

import json
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .product_catalog import ProductManifestError, discover_products
from .providers import command_for

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}

# These lifecycle operations belong to the platform database deployment engine.
DEPLOYMENT_ACTIONS = frozenset({
    "deployment.validate", "deployment.status", "deployment.health",
    "deployment.doctor", "deployment.heal", "deployment.create",
    "deployment.start", "deployment.stop", "deployment.restart",
    "deployment.clean", "deployment.failover", "deployment.rejoin",
    "deployment.lag", "deployment.verify", "deployment.switchover",
    "deployment.reset", "deployment.restore", "deployment.change",
})


@dataclass(frozen=True)
class Action:
    id: str
    title: str
    capability: str
    changes_environment: bool = False


ACTIONS = {
    item.id: item
    for item in (
        Action("database.check", "检查数据库连接", "database"),
        Action("workload.pgbench", "运行 pgbench 负载", "workload"),
        Action("workload.jdbc", "运行 JDBC 负载", "workload"),
        Action("deployment.validate", "校验部署配置", "deployment"),
        Action("deployment.status", "查看部署状态", "deployment"),
        Action("deployment.health", "检查集群健康", "deployment"),
        Action("deployment.doctor", "环境体检", "deployment"),
        Action("deployment.heal", "检查并恢复集群", "deployment", True),
        Action("deployment.create", "创建集群", "deployment", True),
        Action("deployment.change", "按已审阅计划变更集群", "deployment", True),
        Action("deployment.start", "启动集群", "deployment", True),
        Action("deployment.stop", "停止集群", "deployment", True),
        Action("deployment.restart", "重启集群", "deployment", True),
        Action("deployment.clean", "清理集群", "deployment", True),
        Action("deployment.reset", "重置环境（杀残留进程并重启）", "deployment", True),
        Action("deployment.failover", "备库升主", "deployment", True),
        Action("deployment.switchover", "主备切换", "deployment", True),
        Action("deployment.rejoin", "重建旧主为备库", "deployment", True),
        Action("deployment.restore", "切回原主库", "deployment", True),
        Action("deployment.lag", "检查复制延迟", "deployment"),
        Action("deployment.verify", "校验集群复制", "deployment"),
        Action("diagnostics.analyze", "AI 分析当前测试结果", "diagnostics"),
    )
}


def actions_for_environment(environment: dict, settings: Settings) -> list[dict]:
    """Return actions declared by the installed product package.

    Deployment lifecycle actions are platform capabilities and are added when
    the installed product declares deployment and has a configuration.
    """
    try:
        manifest = discover_products(settings.products_root).get(environment["product_id"])
    except ProductManifestError as exc:
        raise RuntimeError(f"产品目录校验失败: {exc}") from exc
    if manifest is None:
        return []
    declared = {item.id: item for item in manifest.actions}
    if environment.get("deployment_config") and "deployment" in manifest.capabilities:
        declared.update({item.id: item for item in ACTIONS.values() if item.id in DEPLOYMENT_ACTIONS})
    if "database" in manifest.capabilities:
        declared.update({item.id: item for item in ACTIONS.values() if item.capability == "workload"})

    def parameter_schema(item):
        if item.capability == "workload":
            from .workloads import WorkloadInput
            return WorkloadInput.model_json_schema()
        return (declared[item.id].parameter_schema or {}) if item.id not in ACTIONS else {}

    return [
        {
            "id": item.id,
            "title": item.title,
            "capability": item.capability,
            "changes_environment": item.changes_environment,
            "parameter_schema": parameter_schema(item),
        }
        for item in (declared[action_id] for action_id in sorted(declared))
    ]


def action_for_environment(settings: Settings, environment: dict, action_id: str) -> Action | None:
    """Resolve current action metadata from the installed product declaration."""
    allowed = next(
        (item for item in actions_for_environment(environment, settings) if item["id"] == action_id),
        None,
    )
    if allowed is None:
        return None
    if action_id in ACTIONS:
        return ACTIONS[action_id]
    return Action(
        allowed["id"], allowed["title"], allowed["capability"],
        allowed["changes_environment"],
    )


def command_for_task(
    settings: Settings, environment: dict, action: str, target: str, parameters: dict
) -> tuple[list[str], Path]:
    spec = command_for(settings, environment, action, target, parameters)
    command = list(spec.command)
    if "platform_regress.cli" in command:
        output = settings.artifact_dir(environment["product_id"], environment["id"])
        command[command.index("--output-dir") + 1] = str(output)
        state = settings.regression_state_dir(environment["product_id"], environment["id"])
        if "--state-dir" in command:
            command[command.index("--state-dir") + 1] = str(state)
        else:
            command += ["--state-dir", str(state)]
        if "--context-json" not in command:
            command += ["--context-json", "{}"]
        index = command.index("--context-json") + 1
        context = json.loads(command[index])
        context.update(
            state_root=str(state),
            ledger_root=str(settings.resource_dir(environment["product_id"], environment["id"])),
            runtime_root=str(settings.runtime_dir / "products" / environment["product_id"] / environment["id"]),
        )
        command[index] = json.dumps(context, ensure_ascii=False)
    if parameters.get("export_reports") and "platform_regress.cli" in command:
        command += ["--html", "report.html", "--junit", "junit.xml"]
    return command, spec.cwd
