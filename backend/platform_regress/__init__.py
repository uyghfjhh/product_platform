"""Shared regression contracts independent of product test frameworks."""

from .catalog import CaseCatalog, CaseDefinition, CaseContractError
from .engine import Blocked, Cancelled, CaseContext, CaseResult, CommandResult, RegressionEngine, SqlResult
from .steps import run_sql_step
from .runtime import CaseRuntime, CaseRuntimeFailure, EnvironmentRef, RegressionContext

__all__ = [
    "Blocked", "Cancelled", "CaseCatalog", "CaseContext", "CaseContractError",
    "CaseDefinition", "CaseResult", "CommandResult", "RegressionEngine", "SqlResult",
    "run_sql_step",
    "CaseRuntime", "CaseRuntimeFailure", "EnvironmentRef", "RegressionContext",
]
