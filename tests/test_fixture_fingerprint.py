import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from products.fbasecman.deployment import fixture


def test_local_fingerprint_records_sha256_size_mtime(tmp_path):
    blob = tmp_path / "fbasecman"
    blob.write_bytes(b"binary-bytes")
    entry = fixture._local_fingerprint(str(blob))
    assert entry["sha256"] == hashlib.sha256(b"binary-bytes").hexdigest()
    assert entry["size"] == len(b"binary-bytes")
    assert entry["mtime"] == int(blob.stat().st_mtime)
    assert entry["path"] == str(blob)


def test_local_fingerprint_missing_file():
    entry = fixture._local_fingerprint("/nonexistent/bin/fbasecman")
    assert entry["path"] == "/nonexistent/bin/fbasecman"
    assert "error" in entry


def test_remote_fingerprint_parses_sha256_and_stat():
    class Proc:
        returncode = 0
        stdout = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n42107688 1750324115\n"
        stderr = ""

    with patch.object(fixture.subprocess, "run", return_value=Proc()) as run:
        entry = fixture._remote_fingerprint("192.168.1.24", "postgres", "/pg/bin/postgres")
    assert entry["sha256"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert entry["size"] == 42107688
    assert entry["mtime"] == 1750324115
    argv = run.call_args[0][0]
    assert argv[:2] == ["ssh", "-F"]
    assert "postgres@192.168.1.24" in argv


def test_remote_fingerprint_unreachable():
    class Proc:
        returncode = 255
        stdout = ""
        stderr = "ssh: connect failed"

    with patch.object(fixture.subprocess, "run", return_value=Proc()):
        entry = fixture._remote_fingerprint("192.168.1.24", "postgres", "/pg/bin/postgres")
    assert "error" in entry
    assert entry["path"] == "/pg/bin/postgres"
