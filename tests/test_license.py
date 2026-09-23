import base64
import subprocess
from datetime import date

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
    verifier = "/home/postgres/fly_dev/fd_licenser/fd_licenser"
    result = subprocess.run(
        [verifier, "check", "-k", str(key_dir), str(filename)],
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
