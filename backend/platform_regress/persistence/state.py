"""Versioned file state with isolated defaults and locked read-modify-write."""

import copy
import json
from pathlib import Path

from . import atomic_write_text, blocking_file_lock


class JsonStateStore:
    def __init__(self, path, *, defaults, schema_version=1, accepted_versions=(1,)):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(".lock")
        self.defaults = copy.deepcopy(defaults)
        self.schema_version, self.accepted_versions = schema_version, accepted_versions

    def load(self):
        state = copy.deepcopy(self.defaults)
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or value.get("schema_version", 0) not in self.accepted_versions
            ):
                raise ValueError("unsupported state schema")
            state.update(value)
        return state

    def _write(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(state, indent=2, sort_keys=True) + "\n")

    def save(self, value):
        with blocking_file_lock(self.lock_path):
            payload = copy.deepcopy(value)
            payload["schema_version"] = self.schema_version
            payload["revision"] = int(self.load().get("revision", 0)) + 1
            self._write(payload)
            value.update(payload)

    def update(self, mutator):
        with blocking_file_lock(self.lock_path):
            state = self.load()
            result = mutator(state)
            state["schema_version"] = self.schema_version
            state["revision"] = int(state.get("revision", 0)) + 1
            self._write(state)
            return state if result is None else result
