"""兼容既有 License 文件的 Python 生成模块；不依赖旧 C 服务。"""

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
from datetime import date, datetime
from pathlib import Path

try:
    from argon2.low_level import Type, hash_secret_raw
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from nacl.bindings import (
        crypto_aead_xchacha20poly1305_ietf_decrypt,
        crypto_aead_xchacha20poly1305_ietf_encrypt,
    )
except ImportError:
    Type = None
    hash_secret_raw = None
    Ed25519PrivateKey = None
    crypto_aead_xchacha20poly1305_ietf_decrypt = None
    crypto_aead_xchacha20poly1305_ietf_encrypt = None

from pydantic import BaseModel, Field, model_validator

from .config import Settings

KEY_VERSION = re.compile(r"^1\.([1-9][0-9]*)$")
MAC_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
SIGN_LIMIT = threading.BoundedSemaphore(2)


class LicenseProduct(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=63)
    expiration_at: date

    @model_validator(mode="after")
    def validate_version(self):
        if not self.version.strip() or self.version != self.version.strip() or any(ord(c) < 32 for c in self.version):
            raise ValueError("产品版本不能为空、带首尾空格或控制字符")
        return self


class LicenseInput(BaseModel):
    license_version: str
    start_at: date
    products: list[LicenseProduct] = Field(min_length=1, max_length=100)
    mac_addrs: list[str] = Field(min_length=1, max_length=256)
    purpose: str = Field(default="", max_length=1000)
    password: str = Field(min_length=1, exclude=True)
    save_to_directory: bool = False
    output_directory: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def check_fields(self):
        if self.output_directory is not None:
            from .license_defaults import validate_output_directory
            validate_output_directory(self.output_directory)
        if not KEY_VERSION.fullmatch(self.license_version):
            raise ValueError("License 版本应为 1.<密钥版本>")
        if any(product.expiration_at < self.start_at for product in self.products):
            raise ValueError("产品到期日期不能早于生效日期")
        if any(not MAC_PATTERN.fullmatch(value) for value in self.mac_addrs):
            raise ValueError("MAC 地址格式错误，应为 xx:xx:xx:xx:xx:xx")
        if len({value.lower() for value in self.mac_addrs}) != len(self.mac_addrs):
            raise ValueError("MAC 地址不能重复")
        return self


def _version_dir(key_dir: Path, version: str) -> Path:
    if not KEY_VERSION.fullmatch(version):
        raise ValueError("不支持的 License 版本")
    return key_dir / ("v" + version)


def options(settings: Settings) -> dict:
    """List signable products from installed packages, not legacy config.json.

    Persisted defaults override manifest defaults and the legacy vendor setting.
    Removing a product package immediately removes its new-signing option.
    """
    config_file = settings.license_config
    if not config_file.is_file():
        config_file = config_file.with_name("config.json.exmaple")
    vendor = settings.license_vendor
    if config_file.is_file():
        data = json.loads(config_file.read_text(encoding="utf-8"))
        vendor = data.get("vendor") or vendor
    from .license_defaults import read_defaults
    defaults = read_defaults(settings, vendor=vendor)
    vendor = defaults.vendor
    products = [{"name": name, "version": product.default_version}
                for name, product in defaults.products.items()]
    products.sort(key=lambda item: (item["name"], item["version"]))
    versions = []
    if settings.license_key_dir.is_dir():
        versions = sorted(
            (
                item.name[1:]
                for item in settings.license_key_dir.iterdir()
                if item.is_dir()
                and KEY_VERSION.fullmatch(item.name[1:])
                and (item / "public.pem").is_file()
                and (item / "private.pem").is_file()
            ),
            key=lambda text: int(text.split(".")[1]),
        )
    usable_versions = [v for v in versions if not _key_metadata(settings.license_key_dir, v)["revoked"]]
    return {"vendor": vendor, "products": products, "key_versions": versions,
            "usable_key_versions": usable_versions,
            "defaults": defaults.model_dump()}


def import_legacy_keys(settings: Settings, source: Path) -> list[str]:
    source = Path(source).expanduser().resolve(strict=True)
    target = settings.license_key_dir
    if source == target.resolve():
        raise ValueError("来源与目标密钥目录相同")
    versions = sorted(
        (item for item in source.iterdir()
         if item.is_dir() and not item.is_symlink() and KEY_VERSION.fullmatch(item.name[1:])),
        key=lambda item: int(item.name.split(".")[1]),
    )
    if not versions:
        raise ValueError("来源目录没有可导入的密钥版本")
    for directory in versions:
        if (target / directory.name).exists():
            raise ValueError("目标密钥版本已存在: %s" % directory.name)
        for name in ("public.pem", "private.pem"):
            key = directory / name
            if key.is_symlink() or not key.is_file():
                raise ValueError("密钥对不完整或包含符号链接: %s" % directory.name)
        _key_metadata(source, directory.name[1:])
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in versions:
        destination = target / directory.name
        destination.mkdir(mode=0o700)
        for name in ("public.pem", "private.pem"):
            with (directory / name).open("rb") as reader:
                fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as writer:
                    shutil.copyfileobj(reader, writer)
    return [directory.name[1:] for directory in versions]


def _read_legacy_key(key_dir: Path, version: str, password: str) -> Ed25519PrivateKey:
    directory = _version_dir(key_dir, version)
    key_file = directory / "private.pem"
    public_file = directory / "public.pem"
    try:
        private_lines = key_file.read_text(encoding="ascii").splitlines()
        public_lines = public_file.read_text(encoding="ascii").splitlines()
        packed = base64.b64decode(private_lines[1], validate=True)
        expected_public = bytes.fromhex(public_lines[1])
    except (OSError, IndexError, ValueError) as exc:
        raise ValueError("无法读取所选版本的加密密钥") from exc
    if len(packed) != 120 or len(expected_public) != 32:
        raise ValueError("加密密钥文件格式无效")
    salt, nonce, cipher, tag = packed[:16], packed[16:40], packed[40:104], packed[104:]
    derived = hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=2,
        memory_cost=65536,
        parallelism=1,
        hash_len=32,
        type=Type.ID,
    )
    try:
        private = crypto_aead_xchacha20poly1305_ietf_decrypt(
            cipher + tag, b"", nonce, derived
        )
    except Exception as exc:
        raise ValueError("口令不正确或密钥文件损坏") from exc
    if len(private) != 64 or private[32:] != expected_public:
        raise ValueError("私钥与公钥不匹配")
    signer = Ed25519PrivateKey.from_private_bytes(private[:32])
    actual_public = signer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if actual_public != expected_public:
        raise ValueError("私钥与公钥不匹配")
    return signer


def _key_metadata(key_dir: Path, version: str) -> dict:
    directory = _version_dir(key_dir, version)
    public_file = directory / "public.pem"
    try:
        lines = public_file.read_text(encoding="ascii").splitlines()
        public = bytes.fromhex(lines[1])
    except (OSError, IndexError, ValueError) as exc:
        raise ValueError("无法读取公钥文件") from exc
    if len(public) != 32:
        raise ValueError("公钥文件格式无效")
    revoked_file = key_dir / "revoked.json"
    try:
        revoked = json.loads(revoked_file.read_text(encoding="utf-8")) if revoked_file.is_file() else {}
    except (OSError, ValueError):
        revoked = {}
    return {
        "version": version,
        "public_key": public.hex(),
        "fingerprint": hashlib.sha256(public).hexdigest(),
        "revoked": version in revoked,
        "revoked_at": revoked.get(version),
    }


def key_metadata(settings: Settings, version: str) -> dict:
    return _key_metadata(settings.license_key_dir, version)


def _write_key_pair(key_dir: Path, version: str, signer: Ed25519PrivateKey,
                    password: str, *, replace: bool = False) -> None:
    if not password:
        raise ValueError("密钥口令不能为空")
    directory = _version_dir(key_dir, version)
    directory.mkdir(parents=True, exist_ok=True)
    private_file, public_file = directory / "private.pem", directory / "public.pem"
    if not replace and (private_file.exists() or public_file.exists()):
        raise ValueError("密钥版本已存在")
    public = signer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
    )
    private = signer.private_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ) + public
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(24)
    derived = hash_secret_raw(password.encode("utf-8"), salt, 2, 65536, 1, 32, Type.ID)
    encrypted = crypto_aead_xchacha20poly1305_ietf_encrypt(private, b"", nonce, derived)
    packed = base64.b64encode(salt + nonce + encrypted).decode("ascii")
    public_text = "-----BEGIN PUBLIC KEY-----\n%s\n-----END PUBLIC KEY-----\n" % public.hex()
    private_text = "-----BEGIN ENCRYPTED PRIVATE KEY-----\n%s\n-----END ENCRYPTED PRIVATE KEY-----\n" % packed
    private_file.write_text(private_text, encoding="ascii")
    public_file.write_text(public_text, encoding="ascii")


def generate_key(settings: Settings, version: str, password: str) -> dict:
    if not KEY_VERSION.fullmatch(version):
        raise ValueError("License 版本应为 1.<密钥版本>")
    signer = Ed25519PrivateKey.generate()
    _write_key_pair(settings.license_key_dir, version, signer, password)
    return _key_metadata(settings.license_key_dir, version)


def revoke_key(settings: Settings, version: str, password: str) -> dict:
    _read_legacy_key(settings.license_key_dir, version, password)
    metadata = _key_metadata(settings.license_key_dir, version)
    if metadata["revoked"]:
        return metadata
    revoked_file = settings.license_key_dir / "revoked.json"
    try:
        values = json.loads(revoked_file.read_text(encoding="utf-8")) if revoked_file.is_file() else {}
    except (OSError, ValueError):
        values = {}
    values[version] = datetime.now().astimezone().isoformat(timespec="seconds")
    revoked_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = revoked_file.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(revoked_file)
    return _key_metadata(settings.license_key_dir, version)


def change_key_password(settings: Settings, version: str, old_password: str,
                        new_password: str) -> dict:
    signer = _read_legacy_key(settings.license_key_dir, version, old_password)
    _write_key_pair(settings.license_key_dir, version, signer, new_password, replace=True)
    return _key_metadata(settings.license_key_dir, version)


def delete_key(settings: Settings, version: str, password: str) -> None:
    versions = options(settings)["key_versions"]
    if version not in versions:
        raise ValueError("密钥版本不存在")
    if len(versions) <= 1:
        raise ValueError("不能删除唯一的密钥版本")
    _read_legacy_key(settings.license_key_dir, version, password)
    shutil.rmtree(_version_dir(settings.license_key_dir, version))


def _separator(title: str) -> str:
    width = 80
    left = (width - len(title)) // 2
    return "-" * left + title + "-" * (width - left - len(title))


def _new_license_id() -> str:
    today = datetime.now().strftime("%Y%m%d")
    unique = secrets.token_hex(6).upper()
    middle = "-".join(unique[index : index + 4] for index in (0, 4, 8))
    millis = int(time.time() * 1000) % 86_400_000
    return f"{today}-{middle}-{millis:010d}"


def generate(settings: Settings, request: LicenseInput) -> tuple[bytes, str]:
    """Sign one multi-product License after checking installed product rules."""
    if not SIGN_LIMIT.acquire(blocking=False):
        raise RuntimeError("当前已有两项 License 生成操作，请稍后重试")
    try:
        signing_options = options(settings)
        installed = signing_options['defaults']['products']
        if len({item.name for item in request.products}) != len(request.products):
            raise ValueError("同一 License 中产品不能重复")
        for item in request.products:
            if item.name not in installed:
                raise ValueError(f"产品未安装: {item.name}")
        if _key_metadata(settings.license_key_dir, request.license_version)["revoked"]:
            raise ValueError("所选密钥版本已撤销")
        signer = _read_legacy_key(
            settings.license_key_dir, request.license_version, request.password
        )
        license_id = _new_license_id()
        payload = {
            "products": [
                {
                    "name": item.name,
                    "version": item.version,
                    "expirationAt": item.expiration_at.isoformat(),
                }
                for item in request.products
            ],
            "licenseVersion": request.license_version,
            "startAt": request.start_at.isoformat(),
            "licenseId": license_id,
            "macAddrs": request.mac_addrs,
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        signature = signer.sign(raw)
        b64 = base64.b64encode(signature + raw).decode("ascii")
        lines = [_separator("BEGIN LICENSE")]
        lines.extend(b64[index : index + 76] for index in range(0, len(b64), 76))
        lines.extend(
            (
                _separator("MD5SUM"),
                hashlib.md5(raw).hexdigest(),
                _separator("END LICENSE"),
                f"License编号: {license_id} ",
                f"License版本: {request.license_version}",
                f"厂商: {signing_options['vendor']}",
                f"License用途: {request.purpose}",
                f"生效时间: {request.start_at.isoformat()}",
            )
        )
        return ("\n".join(lines) + "\n").encode("utf-8"), license_id
    finally:
        SIGN_LIMIT.release()


def save_generated_license(settings, content: bytes, directory: str | None = None) -> Path:
    """Atomically replace license.dat, retaining private permissions."""
    from platform_regress.persistence.atomic import (
        atomic_write_text,
        blocking_file_lock,
    )

    from .license_defaults import read_defaults, validate_output_directory
    destination = validate_output_directory(directory or read_defaults(settings).output_directory) / 'license.dat'
    with blocking_file_lock(settings.runtime_dir / 'license-output.lock'):
        atomic_write_text(destination, content.decode('utf-8'))
    return destination
