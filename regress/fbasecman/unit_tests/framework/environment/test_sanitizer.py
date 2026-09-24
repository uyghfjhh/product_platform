import unittest
from pathlib import Path
from unittest.mock import patch

from framework.environment.sanitizer import preflight_health_check


class SanitizerTest(unittest.TestCase):
    def test_heal_error_and_unavailable_health_are_failures(self):
        config = {"database": {"ports": {"mmr1_standbys": [1, 2, 3]}}}
        env = type("Env", (), {"config": config})()
        with patch("framework.environment.sanitizer.load_regression_config", return_value=env), \
             patch("framework.environment.sanitizer.create_environment_provider") as create:
            create.return_value.heal.side_effect = RuntimeError("cluster cannot start")
            self.assertEqual(
                {"status": "FAILED", "reason": "cluster cannot start"},
                preflight_health_check(Path("/repo")),
            )
            create.return_value.heal.side_effect = None
            create.return_value.heal.return_value = {
                "health": {"mmr_streaming": "UNAVAILABLE: connection refused"}
            }
            result = preflight_health_check(Path("/repo"))
            self.assertEqual("FAILED", result["status"])
            self.assertIn("mmr_streaming", result["reason"])

            create.return_value.heal.return_value = {"health": {
                "mmr_non_active": "0", "testdb_node1": "ACTIVE",
                "testdb_node2": "JOIN_START", "mmr_streaming": "3",
            }}
            self.assertEqual("HEALED", preflight_health_check(Path("/repo"))["status"])
