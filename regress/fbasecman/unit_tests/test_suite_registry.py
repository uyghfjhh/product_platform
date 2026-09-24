import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

from framework.suites import get_default_registry, SuiteDefinition, SuiteRegistry


class SuiteRegistryTest(unittest.TestCase):
    def test_default_registry_has_all_suites(self):
        reg = get_default_registry()
        expected = [
            "guc", "high_availability", "ha_commands", "outstanding",
            "global_cache", "handover", "sql_parse", "rw_toggle", "common", "tmp"
        ]
        self.assertEqual(expected, reg.suite_ids())

    def test_web_definitions_format(self):
        reg = get_default_registry()
        web_defs = reg.to_web_definitions()
        self.assertEqual(10, len(web_defs))
        for d in web_defs:
            self.assertIn("id", d)
            self.assertIn("title", d)
            self.assertIn("description", d)
            self.assertIn("items", d)
            self.assertIn("prefix", d)
            self.assertGreater(len(d["items"]), 0)

    def test_selected_targets_exact_and_suite(self):
        reg = get_default_registry()
        # Full suite
        tmp_targets = reg.selected_targets("tmp")
        self.assertEqual(["tmp.reload_disable_monitor_route_loss"], tmp_targets)

        # Specific target
        exact = reg.selected_targets("tmp.reload_disable_monitor_route_loss")
        self.assertEqual(["tmp.reload_disable_monitor_route_loss"], exact)

        # Invalid target
        invalid = reg.selected_targets("tmp.non_existent")
        self.assertEqual([], invalid)
