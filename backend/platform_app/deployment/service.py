"""Durable, recoverable deployment applications. Only reviewed plans are replayed."""

from ..operations import OperationError, OperationRequest
from .workbench import Workbench


class DeploymentService:
    def __init__(self, settings, store, operations):
        self.store, self.operations = store, operations
        self.workbench = Workbench(settings, store)

    def apply(self, plan_id, acknowledge_change=False):
        plan = self.workbench.verify(plan_id)
        if not plan["action"]:
            raise OperationError(422, "方案没有可执行操作")
        if plan["action"] in {"deployment.create", "deployment.change"} and not acknowledge_change:
            raise OperationError(422, "请确认此方案将初始化并部署新实例")
        request = self.store.deployments.begin_deployment_request(
            plan_id, plan["environment_id"], acknowledge_change)
        return self.resume(request)

    def resume(self, request):
        task = (self.store.tasks.get_task(request["task_id"]) if request.get("task_id") else
                self.store.tasks.task_for_submission("deployment-request:" + request["id"]))
        if task:
            self.store.deployments.update_deployment_request(request["id"], state="SUBMITTED", task_id=task["id"])
            if task.get("dispatch_pending") and task["status"] == "QUEUED":
                try:
                    self.operations.dispatch(task["id"])
                except Exception as exc:
                    raise OperationError(503, "部署任务已持久化，投递等待重试") from exc
            return self.store.tasks.get_task(task["id"])
        plan = self.workbench.verify(request["plan_id"])
        self.workbench.associate(request["plan_id"], request["id"])
        self.store.deployments.update_deployment_request(request["id"], state="ASSOCIATED")
        command = OperationRequest(
            environment_id=request["environment_id"], action=plan["action"],
            target=plan["target"], acknowledge_change=request["acknowledged"],
            deployment_plan_id=plan["id"],
            submission_key="deployment-request:" + request["id"],
        )
        try:
            task = self.operations.submit(command)
        except OperationError as exc:
            # A dispatch failure still has a durable task/outbox. The same
            # submission key connects it to the request during recovery.
            self.store.deployments.update_deployment_request(request["id"], error=str(exc))
            raise
        self.store.deployments.update_deployment_request(request["id"], state="SUBMITTED", task_id=task["id"], error=None)
        return task

    def recover(self):
        for request in self.store.deployments.deployment_requests():
            if request["state"] not in {"PENDING", "ASSOCIATED"}:
                continue
            try:
                self.resume(request)
            except OperationError as exc:
                state = self.store.deployments.deployment_request(request["id"])["state"] if exc.status_code == 503 else "REJECTED"
                self.store.deployments.update_deployment_request(request["id"], state=state, error=str(exc))
            except (ValueError, KeyError, RuntimeError) as exc:
                self.store.deployments.update_deployment_request(request["id"], state="REJECTED", error=str(exc))
