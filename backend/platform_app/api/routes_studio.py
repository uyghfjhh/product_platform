"""Database Studio BFF and per-node live SQL diagnostics routes."""

from fastapi import Body, Header, HTTPException
from fastapi.encoders import jsonable_encoder

from ..config import Settings
from ..postgres_monitoring import diagnostics
from ..studio_runtime import cancel


def register(app, settings: Settings, store) -> None:
    from . import routes_environments

    def studio_environment(environment_id: str, node_id: str | None = None):
        environment = store.environments.get_environment(environment_id)
        if environment is None:
            raise HTTPException(404, "环境不存在")
        target = dict(environment)
        if node_id:
            try:
                nodes = routes_environments.configured_topology(settings, environment)["nodes"]
            except (ValueError, FileNotFoundError) as exc:
                raise HTTPException(422, "无法解析目标实例") from exc
            node = next((candidate for candidate in nodes if candidate["id"] == node_id), None)
            if node is None:
                raise HTTPException(404, "目标实例不属于当前环境")
            target.update(host=node["host"], port=node["port"])
        return target

    @app.get("/api/v1/environments/{environment_id}/monitoring/nodes/{node_id}/diagnostics/{view}")
    def monitoring_diagnostics(environment_id: str, node_id: str, view: str):
        if view not in ("queries", "tables"):
            raise HTTPException(422, "未知诊断视图")
        return jsonable_encoder(diagnostics(studio_environment(environment_id, node_id), view))

    @app.post("/api/v1/environments/{environment_id}/studio")
    def studio_bff(
        environment_id: str,
        item: dict = Body(...),
        request_id: str | None = Header(default=None, alias="X-Studio-Request-Id"),
    ):
        return routes_environments.studio_dispatch(
            studio_environment(environment_id),
            item,
            **({"request_id": request_id} if isinstance(request_id, str) else {}),
        )

    @app.post("/api/v1/environments/{environment_id}/studio/nodes/{node_id}")
    def studio_node_bff(
        environment_id: str,
        node_id: str,
        item: dict = Body(...),
        request_id: str | None = Header(default=None, alias="X-Studio-Request-Id"),
    ):
        return routes_environments.studio_dispatch(
            studio_environment(environment_id, node_id),
            item,
            **({"request_id": request_id} if isinstance(request_id, str) else {}),
        )

    def cancel_studio(environment_id, request_id, node_id=None):
        try:
            return cancel(studio_environment(environment_id, node_id), request_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/v1/environments/{environment_id}/studio/cancel/{request_id}")
    def cancel_studio_query(environment_id: str, request_id: str):
        return cancel_studio(environment_id, request_id)

    @app.post("/api/v1/environments/{environment_id}/studio/nodes/{node_id}/cancel/{request_id}")
    def cancel_studio_node_query(environment_id: str, node_id: str, request_id: str):
        return cancel_studio(environment_id, request_id, node_id)
