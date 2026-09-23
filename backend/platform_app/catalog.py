"""产品目录只声明业务能力，执行器由各模块实现。"""

from .config import Settings


PRODUCTS = (
    {
        "id": "fbase-database",
        "title": "FBase 数据库",
        "description": "数据库内核、多活与企业版能力",
        "capabilities": ["deployment", "database", "tests", "knowledge", "license"],
    },
    {
        "id": "fbasecman",
        "title": "fbasecman",
        "description": "数据库代理、路由与高可用",
        "capabilities": [
            "deployment",
            "database",
            "tests",
            "stability",
            "knowledge",
            "license",
        ],
    },
)


def list_products(settings: Settings) -> list[dict]:
    by_id = {item["id"]: dict(item) for item in PRODUCTS}
    by_id["fbase-database"]["source_path"] = str(settings.fbase_regress_root.parent)
    by_id["fbasecman"]["source_path"] = str(settings.fbasecman_regress_root.parent)
    return list(by_id.values())


def get_product(product_id: str, settings: Settings) -> dict | None:
    return next(
        (item for item in list_products(settings) if item["id"] == product_id), None
    )
