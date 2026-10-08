"""Persisted signing defaults; installed manifests remain the product boundary."""
import json
from pathlib import Path
from typing import Literal

from platform_regress.persistence.atomic import blocking_file_lock, write_json
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .product_catalog import discover_products


class Duration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    years: int = Field(default=10, ge=0, le=100)
    months: int = Field(default=0, ge=0, le=1200)
    days: int = Field(default=0, ge=0, le=36600)


class ProductDefaults(BaseModel):
    model_config = ConfigDict(extra='forbid')
    default_version: str = Field(min_length=1, max_length=63)
    selected: bool = True
    validity: Duration | None = None

    @model_validator(mode='after')
    def validate_versions(self):
        if (not self.default_version.strip() or self.default_version != self.default_version.strip()
                or any(ord(c) < 32 for c in self.default_version)):
            raise ValueError('默认版本不能为空、带首尾空格或控制字符')
        return self


class LicenseDefaults(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    vendor: str = Field(min_length=1, max_length=255)
    license_version: str | None = None
    default_password: str = Field(default="123456", max_length=1024)
    start_at: Literal['today'] = 'today'
    validity: Duration = Field(default_factory=Duration)
    purpose: str = Field(default='测试', max_length=1000)
    save_to_directory: bool = True
    output_directory: str = Field(default='/home/postgres/lic', min_length=1, max_length=4096)
    mac_addrs: list[str] = Field(default_factory=list, max_length=256)
    products: dict[str, ProductDefaults]

    @model_validator(mode='after')
    def validate_common(self):
        from .license import KEY_VERSION, MAC_PATTERN
        validate_output_directory(self.output_directory)
        if not self.vendor.strip():
            raise ValueError('厂商名称不能为空')
        if self.license_version is not None and not KEY_VERSION.fullmatch(self.license_version):
            raise ValueError('License 版本应为 1.<密钥版本>')
        if any(not MAC_PATTERN.fullmatch(v) for v in self.mac_addrs):
            raise ValueError('MAC 地址格式错误，应为 xx:xx:xx:xx:xx:xx')
        if len({v.lower() for v in self.mac_addrs}) != len(self.mac_addrs):
            raise ValueError('MAC 地址不能重复')
        return self


def validate_output_directory(value):
    if not Path(value).is_absolute() or '..' in Path(value).parts or any(ord(c) < 32 for c in value):
        raise ValueError('License 保存目录必须是绝对路径，不能包含 .. 或控制字符')
    return Path(value)


def descriptors(settings):
    return {d.product_code: d for m in discover_products(settings.products_root).values()
            if m.license for d in (m.license, *m.license.additional_products)}


def defaults_path(settings):
    return settings.config_dir / 'license' / 'defaults.json'


def read_defaults(settings, *, vendor=None):
    initial = LicenseDefaults(vendor=vendor or settings.license_vendor, products={
        name: ProductDefaults(default_version=d.default_version)
        for name, d in descriptors(settings).items()})
    path = defaults_path(settings)
    if path.exists():
        saved = LicenseDefaults.model_validate(json.loads(path.read_text()))
        initial = saved.model_copy(update={'products': {
            name: saved.products.get(name, product) for name, product in initial.products.items()}})
    return initial


def save_defaults(settings, value):
    from .license import key_metadata, options
    installed = descriptors(settings)
    if set(value.products) != set(installed):
        raise ValueError('产品目录已变化，请刷新设置后保存')
    if value.license_version:
        if value.license_version not in options(settings)['key_versions'] or key_metadata(settings, value.license_version)['revoked']:
            raise ValueError('默认密钥版本不存在或已撤销')
    path = defaults_path(settings)
    with blocking_file_lock(settings.runtime_dir / 'license-defaults.lock'):
        previous = LicenseDefaults.model_validate(json.loads(path.read_text())) if path.exists() else None
        data = value.model_dump()
        if previous:
            data['products'] = {**{name: p.model_dump() for name, p in previous.products.items()}, **data['products']}
        write_json(path, data)
    return value


class DefaultKeyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: str = Field(pattern=r'^1\.[1-9][0-9]*$')


def set_default_key(settings, version):
    from .license import options
    if version not in options(settings)['usable_key_versions']:
        raise ValueError('所选密钥不存在或不可签发')
    path = defaults_path(settings)
    with blocking_file_lock(settings.runtime_dir / 'license-defaults.lock'):
        value = (LicenseDefaults.model_validate(json.loads(path.read_text())) if path.exists()
                 else read_defaults(settings, vendor=options(settings)['vendor']))
        value.license_version = version
        write_json(path, value.model_dump())
    return {'license_version': version}
