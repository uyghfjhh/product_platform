"""Single registration point for product metadata and platform capabilities."""

import os
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT, Settings

DEPLOYMENT_ACTIONS = frozenset({
    "deployment.validate", "deployment.status", "deployment.health",
    "deployment.doctor", "deployment.heal",
    "deployment.create", "deployment.start", "deployment.stop",
    "deployment.restart", "deployment.clean", "deployment.failover",
    "deployment.rejoin",
})


@dataclass(frozen=True)
class ProductSpec:
    id: str
    title: str
    description: str
    capabilities: tuple[str, ...]
    actions: frozenset[str]
    regress_setting: str
    source_env: str
    source_default: Path
    discovery_kind: str

    def regress_root(self, settings: Settings) -> Path:
        return getattr(settings, self.regress_setting)

    def source_root(self) -> Path:
        return Path(os.environ.get(self.source_env, str(self.source_default))).expanduser().resolve()


PRODUCTS = {
    item.id: item for item in (
        ProductSpec(
            id="fbase-database", title="FBase 数据库",
            description="数据库内核、多活与企业版能力",
            capabilities=("deployment", "database", "tests", "knowledge", "license", "diagnostics"),
            actions=frozenset({"database.check", "tests.fbase", "diagnostics.analyze"}),
            regress_setting="fbase_regress_root",
            source_env="PRODUCT_PLATFORM_FBASE_SOURCE_ROOT",
            source_default=ROOT.parent / "postgresql_for_fbase_dev",
            discovery_kind="fbase",
        ),
        ProductSpec(
            id="fbasecman", title="fbasecman",
            description="数据库代理、路由与高可用",
            capabilities=("deployment", "database", "tests", "stability", "knowledge", "license", "diagnostics"),
            actions=frozenset({
                "database.check", "tests.fbasecman", "tests.prepare_fbasecman",
                "stability.fbasecman", "diagnostics.analyze",
            }),
            regress_setting="fbasecman_regress_root",
            source_env="PRODUCT_PLATFORM_CMAN_SOURCE_ROOT",
            source_default=ROOT.parent / "fbasecman_dev",
            discovery_kind="cman",
        ),
    )
}


def get_product_spec(product_id: str) -> ProductSpec | None:
    return PRODUCTS.get(product_id)
