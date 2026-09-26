"""产品目录只声明业务能力，执行器由各模块实现。"""

from .config import Settings
from .product_registry import PRODUCTS


def list_products(settings: Settings) -> list[dict]:
    return [{
        "id": item.id, "title": item.title, "description": item.description,
        "capabilities": list(item.capabilities), "source_path": str(item.source_root()),
    } for item in PRODUCTS.values()]


def get_product(product_id: str, settings: Settings) -> dict | None:
    return next(
        (item for item in list_products(settings) if item["id"] == product_id), None
    )
