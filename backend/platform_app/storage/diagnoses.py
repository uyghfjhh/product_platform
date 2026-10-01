"""Diagnoses repository with a shared transaction backend."""
import json
from pathlib import Path

from .backend import now


class DiagnosesStore:
    def __init__(self, backend, owner):
        self.backend, self.owner = backend, owner
        self.root, self.runtime_dir, self.logs_dir = backend.root, backend.runtime_dir, backend.logs_dir

    def _diagnosis_path(self, environment_id: str, product_id: str,
                        target: str, profile: str) -> Path:
        name = self.backend._key(product_id, target, profile) + ".json"
        return self.root / "diagnoses" / environment_id / name

    def put_diagnosis(self, result: dict, evidence_hash: str, model: str,
                      content: dict) -> None:
        record = {
            "product_id": result["product_id"],
            "environment_id": result["environment_id"],
            "target": result["target"],
            "profile": result["profile"],
            "result_updated_at": result["updated_at"],
            "evidence_hash": evidence_hash,
            "model": model,
            "content": content,
            "created_at": now(),
        }
        path = self._diagnosis_path(
            record["environment_id"], record["product_id"],
            record["target"], record["profile"])
        path.parent.mkdir(parents=True, exist_ok=True)
        self.backend._atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2))

    def get_diagnosis(self, product_id: str, environment_id: str, target: str,
                      profile: str = "default") -> dict | None:
        record = self.backend._read_json(
            self._diagnosis_path(environment_id, product_id, target, profile))
        if record is None:
            return None
        current = self.owner.results.get_result(product_id, environment_id, target, profile)
        record["stale"] = (
            record["result_updated_at"]
            != (current or {}).get("updated_at")
        )
        return record
