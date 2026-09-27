"""Product-neutral suite contracts, registry, and runner."""

from .contracts import CaseResult, CaseSpec, SuitePlugin, SuiteRunResult
from .failed import (
    case_status, failed_targets, last_failed_path, read_last_failed,
    rerun_failed, write_last_failed,
)
from .legacy import LegacyCaseBinding, LegacySuiteCase
from .registry import SuiteRegistry, set_default_preflight_check, set_default_quiet_env_var

__all__ = [
    "CaseResult", "CaseSpec", "LegacyCaseBinding", "LegacySuiteCase",
    "SuitePlugin", "SuiteRegistry",
    "SuiteRunResult", "case_status", "failed_targets", "last_failed_path",
    "read_last_failed", "rerun_failed", "set_default_preflight_check",
    "set_default_quiet_env_var", "write_last_failed",
]
