"""Platform metadata routes: health, products, integrations, diagnostics flag."""

import os

from ..catalog import list_products
from ..config import Settings
from ..product_catalog import discover_products
from .contracts import Product


def register(app, settings: Settings) -> None:

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok", "storage": "files",
            "runtime_dir": str(settings.runtime_dir),
            "logs_dir": str(settings.logs_dir),
            "environment_records_dir": str(settings.environment_records_dir),
            "profiles_dir": str(settings.profiles_dir),
        }

    @app.get("/api/v1/products", response_model=list[Product])
    def products():
        return list_products(settings)

    @app.get("/api/v1/integrations")
    def integrations():
        items = [("pgcluster", settings.pgcluster_root, "pgcluster")]
        for product_id in sorted(discover_products(settings.products_root)):
            items.append(
                ("%s-regress" % product_id,
                 settings.product_regress_root(product_id), "regress.yaml")
            )
        return [
            {"id": item_id, "path": str(root), "available": (root / entry).is_file()}
            for item_id, root, entry in items
        ]

    @app.get("/api/v1/diagnostics/availability")
    def diagnostic_availability():
        return {
            "configured": bool(os.environ.get("OPENAI_API_KEY")),
            "model": os.environ.get("PRODUCT_PLATFORM_AI_MODEL", "gpt-6-astra"),
        }
