"""Mount optional HTTP routers supplied by installed product packages."""

import importlib.util

from fastapi import APIRouter, FastAPI

from .config import Settings
from .product_catalog import discover_products
from .storage import Store


def register_product_routes(app: FastAPI, settings: Settings, store: Store) -> None:
    for product in discover_products(settings.products_root).values():
        path = product.package_root / "router.py"
        if not path.is_file():
            continue
        name = "_platform_router_" + product.id.replace("-", "_")
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"无法加载产品路由: {product.id}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        factory = getattr(module, "create_router", None)
        if not callable(factory):
            raise ValueError(f"产品路由缺少 create_router: {product.id}")
        router = factory(settings, store)
        if not isinstance(router, APIRouter):
            raise ValueError(f"产品路由类型无效: {product.id}")
        app.include_router(router)
