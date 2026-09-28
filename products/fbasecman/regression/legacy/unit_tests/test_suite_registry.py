import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]

from platform_regress.suites import (
    CaseResult, CaseSpec, SuitePlugin, SuiteRegistry,
)
from suites.registry import get_default_registry


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

    def test_default_entries_are_explicit_suite_plugins(self):
        reg = get_default_registry()
        for plugin in reg.all_suites():
            self.assertIsInstance(plugin, SuitePlugin)
            cases = plugin.get_cases()
            self.assertTrue(cases)
            self.assertTrue(all(isinstance(case, CaseSpec) for case in cases))
            self.assertEqual(
                ["%s.%s" % (plugin.id, case.name) for case in cases],
                plugin.get_targets(),
            )

    def test_plugin_contract_and_normalized_result(self):
        case = CaseSpec("sample", "basic", "basic behavior", "run_basic")
        plugin = SuitePlugin(
            suite_id="sample",
            title="Sample",
            description="Contract fixture",
            case_loader=lambda: [case],
            runner=lambda root, target=None: True,
        )
        self.assertEqual(["sample.basic"], plugin.get_targets())
        self.assertEqual(0, plugin.run(ROOT_DIR, target="basic"))

        result = CaseResult("sample.basic", "PASS", artifacts={"report": "x"})
        self.assertEqual("PASS", result.status)
        self.assertEqual({"report": "x"}, result.artifacts)

    def test_registry_rejects_implicit_registration(self):
        registry = SuiteRegistry()
        with self.assertRaises(TypeError):
            registry.register(object())

    def test_runner_exit_codes_are_not_misreported_as_success(self):
        case = CaseSpec("sample", "basic", "basic behavior", "run_basic")
        for value, expected in ((True, 0), (False, 1), (0, 0), (2, 2),
                                (None, 1), (CaseResult(case.target, "FAIL"), 1)):
            plugin = SuitePlugin("sample", "Sample", "", lambda: [case],
                                 lambda root, target=None: value)
            self.assertEqual(expected, plugin.run(ROOT_DIR), repr(value))

    def test_preflight_failure_stops_runner_and_invalid_target_skips_preflight(self):
        case = CaseSpec("sample", "basic", "basic behavior", "run_basic")
        calls = []
        plugin = SuitePlugin("sample", "Sample", "", lambda: [case],
                             lambda root, target=None: calls.append(target) or True)
        registry = SuiteRegistry()
        registry.register(plugin)
        with patch("cmanconf.preflight_health_check",
                   return_value={"status": "FAILED", "reason": "database unavailable"}) as check:
            self.assertEqual(2, registry.run_target(ROOT_DIR, "sample.missing"))
            check.assert_not_called()
            self.assertEqual(3, registry.run_target(ROOT_DIR, "sample.basic"))
            self.assertEqual([], calls)
            self.assertEqual(0, registry.run_target(ROOT_DIR, "sample.basic", preflight="warn"))
        self.assertEqual(["basic"], calls)
