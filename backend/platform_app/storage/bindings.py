"""Bindings repository with a shared transaction backend."""
import yaml

from .backend import ConflictError, now


class BindingsStore:
    def __init__(self, backend, owner):
        self.backend, self.owner = backend, owner
        self.root, self.runtime_dir, self.logs_dir = backend.root, backend.runtime_dir, backend.logs_dir

    def _read_bindings(self) -> dict:
        return self.backend._read_yaml(self.root / "bindings.yaml", {})

    def _write_bindings(self, data: dict) -> None:
        self.backend._atomic_write(
            self.root / "bindings.yaml",
            yaml.safe_dump(data, allow_unicode=True, sort_keys=True),
        )

    def list_regression_bindings(self) -> list[dict]:
        rows = []
        for profiles in self._read_bindings().values():
            rows.extend(profiles.values())
        rows.sort(key=lambda item: (item.get("product_id") or "",
                                    item.get("profile_id") or ""))
        return [
            {"product_id": row["product_id"], "profile_id": row["profile_id"],
             "environment_id": row["environment_id"],
             "updated_at": row["updated_at"]}
            for row in rows
        ]

    def get_regression_binding(self, product_id: str,
                               profile_id: str) -> dict | None:
        record = self._read_bindings().get(product_id, {}).get(profile_id)
        if record is None:
            return None
        return {"product_id": record["product_id"],
                "profile_id": record["profile_id"],
                "environment_id": record["environment_id"],
                "updated_at": record["updated_at"]}

    def put_regression_binding(self, product_id: str, profile_id: str,
                               environment_id: str) -> dict:
        with self.backend._locked():
            environment = self.owner.environments.get_environment(environment_id)
            if environment is None or environment.get("product_id") != product_id:
                raise ConflictError("回归测试只能绑定所属产品的环境")
            bindings = self._read_bindings()
            previous = bindings.get(product_id, {}).get(profile_id)
            affected = {environment_id}
            if previous:
                affected.add(previous["environment_id"])
            for affected_id in affected:
                if self.owner.tasks._has_active_task(affected_id):
                    raise ConflictError("环境仍有未结束任务，暂不能变更回归绑定")
            record = {"product_id": product_id, "profile_id": profile_id,
                      "environment_id": environment_id, "updated_at": now()}
            bindings.setdefault(product_id, {})[profile_id] = record
            self._write_bindings(bindings)
        return self.get_regression_binding(product_id, profile_id) or {}

    def delete_regression_binding(self, product_id: str,
                                  profile_id: str) -> bool:
        with self.backend._locked():
            bindings = self._read_bindings()
            existing = bindings.get(product_id, {}).get(profile_id)
            if existing is None:
                return False
            if self.owner.tasks._has_active_task(existing["environment_id"]):
                raise ConflictError("环境仍有未结束任务，暂不能解除回归绑定")
            del bindings[product_id][profile_id]
            if not bindings[product_id]:
                del bindings[product_id]
            self._write_bindings(bindings)
        return True
