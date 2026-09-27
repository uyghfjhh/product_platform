"""Shared regression contracts independent of product test frameworks."""

from .catalog import CaseCatalog, CaseDefinition, CaseContractError
from .engine import Blocked, Cancelled, CaseContext, CaseResult, CommandResult, RegressionEngine, SqlResult
from .steps import (SUPPORTED_COMMAND_ASSERTIONS, SUPPORTED_SQL_ASSERTIONS,
                    run_declared_step, run_declared_steps, run_sql_step)
from .runtime import CaseRuntime, CaseRuntimeFailure, EnvironmentRef, RegressionContext
from .sdk import ProductCase, SDK_VERSION

__all__ = [
    "Blocked", "Cancelled", "CaseCatalog", "CaseContext", "CaseContractError",
    "CaseDefinition", "CaseResult", "CommandResult", "RegressionEngine", "SqlResult",
    "run_declared_step", "run_declared_steps", "run_sql_step",
    "SUPPORTED_COMMAND_ASSERTIONS", "SUPPORTED_SQL_ASSERTIONS",
    "CaseRuntime", "CaseRuntimeFailure", "EnvironmentRef", "RegressionContext",
    "ProductCase", "SDK_VERSION",
]
