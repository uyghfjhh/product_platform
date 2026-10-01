"""Backend repository with a shared transaction backend."""
import fcntl
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import yaml


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")

class ConflictError(RuntimeError):
    pass

ACTIVE_STATUSES = ("QUEUED", "RUNNING", "CANCELLING")
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}


class StorageBackend:
    def __init__(self, data_dir, runtime_dir, logs_dir):
        self.root = Path(data_dir)
        self.runtime_dir = Path(runtime_dir)
        self.logs_dir = Path(logs_dir)
        (self.runtime_dir / "locks").mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """序列化同一控制面内跨进程的写操作。"""
        lock_path = self.runtime_dir / "locks" / "store.lock"
        with open(lock_path, "a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            StorageBackend._sync_directory(path.parent)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _sync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _read_yaml(path: Path, default=None):
        if not path.is_file():
            return default
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle) or default

    @staticmethod
    def _read_json(path: Path, default=None):
        if not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default

    @staticmethod
    def _key(*parts: str) -> str:
        return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:24]

    @contextmanager
    def transaction(self):
        """Serialize a control-plane operation; never nest this context."""
        with self._locked():
            yield

    def write_control_file(self, path: Path, content: str):
        """Durably publish a file inside this store's control-plane root."""
        path = Path(path).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("控制面文件路径越界")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, content)
