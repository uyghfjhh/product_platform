"""Application service shared by HTTP, CLI and deployment workflows."""

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from .actions import action_for_environment, actions_for_environment
from .discovery import validate_target
from .product_catalog import (
    ProductManifestError,
    discover_products,
    validate_parameters,
)
from .topology import configured_topology

TARGET = re.compile(r"^[A-Za-z0-9_.,:-]{1,160}$")


class OperationError(ValueError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class OperationRequest(BaseModel):
    environment_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    action: str
    target: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    submission_key: str | None = Field(default=None, min_length=1, max_length=100)
    acknowledge_change: bool = False
    deployment_plan_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


class OperationService:
    def __init__(self, settings, store, enqueuer):
        self.settings, self.store, self.enqueuer = settings, store, enqueuer

    def submit(self, item: OperationRequest):
        settings, store = self.settings, self.store
        if any(key.startswith("_deployment_") for key in item.parameters):
            raise OperationError(422, "不能直接设置部署任务内部参数")
        snapshot = None
        if item.deployment_plan_id:
            from .deployment.workbench import Workbench
            try:
                plan = Workbench(settings, store).verify(item.deployment_plan_id)
            except (ValueError, KeyError) as exc:
                raise OperationError(422, str(exc)) from exc
            if (plan["environment_id"] != item.environment_id or plan["action"] != item.action
                    or item.target != plan["target"]):
                raise OperationError(422, "操作与部署计划不匹配")
            snapshot = {"plan_id": plan["id"], "sha256": plan["config_sha256"]}
        environment = store.environments.get_environment(item.environment_id)
        if environment is None:
            raise OperationError(404, message="环境不存在")
        for request in store.deployments.deployment_requests():
            if (request["environment_id"] == item.environment_id
                    and request["state"] in {"PENDING", "ASSOCIATED"}
                    and item.submission_key != "deployment-request:" + request["id"]):
                raise OperationError(409, "环境有待执行部署申请")
        if item.action not in {
            action["id"] for action in actions_for_environment(environment, settings)
        }:
            raise OperationError(422, message="当前环境不支持该操作")
        action = action_for_environment(settings, environment, item.action)
        if action is None:
            raise OperationError(422, message="当前环境不支持该操作")
        try:
            manifest = discover_products(settings.products_root).get(environment["product_id"])
            declared = next(
                (value for value in (manifest.actions if manifest else ()) if value.id == item.action),
                None,
            )
            if declared is not None:
                validate_parameters(declared, item.parameters)
        except ProductManifestError as exc:
            raise OperationError(422, message=str(exc)) from exc
        if action.changes_environment and not item.acknowledge_change:
            raise OperationError(422, message="请确认本次操作会修改环境")
        target = item.target or (
            environment["deployment_target"]
            if item.action.startswith("deployment.")
            else environment["id"]
        )
        if not target or not TARGET.fullmatch(target):
            raise OperationError(422, message="目标名称无效")
        if item.action.startswith("deployment."):
            try:
                topology = configured_topology(settings, environment)
            except (ValueError, FileNotFoundError) as exc:
                raise OperationError(422, message=str(exc)) from exc
            nodes = {node["id"] for node in topology["nodes"]}
            allowed = {environment["deployment_target"]} | nodes
            if item.action in {"deployment.failover", "deployment.switchover",
                               "deployment.rejoin", "deployment.lag"}:
                allowed = {f"streaming.{node['group']}" for node in topology["nodes"] if node.get("group")}
            if item.action in {"deployment.verify", "deployment.restore"}:
                allowed = {environment["deployment_target"]} | {
                    f"streaming.{node['group']}" for node in topology["nodes"] if node.get("group")}
            if target not in allowed:
                raise OperationError(422, message="操作目标不属于当前环境拓扑")
            if item.action in {"deployment.validate", "deployment.create", "deployment.health", "deployment.clean"} and target != environment["deployment_target"]:
                raise OperationError(422, message="该操作只能作用于当前集群")
        if declared is not None and declared.validate_target:
            if not validate_target(settings, environment["product_id"], target):
                raise OperationError(422, message="未知产品测试目标")
        if declared is not None and declared.capability == "tests" and manifest.test_profiles:
            requested_profile = item.parameters.get("cluster")
            matches = [profile for profile in manifest.test_profiles
                       if profile.accepts(environment["deployment_target"], target,
                                          requested_profile)]
            if not any((binding := store.bindings.get_regression_binding(manifest.id, profile.id))
                       and binding["environment_id"] == environment["id"] for profile in matches):
                raise OperationError(422, message="测试目标未绑定当前产品环境或测试 profile")
        if item.action == "diagnostics.analyze" and store.results.get_result(
            environment["product_id"], environment["id"], target,
            item.parameters.get("profile", "default"),
        ) is None:
            raise OperationError(404, message="当前环境没有该测试目标的结果")
        parameters = dict(item.parameters)
        if snapshot:
            parameters["_deployment_snapshot"] = snapshot
        task, created = store.tasks.create_task_once(
            item.environment_id,
            item.action,
            target,
            parameters,
            item.submission_key,
        )
        if created or task.get("dispatch_pending"):
            try:
                self.dispatch(task["id"])
            except Exception as exc:
                raise OperationError(503, "任务已持久化，投递失败；后台将重试") from exc
        return store.tasks.get_task(task["id"])

    def dispatch(self, task_id):
        task = self.store.tasks.get_task(task_id)
        if not task or task["status"] != "QUEUED":
            return
        try:
            self.enqueuer(task_id)
        except Exception as exc:
            self.store.tasks.record_dispatch(task_id, pending=True, error=str(exc))
            raise
        self.store.tasks.record_dispatch(task_id, pending=False)

    def retry_dispatch(self):
        for task in self.store.tasks.unfinished_tasks():
            sent_at = task.get("last_dispatched_at")
            try:
                expired = not sent_at or (datetime.now(UTC) - datetime.fromisoformat(sent_at)).total_seconds() >= 30
            except (TypeError, ValueError):
                expired = True
            if task["status"] == "QUEUED" and (task.get("dispatch_pending") or expired):
                try:
                    self.dispatch(task["id"])
                except Exception:
                    continue
