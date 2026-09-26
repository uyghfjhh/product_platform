import json
import tempfile
import unittest
from pathlib import Path

from framework.errors import SafetyError
from framework.state import require_marker, write_marker


class MarkerTest(unittest.TestCase):
    def test_matching_marker(self):
        with tempfile.TemporaryDirectory() as data_dir:
            write_marker(data_dir, "mac", "primary", "env1")
            marker = require_marker(data_dir, "mac", "primary", "env1")
            self.assertEqual(marker["env_id"], "env1")

    def test_missing_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as data_dir:
            with self.assertRaises(SafetyError):
                require_marker(data_dir, "mac", "primary", "env1")

    def test_wrong_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as data_dir:
            write_marker(data_dir, "mac", "primary", "env1")
            with self.assertRaises(SafetyError):
                require_marker(data_dir, "mac", "standby", "env1")
