"""Durable automation definitions, run snapshots and notification inbox."""

import json
import re
import uuid

from .backend import now

KINDS = {"pipelines", "runs", "schedules", "notifications", "webhooks", "releases"}


class OrchestrationStore:
    def __init__(self, backend):
        self.backend = backend

    def path(self, kind, identity):
        if (
            kind not in KINDS
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", identity)
            or ".." in identity
        ):
            raise ValueError("运营记录标识无效")
        return self.backend.root / "orchestration" / kind / (identity + ".json")

    def list(self, kind):
        if kind not in KINDS:
            raise ValueError("运营记录类型无效")
        return sorted(
            [
                json.loads(p.read_text())
                for p in (self.backend.root / "orchestration" / kind).glob("*.json")
            ],
            key=lambda row: row["created_at"],
            reverse=True,
        )

    def get(self, kind, identity):
        path = self.path(kind, identity)
        return self.backend._read_json(path)

    def put(self, kind, value, identity=None):
        identity = identity or uuid.uuid4().hex
        with self.backend.transaction():
            previous = self.get(kind, identity)
            row = {
                **value,
                "id": identity,
                "created_at": previous["created_at"] if previous else now(),
                "updated_at": now(),
            }
            self.backend.write_control_file(
                self.path(kind, identity), json.dumps(row, ensure_ascii=False)
            )
            return row

    def delete(self, kind, identity):
        with self.backend.transaction():
            path = self.path(kind, identity)
            if path.exists():
                path.unlink()
                return True
            return False
