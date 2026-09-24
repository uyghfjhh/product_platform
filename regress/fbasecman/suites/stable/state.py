"""JSON runtime state for foreground and background stable runs."""

import json
from pathlib import Path

from framework.persistence import atomic_write_text, blocking_file_lock


DEFAULT_STATE = {
    "schema_version": 1, "revision": 0,
    "status": "stopped", "run_id": "", "run_dir": "", "started_at": 0,
    "fbasecman_pid": 0, "monitor_pid": 0, "supervisor_pid": 0,
    "workloads": {}, "commands": {},
}


class StateStore(object):
    def __init__(self, path):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(".lock")

    def load(self):
        if not self.path.exists():
            return dict(DEFAULT_STATE)
        value = json.loads(self.path.read_text(encoding="utf-8"))
        version = value.get("schema_version", 0)
        if version not in (0, 1):
            raise RuntimeError("unsupported stable state schema: %s" % version)
        result = dict(DEFAULT_STATE)
        result.update(value)
        return result

    def save(self, value):
        with blocking_file_lock(self.lock_path):
            current_revision = 0
            if self.path.exists():
                current = json.loads(self.path.read_text(encoding="utf-8"))
                current_revision = int(current.get("revision", 0))
            payload = dict(value)
            payload["schema_version"] = 1
            payload["revision"] = current_revision + 1
            atomic_write_text(
                self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n",
            )
            value.update(payload)

    def update(self, mutator):
        """Apply one short read-modify-write transaction under the state lock."""
        with blocking_file_lock(self.lock_path):
            state = dict(DEFAULT_STATE)
            if self.path.exists():
                state.update(json.loads(self.path.read_text(encoding="utf-8")))
            result = mutator(state)
            state["schema_version"] = 1
            state["revision"] = int(state.get("revision", 0)) + 1
            atomic_write_text(
                self.path, json.dumps(state, indent=2, sort_keys=True) + "\n",
            )
            return state if result is None else result
