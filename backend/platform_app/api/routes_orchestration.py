"""Automation and notification API; all execution uses OperationService."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from pydantic import BaseModel

from ..actions import actions_for_environment
from ..notifications import WebhookInput
from ..orchestration import PipelineInput, ScheduleInput
from ..product_catalog import discover_products, validate_parameters


class ExecuteInput(BaseModel):
    acknowledge_change: bool = False


def register(app, settings, store):
    repository = store.orchestration

    @app.get("/api/v1/notification-webhooks")
    def webhooks():
        return repository.list("webhooks")

    def save_webhook(item, identity=None):
        if item.enabled and not item.acknowledge_send:
            raise HTTPException(422, "启用外部通知需要确认发送目标")
        if item.url.username or item.url.password:
            raise HTTPException(422, "URL 不能包含用户名或口令，使用凭据环境变量引用")
        return repository.put(
            "webhooks",
            item.model_dump(mode="json", exclude={"acknowledge_send"}),
            identity,
        )

    @app.post("/api/v1/notification-webhooks", status_code=201)
    def create_webhook(item: WebhookInput):
        return save_webhook(item)

    @app.put("/api/v1/notification-webhooks/{identity}")
    def update_webhook(identity: str, item: WebhookInput):
        if repository.get("webhooks", identity) is None:
            raise HTTPException(404, "通知目标不存在")
        return save_webhook(item, identity)

    @app.delete("/api/v1/notification-webhooks/{identity}")
    def delete_webhook(identity: str):
        if not repository.delete("webhooks", identity):
            raise HTTPException(404, "通知目标不存在")
        return {"deleted": True}

    @app.get("/api/v1/pipelines")
    def pipelines():
        return [row for row in repository.list('pipelines') if not row.get('workbench')]

    def save_pipeline(item, identity=None):
        environment = store.environments.get_environment(item.environment_id)
        if environment is None:
            raise HTTPException(404, "环境不存在")
        actions = {row["id"] for row in actions_for_environment(environment, settings)}
        if any(step.action not in actions for step in item.steps):
            raise HTTPException(422, "流水线包含环境不支持的动作")
        manifest=discover_products(settings.products_root)[environment['product_id']]
        declared={action.id:action for action in manifest.actions}
        try:
            for step in item.steps:
                if step.action in declared:validate_parameters(declared[step.action],step.parameters)
                if step.action.startswith('workload.'):
                    from ..workloads import WorkloadInput
                    WorkloadInput.model_validate(step.parameters)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        return repository.put("pipelines", item.model_dump(), identity)

    @app.post("/api/v1/pipelines", status_code=201)
    def create_pipeline(item: PipelineInput):
        return save_pipeline(item)

    @app.put("/api/v1/pipelines/{identity}")
    def update_pipeline(identity: str, item: PipelineInput):
        row = repository.get('pipelines', identity)
        if row is None:
            raise HTTPException(404, '流水线不存在')
        if row.get('workbench'):
            raise HTTPException(409, '已审阅负载计划不可编辑，请重新生成计划')
        return save_pipeline(item, identity)

    @app.delete("/api/v1/pipelines/{identity}")
    def delete_pipeline(identity: str):
        if any(
            row["pipeline_id"] == identity
            and row["status"] in {"RUNNING", "CANCELLING"}
            for row in repository.list("runs")
        ):
            raise HTTPException(409, "流水线正在执行")
        if any(
            row["pipeline_id"] == identity and row["enabled"]
            for row in repository.list("schedules")
        ):
            raise HTTPException(409, "先停用流水线定时任务")
        if not repository.delete("pipelines", identity):
            raise HTTPException(404, "流水线不存在")
        return {"deleted": True}

    @app.post("/api/v1/pipelines/{identity}/run", status_code=202)
    def run_pipeline(identity: str, item: ExecuteInput):
        row = repository.get('pipelines', identity)
        if row and row.get('workbench'):
            from ..workloads.workbench import Workbench
            try:
                return Workbench(settings, store, app.state.orchestrator).start(identity, item.acknowledge_change)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        return app.state.orchestrator.start(identity, item.acknowledge_change)

    @app.get("/api/v1/pipeline-runs")
    def pipeline_runs():
        return repository.list("runs")

    @app.post("/api/v1/pipeline-runs/{identity}/cancel")
    def cancel_pipeline(identity: str):
        return app.state.orchestrator.cancel(identity)

    @app.get("/api/v1/schedules")
    def schedules():
        return repository.list("schedules")

    def save_schedule(item, identity=None):
        pipeline = repository.get('pipelines', item.pipeline_id)
        if pipeline is None:
            raise HTTPException(404, '流水线不存在')
        if pipeline.get('workbench'):
            raise HTTPException(422, '负载审阅计划不能定时重复执行；保存方案能力尚未接入')
        if item.enabled and not item.acknowledge_change:
            raise HTTPException(422, "启用定时执行需要确认全部流水线步骤")
        value = item.model_dump(exclude={"acknowledge_change"})
        value["next_run_at"] = (
            datetime.now(UTC) + timedelta(seconds=item.interval_seconds)
        ).isoformat()
        return repository.put("schedules", value, identity)

    @app.post("/api/v1/schedules", status_code=201)
    def create_schedule(item: ScheduleInput):
        return save_schedule(item)

    @app.put("/api/v1/schedules/{identity}")
    def update_schedule(identity: str, item: ScheduleInput):
        if repository.get("schedules", identity) is None:
            raise HTTPException(404, "定时任务不存在")
        return save_schedule(item, identity)

    @app.delete("/api/v1/schedules/{identity}")
    def delete_schedule(identity: str):
        if not repository.delete("schedules", identity):
            raise HTTPException(404, "定时任务不存在")
        return {"deleted": True}

    @app.get("/api/v1/notifications")
    def notifications():
        return repository.list("notifications")[:200]

    @app.put("/api/v1/notifications/{identity}/read")
    def read_notification(identity: str):
        row = repository.get("notifications", identity)
        if row is None:
            raise HTTPException(404, "通知不存在")
        row["read"] = True
        return repository.put("notifications", row, identity)
