"""Results repository with a shared transaction backend."""
import json
from pathlib import Path

from .backend import now


class ResultsStore:
    def __init__(self, backend, owner):
        self.backend, self.owner = backend, owner
        self.root, self.runtime_dir, self.logs_dir = backend.root, backend.runtime_dir, backend.logs_dir

    def _result_path(self, environment_id: str, product_id: str,
                     target: str, profile: str) -> Path:
        name = self.backend._key(product_id, target, profile) + ".json"
        return self.root / "results" / environment_id / name

    def put_result(self, product_id: str, environment_id: str, target: str,
                   profile: str, status: str, reason: str | None,
                   artifact_dir: str | None) -> None:
        record = {
            "product_id": product_id,
            "environment_id": environment_id,
            "target": target,
            "profile": profile,
            "status": status,
            "reason": reason,
            "artifact_dir": artifact_dir,
            "updated_at": now(),
        }
        path = self._result_path(environment_id, product_id, target, profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.backend._atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2))

    def list_results(self, environment_id: str) -> list[dict]:
        env_dir = self.root / "results" / environment_id
        rows = []
        if env_dir.is_dir():
            for path in env_dir.glob("*.json"):
                record = self.backend._read_json(path)
                if record:
                    rows.append(record)
        rows.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return rows

    def get_result(self, product_id: str, environment_id: str, target: str,
                   profile: str = "default") -> dict | None:
        return self.backend._read_json(
            self._result_path(environment_id, product_id, target, profile))
