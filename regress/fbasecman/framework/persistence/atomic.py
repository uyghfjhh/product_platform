"""Small crash-safe persistence primitives with process coordination."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    """原子写 JSON 文件：先写临时文件再 rename，读取方不会看到半截内容。"""
    atomic_write_text(
        path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def blocking_file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
