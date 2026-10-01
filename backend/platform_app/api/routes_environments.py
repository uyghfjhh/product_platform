"""Environment, regression-binding, cases and database-inspection routes."""

from pathlib import Path

from fastapi import Body, HTTPException

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

    @app.post("/api/v1/environments/{environment_id}/studio")
    def studio_bff(environment_id: str, item: dict = Body(...)):
        return studio_dispatch(studio_environment(environment_id), item)

    @app.post("/api/v1/environments/{environment_id}/studio/nodes/{node_id}")
    def studio_node_bff(environment_id: str, node_id: str, item: dict = Body(...)):
        return studio_dispatch(studio_environment(environment_id, node_id), item)

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
