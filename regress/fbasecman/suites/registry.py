"""Composition root for the regression suites exposed to CLI and Web."""

import importlib

from framework.suites import SuiteRegistry


PLUGIN_MODULES = (
    "suites.guc.plugin",
    "suites.high_availability.plugin",
    "suites.ha_commands.plugin",
    "suites.outstanding.plugin",
    "suites.global_cache.plugin",
    "suites.handover.plugin",
    "suites.sql_parse.plugin",
    "suites.rw_toggle.plugin",
    "suites.common.plugin",
    "suites.tmp.plugin",
)


def build_registry():
    registry = SuiteRegistry()
    for module_name in PLUGIN_MODULES:
        registry.register(importlib.import_module(module_name).PLUGIN)
    return registry


_default_registry = None


def get_default_registry():
    global _default_registry
    if _default_registry is None:
        _default_registry = build_registry()
    return _default_registry
