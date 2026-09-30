"""Environment, regression-binding, cases and database-inspection routes."""

from pathlib import Path

from fastapi import Body, HTTPException

from ..actions import actions_for_environment
from ..catalog import get_product
from ..config import Settings
from ..database import (
    cancel_backend, execute_query, list_columns, list_locks, list_objects,
    list_replication, list_sessions, list_settings,
)
from ..discovery import discover_cases
from ..product_catalog import discover_products
from ..studio import studio_dispatch
from ..topology import configured_topology, observed_status
from .schemas import (
    CancelBackendInput, EnvironmentInput, QueryInput, RegressionBindingInput,
)


def register(app, settings: Settings, store) -> None:

    @app.get("/api/v1/environments")
    def environments():
        return store.list_environments()

    @app.get("/api/v1/regression-bindings")
    def regression_bindings():
        return store.list_regression_bindings()

    @app.put("/api/v1/regression-bindings/{product_id}/{profile_id}")
    def bind_regression(product_id: str, profile_id: str, item: RegressionBindingInput):
        manifest = discover_products(settings.products_root).get(product_id)
        profile = next((value for value in manifest.test_profiles if value.id == profile_id), None) if manifest else None
        environment = store.get_environment(item.environment_id)
        if profile is None or environment is None or environment["product_id"] != product_id:
            raise HTTPException(status_code=422, detail="产品、测试 profile 或环境不匹配")
        if not profile.accepts(environment["deployment_target"], profile.suites[0], profile.id):
            raise HTTPException(status_code=422, detail="环境拓扑不适用于该回归测试")
        return store.put_regression_binding(product_id, profile_id, item.environment_id)

    @app.delete("/api/v1/regression-bindings/{product_id}/{profile_id}")
    def unbind_regression(product_id: str, profile_id: str):
        if not store.delete_regression_binding(product_id, profile_id):
            raise HTTPException(status_code=404, detail="回归绑定不存在")
        return {"status": "ok"}

    @app.post("/api/v1/environments", status_code=201)
    def add_environment(item: EnvironmentInput):
        if not get_product(item.product_id, settings):
            raise HTTPException(status_code=422, detail="未知产品")
        return store.put_environment(item.model_dump())

    @app.get("/api/v1/environments/{environment_id}")
    def environment(environment_id: str):
        value = store.get_environment(environment_id)
        if value is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        return value

    @app.put("/api/v1/environments/{environment_id}")
    def update_environment(environment_id: str, item: EnvironmentInput):
        if item.id != environment_id:
            raise HTTPException(status_code=422, detail="环境 ID 不可修改")
        if not get_product(item.product_id, settings):
            raise HTTPException(status_code=422, detail="未知产品")
        updated = store.update_environment(environment_id, item.model_dump())
        if updated is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        return updated

    @app.delete("/api/v1/environments/{environment_id}")
    def delete_environment(environment_id: str):
        if not store.delete_environment(environment_id):
            raise HTTPException(status_code=404, detail="环境不存在")
        return {"status": "ok", "deleted": environment_id}

    @app.get("/api/v1/environments/{environment_id}/actions")
    def environment_actions(environment_id: str):
        value = store.get_environment(environment_id)
        if value is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        return actions_for_environment(value, settings)

    @app.get("/api/v1/environments/{environment_id}/topology")
    def environment_topology(environment_id: str):
        value = store.get_environment(environment_id)
        if value is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        try:
            return configured_topology(settings, value)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/environments/{environment_id}/topology/status")
    def environment_topology_status(environment_id: str):
        value = store.get_environment(environment_id)
        if value is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        try:
            return observed_status(settings, value)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/cases")
    def cases(product_id: str):
        if not get_product(product_id, settings):
            raise HTTPException(status_code=404, detail="未知产品")
        try:
            return discover_cases(settings, product_id)
        except (FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/v1/environments/{environment_id}/query")
    def query_database(environment_id: str, item: QueryInput):
        environment = store.get_environment(environment_id)
        if environment is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        target_env = dict(environment)
        if item.port:
            target_env["port"] = item.port
        try:
            return execute_query(target_env, item.sql, item.max_rows)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/environments/{environment_id}/objects")
    def database_objects(environment_id: str):
        environment = store.get_environment(environment_id)
        if environment is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        try:
            return list_objects(environment)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/environments/{environment_id}/objects/{schema}/{table}/columns")
    def database_columns(environment_id: str, schema: str, table: str):
        environment = store.get_environment(environment_id)
        if environment is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        try:
            return list_columns(environment, schema, table)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _admin_env(environment_id: str, port: int | None):
        environment = store.get_environment(environment_id)
        if environment is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        target = dict(environment)
        if port:
            target["port"] = port
        return target

    @app.get("/api/v1/environments/{environment_id}/sessions")
    def database_sessions(environment_id: str, port: int | None = None):
        target = _admin_env(environment_id, port)
        try:
            return list_sessions(target)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/environments/{environment_id}/locks")
    def database_locks(environment_id: str, port: int | None = None):
        target = _admin_env(environment_id, port)
        try:
            return list_locks(target)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/environments/{environment_id}/replication")
    def database_replication(environment_id: str, port: int | None = None):
        target = _admin_env(environment_id, port)
        try:
            return list_replication(target)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/environments/{environment_id}/settings")
    def database_settings(environment_id: str, port: int | None = None, search: str = ""):
        target = _admin_env(environment_id, port)
        try:
            return list_settings(target, search[:80])
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/environments/{environment_id}/sessions/{pid}/cancel")
    def database_cancel_session(environment_id: str, pid: int, item: CancelBackendInput):
        if pid < 1:
            raise HTTPException(status_code=422, detail="无效的后端 PID")
        target = _admin_env(environment_id, item.port)
        try:
            done = cancel_backend(target, pid, item.terminate)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not done:
            raise HTTPException(status_code=404, detail="后端不存在或已结束")
        return {"status": "ok", "pid": pid, "terminated": item.terminate}

    @app.post("/api/v1/environments/{environment_id}/studio")
    def studio_bff(environment_id: str, port: int | None = None, item: dict = Body(...)):
        """Prisma Studio BFF 协议端点——错误经 Either 元组返回，不走 HTTP 状态。"""
        return studio_dispatch(_admin_env(environment_id, port), item)

    @app.get("/api/v1/environments/{environment_id}/configuration")
    def environment_configuration(environment_id: str):
        environment = store.get_environment(environment_id)
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
