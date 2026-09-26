import base64
import subprocess
from datetime import date
from pathlib import Path

import pytest
from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_encrypt
from platform_app.api import create_app
from platform_app.license import _separator
from test_api import settings_for


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


def test_python_license_is_accepted_by_existing_c_verifier(tmp_path):
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
    assert _separator("BEGIN LICENSE").encode() in response.content
    assert password.encode() not in response.content

    filename = tmp_path / "license.dat"
    filename.write_bytes(response.content)
    verifier = Path("/home/postgres/fly_dev/fd_licenser/fd_licenser")
    if not verifier.is_file():
        pytest.skip(f"fd_licenser 校验器未在当前环境找到: {verifier}")
    result = subprocess.run(
        [str(verifier), "check", "-k", str(key_dir), str(filename)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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


def test_generated_v12_key_is_accepted_by_legacy_verifier(tmp_path):
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
    license_file = tmp_path / "generated-license.dat"
    license_file.write_bytes(response.content)
    verifier = Path("/home/postgres/fly_dev/fd_licenser/fd_licenser")
    result = subprocess.run(
        [str(verifier), "check", "-k", str(settings.license_key_dir), str(license_file)],
        cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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
