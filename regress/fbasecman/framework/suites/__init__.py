"""Test suite registry and discovery abstractions."""

from .registry import SuiteDefinition, SuiteRegistry, get_default_registry

__all__ = ["SuiteDefinition", "SuiteRegistry", "get_default_registry"]
