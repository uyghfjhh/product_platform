"""Authoritative directory layout for source, state, runtime and artifacts."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SCOPE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')


def scope(value: str) -> str:
    if not _SCOPE.fullmatch(value) or value in {'.', '..'}:
        raise ValueError('目录标识无效')
    return value


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    pgcluster_root: Path
    license_key_dir: Path
    license_vendor: str
    regress_roots: dict[str, Path] = field(default_factory=dict)
    output_dir: Path | None = None
    runtime_dir: Path | None = None
    logs_dir: Path | None = None

    def __post_init__(self):
        for name in ('output_dir', 'runtime_dir', 'logs_dir'):
            if getattr(self, name) is None:
                directory = {'output_dir': 'output', 'runtime_dir': 'runtime', 'logs_dir': 'logs'}[name]
                object.__setattr__(self, name, self.data_dir.parent / directory)

    @property
    def environment_records_dir(self):
        return self.data_dir / 'environments'

    @property
    def profiles_dir(self):
        return self.data_dir / 'profiles'

    def profile_dir(self, environment_id):
        return self.profiles_dir / scope(environment_id)

    def resource_dir(self, product_id, environment_id):
        return self.data_dir / 'resources' / scope(product_id) / scope(environment_id)

    def regression_state_dir(self, product_id, environment_id):
        return self.data_dir / 'regression' / scope(product_id) / scope(environment_id)

    def artifact_dir(self, product_id, environment_id):
        return self.output_dir / scope(product_id) / scope(environment_id)

    @property
    def license_config(self):
        return self.license_key_dir.parent / 'config.json'

    @property
    def frontend_dist(self):
        return ROOT / 'frontend' / 'dist'

    @property
    def products_root(self):
        return ROOT / 'products'

    def product_regress_root(self, product_id):
        return Path(self.regress_roots.get(product_id, self.products_root / scope(product_id) / 'regression'))


def load_settings():
    def directory(variable, default):
        return Path(os.environ.get(variable, default)).expanduser().resolve()
    data = directory('PRODUCT_PLATFORM_DATA_DIR', ROOT / 'data')
    return Settings(
        data_dir=data,
        pgcluster_root=directory('PRODUCT_PLATFORM_PGCLUSTER_ROOT', ROOT.parent / 'pgcluster'),
        license_key_dir=directory('PRODUCT_PLATFORM_LICENSE_KEYS', data / 'license' / 'keys'),
        license_vendor=os.environ.get('PRODUCT_PLATFORM_LICENSE_VENDOR', '飞象'),
        output_dir=directory('PRODUCT_PLATFORM_OUTPUT_DIR', data.parent / 'output'),
        runtime_dir=directory('PRODUCT_PLATFORM_RUNTIME_DIR', data.parent / 'runtime'),
        logs_dir=directory('PRODUCT_PLATFORM_LOGS_DIR', data.parent / 'logs'),
    )
