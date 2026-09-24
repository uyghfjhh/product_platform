"""兼容既有 License 文件的 Python 生成模块；不依赖旧 C 服务。"""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from datetime import date, datetime
from pathlib import Path

try:
    from argon2.low_level import Type, hash_secret_raw
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt
except ImportError:
    Type = None
    hash_secret_raw = None
    Ed25519PrivateKey = None
    crypto_aead_xchacha20poly1305_ietf_decrypt = None

from pydantic import BaseModel, Field, model_validator

from .config import Settings


KEY_VERSION = re.compile(r"^1\.([1-9][0-9]*)$")
MAC_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
SIGN_LIMIT = threading.BoundedSemaphore(2)


class LicenseProduct(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=63)
    expiration_at: date


class LicenseInput(BaseModel):
    license_version: str
    start_at: date
    products: list[LicenseProduct] = Field(min_length=1, max_length=100)
    mac_addrs: list[str] = Field(min_length=1, max_length=256)
    purpose: str = Field(default="", max_length=1000)
    password: str = Field(min_length=1, exclude=True)

    @model_validator(mode="after")
    def check_fields(self):
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
    config_file = settings.license_config
    if not config_file.is_file():
        config_file = config_file.with_name("config.json.exmaple")
    products = []
    vendor = settings.license_vendor
    if config_file.is_file():
        data = json.loads(config_file.read_text(encoding="utf-8"))
        vendor = data.get("vendor") or vendor
        products = [
            {"name": name, "version": (value or {}).get("productVersion", "")}
            for name, value in (data.get("products") or {}).items()
        ]
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
    return {"vendor": vendor, "products": products, "key_versions": versions}


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
    from cryptography.hazmat.primitives import serialization

    actual_public = signer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if actual_public != expected_public:
        raise ValueError("私钥与公钥不匹配")
    return signer


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
    if not SIGN_LIMIT.acquire(blocking=False):
        raise RuntimeError("当前已有两项 License 生成操作，请稍后重试")
    try:
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
                f"厂商: {options(settings)['vendor']}",
                f"License用途: {request.purpose}",
                f"生效时间: {request.start_at.isoformat()}",
            )
        )
        return ("\n".join(lines) + "\n").encode("utf-8"), license_id
    finally:
        SIGN_LIMIT.release()
