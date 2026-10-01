"""统一 HTTP API；产品命令通过已登记的提供者与本机任务执行。"""

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings, load_settings
from ..deployment.service import DeploymentService
from ..filestore import ConflictError, FileStore
from ..operations import OperationError, OperationService
from ..product_catalog import ProductManifestError, discover_products
from ..product_routes import register_product_routes
from ..providers import registry_for
from . import (
    routes_deployment,
    routes_environments,
    routes_licenses,
    routes_meta,
    routes_operations,
    routes_results,
)


def create_app(settings: Settings | None = None, enqueuer=None) -> FastAPI:
    settings = settings or load_settings()
    registry_for(settings).validate_installed()
    store = FileStore(settings.data_dir, runtime_dir=settings.runtime_dir, logs_dir=settings.logs_dir)
    # Bind existing environments only when a product profile has exactly one
    # compatible deployed topology. Ambiguous choices remain user decisions.
    try:
        installed = discover_products(settings.products_root)
        for manifest in installed.values():
            for profile in manifest.test_profiles:
                if store.bindings.get_regression_binding(manifest.id, profile.id):
                    continue
                candidates = [environment for environment in store.environments.list_environments()
                              if environment["product_id"] == manifest.id
                              and profile.accepts(environment["deployment_target"],
                                                  profile.suites[0], profile.id)]
                if len(candidates) == 1:
                    try:
                        store.bindings.put_regression_binding(manifest.id, profile.id, candidates[0]["id"])
                    except ConflictError:
                        pass
    except ProductManifestError:
        pass
    if enqueuer is None:
        from ..queue import create_queue

        _queue, enqueuer = create_queue(settings, store)

    operations = OperationService(settings, store, enqueuer)
    deployments = DeploymentService(settings, store, operations)

    @asynccontextmanager
    async def lifespan(_app):
        await asyncio.to_thread(deployments.recover)
        async def dispatch_loop():
            while True:
                await asyncio.to_thread(operations.retry_dispatch)
                await asyncio.sleep(2)
        dispatcher = asyncio.tasks.create_task(dispatch_loop())
        try:
            yield
        finally:
            dispatcher.cancel()
            with suppress(asyncio.CancelledError):
                await dispatcher

    app = FastAPI(title="公司产品公共管理平台", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.operations = operations
    app.state.deployments = deployments

    @app.exception_handler(OperationError)
    async def operation_error(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict_handler(_request, exc: ConflictError):
        return JSONResponse(
            status_code=409, content={"code": "CONFLICT", "message": str(exc)}
        )

    routes_meta.register(app, settings)
    routes_licenses.register(app, settings)
    routes_environments.register(app, settings, store)
    routes_operations.register(app, settings, store)
    routes_results.register(app, settings, store)
    routes_deployment.register(app, settings, store)
    register_product_routes(app, settings, store)

    if settings.frontend_dist.is_dir():
        # Hashed assets (/assets/index-<hash>.js) are immutable; index.html must
        # never be heuristically cached or browsers pin a stale bundle.
        class FrontendFiles(StaticFiles):
            async def get_response(self, path, scope):
                response = await super().get_response(path, scope)
                if path.startswith("assets/"):
                    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                else:
                    response.headers["Cache-Control"] = "no-cache"
                return response

        app.mount(
            "/",
            FrontendFiles(directory=settings.frontend_dist, html=True),
            name="frontend",
        )

    return app
