"""统一 HTTP API；产品命令通过已登记的提供者与本机任务执行。"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings, load_settings
from ..filestore import ConflictError, FileStore
from ..product_catalog import ProductManifestError, discover_products
from ..product_routes import register_product_routes
from . import routes_environments, routes_licenses, routes_meta, routes_operations, routes_results
from .schemas import (
    EnvironmentInput,
    LicenseKeyCreateInput,
    LicenseKeyDeleteInput,
    LicenseKeyPasswordInput,
    OperationInput,
    QueryInput,
    RegressionBindingInput,
    public_task,
)

__all__ = [
    "create_app", "app", "public_task",
    "EnvironmentInput", "OperationInput", "QueryInput",
    "RegressionBindingInput", "LicenseKeyCreateInput",
    "LicenseKeyDeleteInput", "LicenseKeyPasswordInput",
]


def create_app(settings: Settings | None = None, enqueuer=None) -> FastAPI:
    settings = settings or load_settings()
    store = FileStore(settings.data_dir)
    # Bind existing environments only when a product profile has exactly one
    # compatible deployed topology. Ambiguous choices remain user decisions.
    try:
        installed = discover_products(settings.products_root)
        for manifest in installed.values():
            for profile in manifest.test_profiles:
                if store.get_regression_binding(manifest.id, profile.id):
                    continue
                candidates = [environment for environment in store.list_environments()
                              if environment["product_id"] == manifest.id
                              and profile.accepts(environment["deployment_target"],
                                                  profile.suites[0], profile.id)]
                if len(candidates) == 1:
                    try:
                        store.put_regression_binding(manifest.id, profile.id, candidates[0]["id"])
                    except ConflictError:
                        pass
    except ProductManifestError:
        pass
    if enqueuer is None:
        from ..queue import execute

        enqueuer = execute

    app = FastAPI(title="公司产品公共管理平台", version="0.1.0")
    app.state.settings = settings
    app.state.store = store

    @app.exception_handler(ConflictError)
    async def conflict_handler(_request, exc: ConflictError):
        return JSONResponse(
            status_code=409, content={"code": "CONFLICT", "message": str(exc)}
        )

    routes_meta.register(app, settings)
    routes_licenses.register(app, settings)
    routes_environments.register(app, settings, store)
    routes_operations.register(app, settings, store, enqueuer)
    routes_results.register(app, settings, store)
    register_product_routes(app, settings, store)

    if settings.frontend_dist.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=settings.frontend_dist, html=True),
            name="frontend",
        )

    return app


app = create_app()
