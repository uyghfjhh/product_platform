"""Test-suite contracts, registry and discovery abstractions."""

from .contracts import CaseResult, CaseSpec, SuitePlugin
from .registry import SuiteRegistry

__all__ = [
    "CaseResult", "CaseSpec", "SuitePlugin", "SuiteRegistry",
]
