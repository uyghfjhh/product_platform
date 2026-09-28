"""Public SDK for product regression packages.

Product code should import regression contracts from this module.  The module
is deliberately a small facade: execution, evidence, cancellation and SQL
transport remain owned by :mod:`platform_regress`, while a product only
implements ``RegressionCase.run`` and its business assertions.
"""

from __future__ import annotations

from typing import Protocol

from .engine import (
    Blocked,
    Cancelled,
    CaseContext,
    CaseFailure,
    CaseResult,
    CommandResult,
    RegressionCase,
    RegressionEngine,
    SqlResult,
)
from .runtime import (
    CaseRuntime,
    CaseRuntimeFailure,
    EnvironmentRef,
    RegressionContext,
)

SDK_VERSION = "1"


class ProductCase(Protocol):
    """Minimal contract implemented by every product regression case."""

    def run(self, context: CaseContext) -> bool | None:
        ...


__all__ = [
    "Blocked",
    "Cancelled",
    "CaseContext",
    "CaseFailure",
    "CaseResult",
    "CommandResult",
    "EnvironmentRef",
    "ProductCase",
    "RegressionCase",
    "RegressionContext",
    "RegressionEngine",
    "CaseRuntime",
    "CaseRuntimeFailure",
    "SDK_VERSION",
    "SqlResult",
]
