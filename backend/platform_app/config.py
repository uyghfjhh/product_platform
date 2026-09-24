"""平台自身的全局配置与数据路径定义。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    pgcluster_root: Path
    fbasecman_regress_root: Path
    fbase_regress_root: Path
    license_key_dir: Path
    license_vendor: str

    @property
    def license_config(self) -> Path:
        return self.license_key_dir.parent / "config.json"

    @property
    def database(self) -> Path:
        return self.data_dir / "platform.sqlite3"

    @property
    def queue_database(self) -> Path:
        return self.data_dir / "queue.sqlite3"

    @property
    def frontend_dist(self) -> Path:
        return ROOT / "frontend" / "dist"


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
        fbasecman_regress_root=Path(
            os.environ.get(
                "PRODUCT_PLATFORM_CMAN_REGRESS_ROOT",
                ROOT / "regress" / "fbasecman" if (ROOT / "regress" / "fbasecman").is_dir() else ROOT,
            )
        )
        .expanduser()
        .resolve(),
        fbase_regress_root=Path(
            os.environ.get(
                "PRODUCT_PLATFORM_FBASE_REGRESS_ROOT",
                fly_root / "postgresql_for_fbase_dev" / "fbase_regress",
            )
        )
        .expanduser()
        .resolve(),
        license_key_dir=Path(
            os.environ.get(
                "PRODUCT_PLATFORM_LICENSE_KEYS", fly_root / "fd_licenser" / "keys"
            )
        )
        .expanduser()
        .resolve(),
        license_vendor=os.environ.get("PRODUCT_PLATFORM_LICENSE_VENDOR", "飞象"),
    )
