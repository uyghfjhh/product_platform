import yaml
import time

from framework.configuration import RegressionConfig
from framework.persistence import atomic_write_text, blocking_file_lock

yaml.SafeDumper.ignore_aliases = lambda *args: True
STATE_SCHEMA_VERSION = 1


class EnvState:
    def __init__(self, status="not_created", completed_steps=None,
                 failed_step=None, nodes=None):
        self.status = status
        self.completed_steps = completed_steps or []
        self.failed_step = failed_step
        self.nodes = nodes or {}


class StateStore:
    def __init__(self, env: RegressionConfig):
        self.env = env
        self.env.env_output_dir.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> EnvState:
        if not self.env.env_state_file.exists():
            return EnvState()
        data = yaml.safe_load(self.env.env_state_file.read_text(encoding="utf-8")) or {}
        version = data.get("schema_version", 0)
        if version not in (0, STATE_SCHEMA_VERSION):
            raise RuntimeError("unsupported environment state schema: %s" % version)
        return EnvState(
            status=data.get("status", "not_created"),
            completed_steps=list(data.get("completed_steps", [])),
            failed_step=data.get("failed_step"),
            nodes=dict(data.get("nodes", {})),
        )

    def save_state(self, state):
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "updated_at": int(time.time()),
            "status": state.status,
            "completed_steps": state.completed_steps,
            "failed_step": state.failed_step,
            "nodes": state.nodes,
        }
        with blocking_file_lock(self.env.env_state_file.with_suffix(".lock")):
            atomic_write_text(
                self.env.env_state_file,
                yaml.safe_dump(payload, default_flow_style=False),
            )

    def save_context(self, context):
        with blocking_file_lock(self.env.test_context_file.with_suffix(".lock")):
            atomic_write_text(
                self.env.test_context_file,
                yaml.safe_dump(context, default_flow_style=False),
            )
