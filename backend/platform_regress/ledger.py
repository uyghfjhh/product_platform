"""Persistent registry of engine-owned external resources.

Deferred cleanups restore the environment on every orderly exit — success,
failure, timeout and SIGTERM cancellation alike.  A hard kill (SIGKILL, OOM,
power loss) cannot run any cleanup, so resources the engine created
(isolated postmasters, reserved listeners, temporary clusters) would leak
into later runs.  This ledger keeps a small JSON record per resource; a
later run sweeps entries whose owning process is dead and reclaims them.

Entries are individual files under the ledger root so concurrent engines on
the same environment never contend on a shared lock.  All operations are
best-effort: a corrupt or partial entry is ignored rather than fatal.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


def _pid_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class ResourceLedger:
    """One-file-per-resource registry keyed by owning engine pid."""

    def __init__(self, root: Path | str, owner_pid: int | None = None):
        self.root = Path(root)
        self.owner_pid = owner_pid or os.getpid()
        self.root.mkdir(parents=True, exist_ok=True)

    def register(self, kind: str, **payload: Any) -> str:
        """Record a resource owned by this engine process; returns entry id."""
        entry_id = uuid.uuid4().hex[:12]
        entry = {"id": entry_id, "kind": kind, "owner_pid": self.owner_pid,
                 "created_at": time.time()}
        entry.update(payload)
        path = self.root / f"{self.owner_pid}-{entry_id}.json"
        path.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        return entry_id

    def release(self, entry_id: str | None) -> None:
        """Drop an entry after its resource was reclaimed normally."""
        if not entry_id:
            return
        for path in self.root.glob(f"*-{entry_id}.json"):
            try:
                path.unlink()
            except OSError:
                pass

    def entries(self) -> list[dict[str, Any]]:
        rows = []
        for path in sorted(self.root.glob("*.json")):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(entry, dict) and entry.get("id"):
                entry["_path"] = str(path)
                rows.append(entry)
        return rows

    def sweep(self, kind: str | Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Entries whose owning engine process no longer exists.

        ``kind`` narrows the result (one name or an iterable).  Entries owned
        by live processes — including this process — are never returned.
        """
        kinds = {kind} if isinstance(kind, str) else (set(kind) if kind else None)
        dead = []
        for entry in self.entries():
            if kinds is not None and entry.get("kind") not in kinds:
                continue
            if not _pid_alive(entry.get("owner_pid")):
                dead.append(entry)
        return dead

    def drop(self, entry: dict[str, Any]) -> None:
        """Remove a swept entry after reclaim finishes."""
        path = entry.get("_path")
        if path:
            try:
                Path(path).unlink()
            except OSError:
                pass
        else:
            self.release(entry.get("id"))


def _parse_ipcs_shm(segments_text: str, pids_text: str) -> list[dict[str, Any]]:
    """Merge ``ipcs -m`` and ``ipcs -mp`` output into segment records."""
    segments: dict[str, dict[str, Any]] = {}
    for text, section in ((segments_text, "segments"), (pids_text, "pids")):
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("-"):
                continue
            parts = stripped.split()
            if parts[0] in ("key", "shmid"):
                continue
            if section == "segments" and len(parts) >= 6 and parts[0].startswith("0x"):
                try:
                    segments[parts[1]] = {"shmid": int(parts[1]), "key": parts[0],
                                          "owner": parts[2], "bytes": int(parts[4]),
                                          "nattch": int(parts[5])}
                except ValueError:
                    continue
            elif section == "pids" and len(parts) >= 4 and parts[0].isdigit():
                segment = segments.get(parts[0])
                if segment is not None:
                    try:
                        segment["cpid"], segment["lpid"] = int(parts[2]), int(parts[3])
                    except ValueError:
                        continue
    return list(segments.values())


def orphaned_sysv_shm(owner: str | None = None) -> list[dict[str, Any]]:
    """SysV shared-memory segments whose creator process is dead.

    A postmaster killed with SIGKILL (or lost to a power cycle) leaves its
    SysV segment behind; the next start then fails with ``pre-existing
    shared memory block ... is still in use``.  A segment is an orphan only
    when its creator pid is gone — ``nattch=0`` alone is not proof because
    live postmasters hold lazily-attached dynamic segments.
    """
    import subprocess

    def ipcs(*flags: str) -> str:
        return subprocess.run(["ipcs", *flags], capture_output=True,
                              text=True, timeout=10).stdout or ""

    orphans = []
    for segment in _parse_ipcs_shm(ipcs("-m"), ipcs("-mp")):
        if owner is not None and segment.get("owner") != owner:
            continue
        cpid = segment.get("cpid")
        if cpid is not None and not _pid_alive(cpid):
            orphans.append(segment)
    return orphans


def sweep_orphaned_sysv_shm(owner: str | None = None) -> list[dict[str, Any]]:
    """Remove creator-dead SysV segments; returns the segments removed."""
    import subprocess
    removed = []
    for segment in orphaned_sysv_shm(owner):
        result = subprocess.run(["ipcrm", "-m", str(segment["shmid"])],
                                capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            removed.append(segment)
    return removed
