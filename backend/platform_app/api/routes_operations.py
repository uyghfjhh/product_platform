"""Operation (task) routes: submit, list, events, cancel, log."""

import asyncio
import json

from fastapi import HTTPException, Query
from fastapi.responses import StreamingResponse

from ..actions import TERMINAL, action_for_environment, actions_for_environment
from ..config import Settings
from ..discovery import validate_target
from ..product_catalog import (
    ProductManifestError,
    discover_products,
    validate_parameters,
)
from ..topology import configured_topology
from .schemas import OperationInput, TARGET, public_task


def register(app, settings: Settings, store, enqueuer) -> None:

    @app.post("/api/v1/operations", status_code=202)
    def start_operation(item: OperationInput):
        environment = store.get_environment(item.environment_id)
        if environment is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        if item.action not in {
            action["id"] for action in actions_for_environment(environment, settings)
        }:
            raise HTTPException(status_code=422, detail="当前环境不支持该操作")
        action = action_for_environment(settings, environment, item.action)
        if action is None:
            raise HTTPException(status_code=422, detail="当前环境不支持该操作")
        try:
            manifest = discover_products(settings.products_root).get(environment["product_id"])
            declared = next(
                (value for value in (manifest.actions if manifest else ()) if value.id == item.action),
                None,
            )
            if declared is not None:
                validate_parameters(declared, item.parameters)
        except ProductManifestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if action.changes_environment and not item.acknowledge_change:
            raise HTTPException(status_code=422, detail="请确认本次操作会修改环境")
        target = item.target or (
            environment["deployment_target"]
            if item.action.startswith("deployment.")
            else environment["id"]
        )
        if not target or not TARGET.fullmatch(target):
            raise HTTPException(status_code=422, detail="目标名称无效")
        if item.action.startswith("deployment."):
            try:
                topology = configured_topology(settings, environment)
            except (ValueError, FileNotFoundError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            nodes = {node["id"] for node in topology["nodes"]}
            allowed = {environment["deployment_target"]} | nodes
            if item.action in {"deployment.failover", "deployment.switchover",
                               "deployment.rejoin", "deployment.lag"}:
                allowed = {f"streaming.{node['group']}" for node in topology["nodes"] if node.get("group")}
            if item.action == "deployment.verify":
                allowed = {environment["deployment_target"]} | {
                    f"streaming.{node['group']}" for node in topology["nodes"] if node.get("group")}
            if target not in allowed:
                raise HTTPException(status_code=422, detail="操作目标不属于当前环境拓扑")
            if item.action in {"deployment.validate", "deployment.create", "deployment.health", "deployment.clean"} and target != environment["deployment_target"]:
                raise HTTPException(status_code=422, detail="该操作只能作用于当前集群")
        if declared is not None and declared.validate_target:
            if not validate_target(settings, environment["product_id"], target):
                raise HTTPException(status_code=422, detail="未知产品测试目标")
        if declared is not None and declared.capability == "tests" and manifest.test_profiles:
            requested_profile = item.parameters.get("cluster")
            matches = [profile for profile in manifest.test_profiles
                       if profile.accepts(environment["deployment_target"], target,
                                          requested_profile)]
            if not any((binding := store.get_regression_binding(manifest.id, profile.id))
                       and binding["environment_id"] == environment["id"] for profile in matches):
                raise HTTPException(status_code=422, detail="测试目标未绑定当前产品环境或测试 profile")
        if item.action == "diagnostics.analyze" and store.get_result(
            environment["product_id"], environment["id"], target,
            item.parameters.get("profile", "default"),
        ) is None:
            raise HTTPException(status_code=404, detail="当前环境没有该测试目标的结果")
        task, created = store.create_task_once(
            item.environment_id,
            item.action,
            target,
            item.parameters,
            item.submission_key,
        )
        if created:
            try:
                enqueuer(task["id"])
            except Exception as exc:
                store.finish_task(task["id"], ("QUEUED",), "FAILED", "任务入队失败")
                raise HTTPException(status_code=503, detail="任务入队失败") from exc
        return public_task(task)

    @app.get("/api/v1/operations")
    def operations(limit: int = Query(default=40, ge=1, le=200)):
        return [public_task(task) for task in store.list_tasks(limit)]

    @app.get("/api/v1/operations/{task_id}")
    def operation(task_id: str):
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return public_task(task)

    @app.get("/api/v1/operations/{task_id}/events")
    def operation_events(task_id: str, after: int = Query(default=0, ge=0)):
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return store.list_events(task_id, after)

    @app.get("/api/v1/operations/{task_id}/events/stream")
    async def event_stream(task_id: str, after: int = Query(default=0, ge=0)):
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="任务不存在")

        async def stream():
            cursor = after
            while True:
                # Read terminal state before its event snapshot. If finish
                # races this iteration, the next iteration drains its event.
                task = store.get_task(task_id)
                if task is None:
                    return
                events = store.list_events(task_id, cursor)
                for event in events:
                    cursor = event["sequence"]
                    yield "id: %s\nevent: update\ndata: %s\n\n" % (
                        cursor,
                        json.dumps(event, ensure_ascii=False),
                    )
                if task["status"] in TERMINAL:
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/v1/operations/{task_id}/cancel")
    def cancel_operation(task_id: str):
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if not store.request_cancel(task_id):
            raise HTTPException(status_code=409, detail="任务已经结束或正在取消")
        return public_task(store.get_task(task_id))

    @app.get("/api/v1/operations/{task_id}/log")
    def operation_log(
        task_id: str, last_lines: int = Query(default=500, ge=1, le=5000)
    ):
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        path = settings.platform_dir / "operations" / (task_id + ".log")
        if not path.is_file():
            return {"lines": [], "path": str(path), "available": False}
        # 日志按后缀行数读取，前端保留原文与等级高亮的独立表示。
        from collections import deque

        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = list(deque(handle, maxlen=last_lines))
        return {
            "lines": [line.rstrip("\n") for line in lines],
            "path": str(path),
            "available": True,
        }
