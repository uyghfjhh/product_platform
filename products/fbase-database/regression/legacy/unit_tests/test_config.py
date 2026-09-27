import os
import tempfile
import unittest
from pathlib import Path

import yaml

from framework.config import RegressionConfig, expand_environment
from framework.errors import ConfigError


class ExpandEnvironmentTest(unittest.TestCase):
    def test_expand_value_and_default(self):
        source = {"a": "${SET}", "b": "${MISSING:-default}", "c": ["x-${EMPTY:-y}"]}
        actual = expand_environment(source, {"SET": "value", "EMPTY": ""})
        self.assertEqual(actual, {"a": "value", "b": "default", "c": ["x-y"]})

    def test_missing_without_default_becomes_empty(self):
        self.assertEqual(expand_environment("${MISSING}", {}), "")


class ConfigValidationTest(unittest.TestCase):
    def base_config(self, root):
        return {
            "postgres": {
                "home": "/opt/pg",
                "license_file": "/tmp/license.dat",
            },
            "clusters": {
                "mac": {
                    "plugins": {"fb_license": {"preload": False}, "fbase_mac": {}},
                    "nodes": {
                        "primary": {"host": "127.0.0.1", "port": 15432,
                                    "data_dir": str(Path(root) / "primary")},
                        "standby": {"host": "127.0.0.1", "port": 15433,
                                    "data_dir": str(Path(root) / "standby")},
                    },
                    "groups": {"streaming": {"primary": "primary", "standbys": ["standby"]}},
                }
            },
        }

    def test_valid_config(self):
        with tempfile.TemporaryDirectory() as root:
            config = RegressionConfig(root, self.base_config(root))
            self.assertEqual(sorted(config.clusters), ["mac"])

    def test_unknown_node_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            data = self.base_config(root)
            data["clusters"]["mac"]["groups"]["streaming"]["primary"] = "missing"
            with self.assertRaises(ConfigError):
                RegressionConfig(root, data)

    def test_invalid_plugin_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            data = self.base_config(root)
            data["clusters"]["mac"]["plugins"]["bad-name"] = {}
            with self.assertRaises(ConfigError):
                RegressionConfig(root, data)
