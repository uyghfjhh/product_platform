import tempfile
import unittest
from pathlib import Path

from framework.configuration.reload import (
    ReloadConfigError,
    config_lines_by_keys,
    has_config_value,
    install_reload_config,
)


class ReloadConfigTest(unittest.TestCase):
    def test_matches_exact_config_keys(self):
        conf = "enable_guc_sync yes\nenable_guc_sync_extra no\n  server_lifetime 10\n"

        self.assertEqual(["enable_guc_sync yes"], config_lines_by_keys(conf, ["enable_guc_sync"]))
        self.assertTrue(has_config_value(conf, "server_lifetime", 10))
        self.assertFalse(has_config_value(conf, "enable_guc_sync", "no"))

    def test_atomically_installs_validated_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "next.conf"
            target = root / "live.conf"
            source.write_text("enable_guc_sync no\n", encoding="utf-8")
            target.write_text("enable_guc_sync yes\n", encoding="utf-8")

            installed = install_reload_config(
                source, target, [("enable_guc_sync", "no")]
            )

            self.assertEqual("enable_guc_sync no\n", installed)
            self.assertFalse((root / "live.conf.reload.tmp").exists())

    def test_rejects_source_before_replacing_live_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "next.conf"
            target = root / "live.conf"
            source.write_text("enable_guc_sync yes\n", encoding="utf-8")
            target.write_text("enable_guc_sync no\n", encoding="utf-8")

            with self.assertRaisesRegex(ReloadConfigError, "source conf check failed"):
                install_reload_config(source, target, [("enable_guc_sync", "no")])

            self.assertEqual("enable_guc_sync no\n", target.read_text(encoding="utf-8"))
