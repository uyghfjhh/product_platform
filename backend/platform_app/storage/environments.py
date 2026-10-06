"""Environments repository with a shared transaction backend."""
from pathlib import Path

import yaml

from .backend import ConflictError, now


class EnvironmentsStore:
    def __init__(self, backend, owner):
        self.backend, self.owner = backend, owner
        self.root, self.runtime_dir, self.logs_dir = backend.root, backend.runtime_dir, backend.logs_dir

    def _env_path(self, environment_id: str) -> Path:
        return self.root / "environments" / f"{environment_id}.yaml"

    def mark_deployment(self, environment_id, plan_id, status):
        with self.backend._locked():
            env = self.get_environment(environment_id)
            if not env or env.get("desired_deployment_plan_id") != plan_id:
                return
            env["deployment_status"] = status
            if status == "APPLIED":
                env["applied_deployment_plan_id"] = plan_id
                env["applied_deployment_config"] = env.get("deployment_config")
                env.pop('resource_baseline_config', None)
            self.backend._atomic_write(self._env_path(environment_id),
                               yaml.safe_dump(env, allow_unicode=True, sort_keys=True))

    def list_environments(self) -> list[dict]:
        envs = []
        for path in sorted(self._env_path("*").parent.glob("*.yaml")):
            env = self.backend._read_yaml(path)
            if env:
                envs.append(env)
        envs.sort(key=lambda item: item.get("title") or "")
        return envs

    def get_environment(self, environment_id: str) -> dict | None:
        return self.backend._read_yaml(self._env_path(environment_id))

    def put_environment(self, payload: dict, *, validator=None) -> dict:
        with self.backend._locked():
            if self._env_path(payload["id"]).exists():
                raise ConflictError("环境 ID 已存在")
            if validator:
                validator(self.owner, payload)
            record = {**payload, "created_at": now()}
            self.backend._atomic_write(
                self._env_path(payload["id"]),
                yaml.safe_dump(record, allow_unicode=True, sort_keys=True),
            )
        return self.get_environment(payload["id"]) or {}

    def update_environment(self, environment_id: str, payload: dict, *, validator=None) -> dict | None:
        columns = (
            "product_id", "title", "host", "port", "database_name",
            "database_user", "deployment_config", "deployment_target",
        )
        with self.backend._locked():
            env = self.get_environment(environment_id)
            if env is None:
                return None
            if validator:
                if self.owner.tasks._has_active_task(environment_id):
                    raise ConflictError("环境有活动任务，不能修改登记")
                validator(self.owner, payload)
            env.update({key: payload.get(key) for key in columns})
            for key in ("desired_deployment_plan_id", "deployment_status",
                        "resource_baseline_config"):
                if key in payload:
                    env[key] = payload[key]
            self.backend._atomic_write(
                self._env_path(environment_id),
                yaml.safe_dump(env, allow_unicode=True, sort_keys=True),
            )
        return self.get_environment(environment_id)

    def delete_environment(self, environment_id: str) -> bool:
        with self.backend._locked():
            if not self._env_path(environment_id).exists():
                return False
            if self.owner.tasks._has_active_task(environment_id):
                raise ConflictError("环境仍有未结束的任务")
            # 级联：任务目录（含事件）、结果、诊断、绑定引用
            for meta in (self.root / "tasks").glob("*/meta.json"):
                row = self.backend._read_json(meta)
                if row and row.get("environment_id") == environment_id:
                    for child in meta.parent.iterdir():
                        child.unlink()
                    meta.parent.rmdir()
            for sub in ("results", "diagnoses"):
                env_dir = self.root / sub / environment_id
                if env_dir.is_dir():
                    for child in env_dir.iterdir():
                        child.unlink()
                    env_dir.rmdir()
            bindings = self.owner.bindings._read_bindings()
            changed = False
            for product_id, profiles in list(bindings.items()):
                for profile_id, record in list(profiles.items()):
                    if record.get("environment_id") == environment_id:
                        del profiles[profile_id]
                        changed = True
                if not profiles:
                    del bindings[product_id]
            if changed:
                self.owner.bindings._write_bindings(bindings)
            self._env_path(environment_id).unlink()
        return True
