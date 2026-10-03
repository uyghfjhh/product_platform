import base64
import hashlib
import json
import stat
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from fastapi.testclient import TestClient
from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_encrypt
from platform_app.api import create_app
from platform_app.license import _separator
from platform_app.config import Settings
from test_api import settings_for


def _decode_license_body(content: bytes):
    """Split a signed license file into signature, raw payload and footer."""
    lines = content.decode("utf-8").splitlines()
    assert lines[0] == _separator("BEGIN LICENSE")
    md5_index = lines.index(_separator("MD5SUM"))
    end_index = lines.index(_separator("END LICENSE"))
    packed = base64.b64decode("".join(lines[1:md5_index]), validate=True)
    signature, raw = packed[:64], packed[64:]
    assert lines[md5_index + 1] == hashlib.md5(raw).hexdigest()
    return signature, raw, lines[end_index + 1:]


def _public_key_for(key_dir: Path, version: str) -> Ed25519PublicKey:
    lines = (key_dir / f"v{version}" / "public.pem").read_text("ascii").splitlines()
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(lines[1]))


def legacy_key_fixture(tmp_path):
    """用公开的固定测试种子构造旧格式，测试不使用用户签发私钥。"""
    root = tmp_path / "keys"
    version = root / "v1.1"
    version.mkdir(parents=True)
    seed = bytes(range(32))
    password = "test-password"
    public = (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    salt, nonce = bytes(range(16)), bytes(range(24))
    key = hash_secret_raw(password.encode(), salt, 2, 65536, 1, 32, Type.ID)
    cipher_and_tag = crypto_aead_xchacha20poly1305_ietf_encrypt(
        seed + public, b"", nonce, key
    )
    packed = base64.b64encode(salt + nonce + cipher_and_tag).decode("ascii")
    (version / "public.pem").write_text(
        "-----BEGIN PUBLIC KEY-----\n%s\n-----END PUBLIC KEY-----\n" % public.hex(),
        encoding="ascii",
    )
    (version / "private.pem").write_text(
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\n%s\n-----END ENCRYPTED PRIVATE KEY-----\n"
        % packed,
        encoding="ascii",
    )
    return root, password


def test_python_license_verifies_against_own_key_material(tmp_path):
    settings = settings_for(tmp_path)
    key_dir, password = legacy_key_fixture(tmp_path)
    client = TestClient(create_app(settings, enqueuer=lambda task_id: None))
    assert client.get("/api/v1/licenses/options").json()["key_versions"] == ["1.1"]
    response = client.post(
        "/api/v1/licenses/generate",
        json={
            "license_version": "1.1",
            "start_at": "2026-09-23",
            "products": [
                {"name": "fbasecman", "version": "1.7", "expiration_at": "2027-09-23"}
            ],
            "mac_addrs": ["02:42:8e:0f:0b:1b"],
            "purpose": "测试签发",
            "password": password,
        },
    )
    assert response.status_code == 200, response.text
    assert (
        response.headers["content-disposition"] == 'attachment; filename="license.dat"'
    )
    assert password.encode() not in response.content

    signature, raw, footer = _decode_license_body(response.content)
    _public_key_for(key_dir, "1.1").verify(signature, raw)
    payload = json.loads(raw)
    assert payload["licenseVersion"] == "1.1"
    assert payload["startAt"] == "2026-09-23"
    assert payload["products"] == [
        {"name": "fbasecman", "version": "1.7", "expirationAt": "2027-09-23"}
    ]
    assert payload["macAddrs"] == ["02:42:8e:0f:0b:1b"]
    assert payload["licenseId"] in footer[0]
    assert any("测试签发" in line for line in footer)


def test_license_input_rejects_invalid_date_and_mac(tmp_path):
    settings = settings_for(tmp_path)
    key_dir, password = legacy_key_fixture(tmp_path)
    client = TestClient(create_app(settings, enqueuer=lambda task_id: None))
    response = client.post(
        "/api/v1/licenses/generate",
        json={
            "license_version": "1.1",
            "start_at": date.today().isoformat(),
            "products": [
                {"name": "fbasecman", "version": "1.7", "expiration_at": "2020-01-01"}
            ],
            "mac_addrs": ["bad-mac"],
            "password": password,
        },
    )
    assert response.status_code == 422


def test_key_generation_metadata_and_password_rotation(tmp_path):
    from platform_app.license import (
        _read_legacy_key,
        change_key_password,
        generate_key,
        key_metadata,
    )

    config = settings_for(tmp_path)
    created = generate_key(config, "1.2", "old-password")
    assert created["version"] == "1.2"
    assert len(created["public_key"]) == 64
    assert key_metadata(config, "1.2")["fingerprint"] == created["fingerprint"]
    rotated = change_key_password(config, "1.2", "old-password", "new-password")
    assert rotated["public_key"] == created["public_key"]
    _read_legacy_key(config.license_key_dir, "1.2", "new-password")
    with pytest.raises(ValueError, match="口令不正确"):
        _read_legacy_key(config.license_key_dir, "1.2", "old-password")


def test_key_delete_requires_password_and_preserves_last_version(tmp_path):
    from platform_app.license import delete_key, generate_key

    config = settings_for(tmp_path)
    generate_key(config, "1.1", "one")
    generate_key(config, "1.2", "two")
    with pytest.raises(ValueError, match="口令不正确"):
        delete_key(config, "1.2", "bad")
    delete_key(config, "1.2", "two")
    with pytest.raises(ValueError, match="唯一"):
        delete_key(config, "1.1", "one")


def test_generated_v12_key_signs_verifiable_license(tmp_path):
    from platform_app.license import generate_key

    settings = settings_for(tmp_path)
    generate_key(settings, "1.2", "generated-password")
    client = TestClient(create_app(settings, enqueuer=lambda task_id: None))
    response = client.post("/api/v1/licenses/generate", json={
        "license_version": "1.2", "start_at": "2026-09-25",
        "products": [{"name": "fbasecman", "version": "1.7", "expiration_at": "2027-09-25"}],
        "mac_addrs": ["02:42:8e:0f:0b:1b"], "password": "generated-password",
    })
    assert response.status_code == 200, response.text
    signature, raw, footer = _decode_license_body(response.content)
    _public_key_for(settings.license_key_dir, "1.2").verify(signature, raw)
    payload = json.loads(raw)
    assert payload["licenseVersion"] == "1.2"
    assert payload["products"][0]["expirationAt"] == "2027-09-25"
    assert any("License版本: 1.2" in line for line in footer)


def test_key_management_http_contract(tmp_path):
    settings = settings_for(tmp_path)
    client = TestClient(create_app(settings, enqueuer=lambda task_id: None))
    created = client.post("/api/v1/licenses/keys", json={
        "version": "1.3", "password": "first-password",
    })
    assert created.status_code == 200, created.text
    assert created.json()["version"] == "1.3"
    assert client.get("/api/v1/licenses/keys/1.3").json()["fingerprint"]
    rotated = client.post("/api/v1/licenses/keys/1.3/password", json={
        "old_password": "first-password", "new_password": "second-password",
    })
    assert rotated.status_code == 200, rotated.text
    deleted = client.request("DELETE", "/api/v1/licenses/keys/1.3", json={"password": "second-password"})
    assert deleted.status_code == 422
    assert "唯一" in deleted.json()["detail"]
    assert client.post("/api/v1/licenses/keys", json={"version": "bad", "password": "x"}).status_code == 422
    assert client.post("/api/v1/licenses/keys", json={"version": "1.4", "password": ""}).status_code == 422


def test_revoked_key_cannot_sign_new_license(tmp_path):
    from platform_app.license import revoke_key, generate_key

    settings = settings_for(tmp_path)
    generate_key(settings, "1.5", "revoke-password")
    metadata = revoke_key(settings, "1.5", "revoke-password")
    assert metadata["revoked"] is True
    client = TestClient(create_app(settings, enqueuer=lambda task_id: None))
    response = client.post("/api/v1/licenses/generate", json={
        "license_version": "1.5", "start_at": "2026-09-25",
        "products": [{"name": "fbasecman", "version": "1.7", "expiration_at": "2027-09-25"}],
        "mac_addrs": ["02:42:8e:0f:0b:1b"], "password": "revoke-password",
    })
    assert response.status_code == 422


def test_uninstalled_product_cannot_be_signed(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    _key_dir, password = legacy_key_fixture(tmp_path)
    package = tmp_path / "products" / "demo"
    package.mkdir(parents=True)
    manifest = package / "product.yaml"
    manifest.write_text(
        "id: demo\ntitle: Demo\nplugin_api: v1\nregression_sdk: '2'\ncapabilities:\n  license: fd-licenser\n"
        "license:\n  product_code: demo\n  allowed_versions: ['1.0']\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Settings, "products_root", property(lambda self: package.parent))
    client = TestClient(create_app(settings, enqueuer=lambda task_id: None))
    payload = {
        "license_version": "1.1", "start_at": "2026-09-25",
        "products": [{"name": "demo", "version": "1.0", "expiration_at": "2027-09-25"}],
        "mac_addrs": ["02:42:8e:0f:0b:1b"], "password": password,
    }
    assert client.get("/api/v1/licenses/options").json()["products"] == [{"name": "demo", "version": "1.0"}]
    assert client.post("/api/v1/licenses/generate", json=payload).status_code == 200

    manifest.unlink()
    assert client.get("/api/v1/licenses/options").json()["products"] == []
    rejected = client.post("/api/v1/licenses/generate", json=payload)
    assert rejected.status_code == 422
    assert "未安装" in rejected.json()["detail"]


def test_versioned_public_keys_do_not_enable_signing(tmp_path):
    from platform_app.license import key_metadata, options

    public_keys = Path(__file__).resolve().parents[1] / "license" / "public_keys"
    settings = replace(settings_for(tmp_path), license_key_dir=public_keys)
    for version in ("1.1", "1.2", "1.3"):
        public = bytes.fromhex(key_metadata(settings, version)["public_key"])
        Ed25519PublicKey.from_public_bytes(public)
        assert len(public) == 32
        assert not (public_keys / f"v{version}" / "private.pem").exists()
    assert options(settings)["key_versions"] == []


def test_import_legacy_key_pair_without_overwriting(tmp_path):
    from platform_app.license import _read_legacy_key, import_legacy_keys, options

    source, password = legacy_key_fixture(tmp_path / "old")
    settings = settings_for(tmp_path)
    assert import_legacy_keys(settings, source) == ["1.1"]
    assert options(settings)["key_versions"] == ["1.1"]
    private = settings.license_key_dir / "v1.1" / "private.pem"
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert _read_legacy_key(settings.license_key_dir, "1.1", password)
    with pytest.raises(ValueError, match="已存在"):
        import_legacy_keys(settings, source)
    assert private.read_bytes() == (source / "v1.1" / "private.pem").read_bytes()
