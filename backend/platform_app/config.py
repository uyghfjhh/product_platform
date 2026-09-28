"""平台自身的全局配置与数据路径定义。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    pgcluster_root: Path
    license_key_dir: Path
    license_vendor: str
    regress_roots: Dict[str, Path] = field(default_factory=dict)

    @property
    def platform_dir(self) -> Path:
        """Control-plane state; legacy data root is used for old test fixtures."""
        candidate = self.data_dir / "platform"
        return candidate if candidate.is_dir() else self.data_dir

    @property
    def environment_dir(self) -> Path:
        """Environment profiles and evidence, separate from control state."""
        candidate = self.data_dir / "environments"
        return candidate if candidate.is_dir() else self.data_dir

    @property
    def license_config(self) -> Path:
        return self.license_key_dir.parent / "config.json"

    @property
    def database(self) -> Path:
        return self.platform_dir / "platform.sqlite3"

    @property
    def queue_database(self) -> Path:
        return self.platform_dir / "queue.sqlite3"

    @property
    def frontend_dist(self) -> Path:
        return ROOT / "frontend" / "dist"

    @property
    def products_root(self) -> Path:
        """Installed product packages discovered by the modern catalog."""
        return ROOT / "products"

    def product_regress_root(self, product_id: str) -> Path:
        """Legacy regression tree of one installed product package."""
        override = self.regress_roots.get(product_id)
        if override is not None:
            return Path(override)
        return self.products_root / product_id / "regression" / "legacy"


def load_settings() -> Settings:
    fly_root = ROOT.parent
    data_dir = (
        Path(os.environ.get("PRODUCT_PLATFORM_DATA_DIR", ROOT / "data"))
        .expanduser()
        .resolve()
    )
    return Settings(
        data_dir=data_dir,
        pgcluster_root=Path(
            os.environ.get("PRODUCT_PLATFORM_PGCLUSTER_ROOT", fly_root / "pgcluster")
        )
        .expanduser()
        .resolve(),
        license_key_dir=Path(
            os.environ.get(
                "PRODUCT_PLATFORM_LICENSE_KEYS", data_dir / "license" / "keys"
            )
        )
        .expanduser()
        .resolve(),
        license_vendor=os.environ.get("PRODUCT_PLATFORM_LICENSE_VENDOR", "飞象"),
    )
