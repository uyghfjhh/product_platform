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
    output_dir: Path | None = None

    def __post_init__(self):
        # data/ 是控制面状态；output/ 是回归产物耗材区（可随意删除重跑）。
        if self.output_dir is None:
            object.__setattr__(self, "output_dir",
                             self.data_dir.parent / "output")

    @property
    def platform_dir(self) -> Path:
        """Control-plane state; legacy data root is used for old test fixtures."""
        candidate = self.data_dir / "platform"
        return candidate if candidate.is_dir() else self.data_dir

    @property
    def environment_dir(self) -> Path:
        """Environment profiles root (``data/profiles/<env>``).

        FileStore keeps environment *records* at ``data_dir/environments``;
        disposable regression output lives under ``output_dir`` instead.
        """
        return self.data_dir

    @property
    def license_config(self) -> Path:
        return self.license_key_dir.parent / "config.json"

    @property
    def frontend_dist(self) -> Path:
        return ROOT / "frontend" / "dist"

    @property
    def products_root(self) -> Path:
        """Installed product packages discovered by the modern catalog."""
        return ROOT / "products"

    def product_regress_root(self, product_id: str) -> Path:
        """Regression resource tree of one installed product package."""
        override = self.regress_roots.get(product_id)
        if override is not None:
            return Path(override)
        return self.products_root / product_id / "regression"


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
        output_dir=Path(
            os.environ.get("PRODUCT_PLATFORM_OUTPUT_DIR",
                           data_dir.parent / "output")
        ).expanduser().resolve(),
    )
