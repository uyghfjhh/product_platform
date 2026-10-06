"""Environment, regression-binding, cases and database-inspection routes."""

from pathlib import Path

from fastapi import Body, Header, HTTPException

from ..actions import actions_for_environment
from ..catalog import get_product
from ..config import Settings
from ..discovery import discover_cases
from ..product_catalog import discover_products
from ..resources import validate_registration
from ..studio import studio_dispatch
from ..topology import configured_topology, observed_status
from .contracts import Action, Case, Environment, RegressionBinding
from .schemas import (
    EnvironmentInput,
    RegressionBindingInput,
)


def register(app, settings: Settings, store) -> None:

    @app.get("/api/v1/environments", response_model=list[Environment])
    def environments():
        return store.environments.list_environments()

    @app.get("/api/v1/regression-bindings", response_model=list[RegressionBinding])
    def regression_bindings():
        return store.bindings.list_regression_bindings()

    @app.put("/api/v1/regression-bindings/{product_id}/{profile_id}")
    def bind_regression(product_id: str, profile_id: str, item: RegressionBindingInput):
        manifest = discover_products(settings.products_root).get(product_id)
        profile = next((value for value in manifest.test_profiles if value.id == profile_id), None) if manifest else None
        environment = store.environments.get_environment(item.environment_id)
        if profile is None or environment is None or environment["product_id"] != product_id:
            raise HTTPException(status_code=422, detail="产品、测试 profile 或环境不匹配")
        if not profile.accepts(environment["deployment_target"], profile.suites[0], profile.id):
            raise HTTPException(status_code=422, detail="环境拓扑不适用于该回归测试")
        return store.bindings.put_regression_binding(product_id, profile_id, item.environment_id)

    @app.delete("/api/v1/regression-bindings/{product_id}/{profile_id}")
    def unbind_regression(product_id: str, profile_id: str):
        if not store.bindings.delete_regression_binding(product_id, profile_id):
            raise HTTPException(status_code=404, detail="回归绑定不存在")
        return {"status": "ok"}

    @app.post("/api/v1/environments", status_code=201)
    def add_environment(item: EnvironmentInput):
        if not get_product(item.product_id, settings):
            raise HTTPException(status_code=422, detail="未知产品")
        return store.environments.put_environment(item.model_dump(), validator=validate_registration)

    @app.get("/api/v1/environments/{environment_id}")
    def environment(environment_id: str):
        value = store.environments.get_environment(environment_id)
        if value is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        return value

    @app.put("/api/v1/environments/{environment_id}")
    def update_environment(environment_id: str, item: EnvironmentInput):
        if item.id != environment_id:
            raise HTTPException(status_code=422, detail="环境 ID 不可修改")
        if not get_product(item.product_id, settings):
            raise HTTPException(status_code=422, detail="未知产品")
        updated = store.environments.update_environment(environment_id, item.model_dump(), validator=validate_registration)
        if updated is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        return updated

    @app.delete("/api/v1/environments/{environment_id}")
    def delete_environment(environment_id: str):
        if not store.environments.delete_environment(environment_id):
            raise HTTPException(status_code=404, detail="环境不存在")
        return {"status": "ok", "deleted": environment_id}

    @app.get("/api/v1/environments/{environment_id}/actions", response_model=list[Action])
    def environment_actions(environment_id: str):
        value = store.environments.get_environment(environment_id)
        if value is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        return actions_for_environment(value, settings)

    @app.get("/api/v1/environments/{environment_id}/topology")
    def environment_topology(environment_id: str):
        value = store.environments.get_environment(environment_id)
        if value is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        try:
            return configured_topology(settings, value)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/environments/{environment_id}/topology/status")
    def environment_topology_status(environment_id: str):
        value = store.environments.get_environment(environment_id)
        if value is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        try:
            return observed_status(settings, value)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/environments/{environment_id}/monitoring")
    def environment_monitoring(environment_id: str, window_minutes: int = 60):
        from ..providers import provider_for
        value = store.environments.get_environment(environment_id)
        if value is None:
            raise HTTPException(404, "环境不存在")
        provider = provider_for(settings, value["product_id"])
        collect = getattr(provider, "monitoring_snapshot", None)
        if collect is None:
            raise HTTPException(422, "产品尚未提供监控接口")
        try:
            return app.state.monitoring.read(environment_id, window_minutes)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/environments/{environment_id}/monitoring/metrics")
    def monitoring_metrics(environment_id: str):
        from fastapi.responses import Response
        if store.environments.get_environment(environment_id) is None:
            raise HTTPException(404, "环境不存在")
        return Response(app.state.monitoring.metrics(environment_id),headers={"Content-Type":"text/plain; version=0.0.4; charset=utf-8","Cache-Control":"no-store"})

    @app.post("/api/v1/environments/{environment_id}/monitoring")
    def enable_environment_monitoring(environment_id: str, item: dict = Body(...)):
        from ..providers import provider_for
        value = store.environments.get_environment(environment_id)
        if value is None:
            raise HTTPException(404, "环境不存在")
        if not callable(getattr(provider_for(settings, value["product_id"]), "monitoring_snapshot", None)):
            raise HTTPException(422, "产品尚未提供监控接口")
        if not isinstance(item.get("enabled"), bool):
            raise HTTPException(422, "enabled 必须是布尔值")
        if item['enabled']:
            try:
                configured_topology(settings,value)
            except (ValueError,FileNotFoundError) as exc:
                raise HTTPException(422,'启用监控前需要有效的部署拓扑') from exc
        app.state.monitoring.enable(environment_id, item["enabled"])
        return {"enabled": item["enabled"]}

    @app.put("/api/v1/environments/{environment_id}/monitoring/rules")
    def monitoring_rules(environment_id: str, item: dict = Body(...)):
        if store.environments.get_environment(environment_id) is None:
            raise HTTPException(404, "环境不存在")
        try:
            return app.state.monitoring.rules(environment_id, item)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/cases", response_model=list[Case])
    def cases(product_id: str):
        if not get_product(product_id, settings):
            raise HTTPException(status_code=404, detail="未知产品")
        try:
            return discover_cases(settings, product_id)
        except (FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    def studio_environment(environment_id: str, node_id: str | None = None):
        environment = store.environments.get_environment(environment_id)
        if environment is None:
            raise HTTPException(404, "环境不存在")
        target = dict(environment)
        if node_id:
            try:
                nodes = configured_topology(settings, environment)["nodes"]
            except (ValueError, FileNotFoundError) as exc:
                raise HTTPException(422, "无法解析目标实例") from exc
            node = next((candidate for candidate in nodes if candidate["id"] == node_id), None)
            if node is None:
                raise HTTPException(404, "目标实例不属于当前环境")
            target.update(host=node["host"], port=node["port"])
        return target

    @app.get("/api/v1/environments/{environment_id}/monitoring/nodes/{node_id}/diagnostics/{view}")
    def monitoring_diagnostics(environment_id: str, node_id: str, view: str):
        from fastapi.encoders import jsonable_encoder
        from ..postgres_monitoring import diagnostics
        if view not in ("queries", "tables"):
            raise HTTPException(422, "未知诊断视图")
        return jsonable_encoder(diagnostics(studio_environment(environment_id,node_id),view))

    @app.post("/api/v1/environments/{environment_id}/studio")
    def studio_bff(environment_id: str, item: dict = Body(...), request_id: str | None = Header(default=None, alias='X-Studio-Request-Id')):
        return studio_dispatch(studio_environment(environment_id), item, **({'request_id':request_id} if isinstance(request_id,str) else {}))

    @app.post("/api/v1/environments/{environment_id}/studio/nodes/{node_id}")
    def studio_node_bff(environment_id: str, node_id: str, item: dict = Body(...), request_id: str | None = Header(default=None, alias='X-Studio-Request-Id')):
        return studio_dispatch(studio_environment(environment_id, node_id), item, **({'request_id':request_id} if isinstance(request_id,str) else {}))

    def cancel_studio(environment_id, request_id, node_id=None):
        from ..studio_runtime import cancel
        try:
            return cancel(studio_environment(environment_id,node_id),request_id)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.post('/api/v1/environments/{environment_id}/studio/cancel/{request_id}')
    def cancel_studio_query(environment_id: str, request_id: str):
        return cancel_studio(environment_id,request_id)

    @app.post('/api/v1/environments/{environment_id}/studio/nodes/{node_id}/cancel/{request_id}')
    def cancel_studio_node_query(environment_id: str, node_id: str, request_id: str):
        return cancel_studio(environment_id,request_id,node_id)

    @app.get("/api/v1/environments/{environment_id}/configuration")
    def environment_configuration(environment_id: str):
        environment = store.environments.get_environment(environment_id)
        if environment is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        config_path = environment["deployment_config"]
        if not config_path:
            raise HTTPException(status_code=404, detail="当前环境未关联部署配置")
        path = Path(config_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="配置文件不存在")
        return {
            "path": str(path),
            "content": path.read_text(encoding="utf-8"),
            "language": "yaml",
        }
