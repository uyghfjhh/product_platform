import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from env.state import EnvState, StateStore as EnvStateStore
from framework.configuration.loader import RegressionConfig
from suites.stable.state import StateStore as StableStateStore


class StatePersistenceTest(unittest.TestCase):
    def test_environment_state_is_versioned_and_written_atomically(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            environment = RegressionConfig(root, {
                "framework": {"output_dir": "output"},
            })
            store = EnvStateStore(environment)
            store.save_state(EnvState(status="ready", completed_steps=["health_check"]))
            payload = yaml.safe_load(environment.env_state_file.read_text(encoding="utf-8"))
            temporary_files = list(environment.env_output_dir.glob("*.tmp"))

        self.assertEqual(1, payload["schema_version"])
        self.assertEqual("ready", payload["status"])
        self.assertFalse(temporary_files)

    def test_stable_state_serializes_short_updates_with_monotonic_revisions(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StableStateStore(path)
            store.save({"status": "running"})
            first = json.loads(path.read_text(encoding="utf-8"))["revision"]
            state = store.update(lambda value: value.update({"status": "finalizing"}))
            second = json.loads(path.read_text(encoding="utf-8"))["revision"]

        self.assertEqual("finalizing", state["status"])
        self.assertEqual(first + 1, second)

    def test_stable_state_rejects_future_schema(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"schema_version": 99}\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unsupported stable state schema"):
                StableStateStore(path).load()
