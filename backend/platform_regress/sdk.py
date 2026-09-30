"""Versioned public contracts for product regression packages.

The exports below and the documented public subpackages in SDK.md are the
supported product API. Execution implementation modules are platform-private.
"""

from .catalog import CaseCatalog, CaseContractError, CaseDefinition
from .clients.pgbench import PgbenchRequest
from .contracts import (
    Blocked,
    Cancelled,
    CaseFailure,
    CaseResult,
    CleanupCase,
    CleanupResult,
    CleanupStatus,
    CommandResult,
    RegressionCase,
    SetupCase,
    SqlResult,
    Verdict,
)
from .engine import CaseContext, RegressionEngine
from .environment.context import CaseEnvironment, NodeEndpoint, resolve_selector
from .environment.disposable import DisposablePostgresResources
from .environment.guards import FileRestoreGuard
from .environment.postgresql_fixtures import PostgresFixtures
from .environment.postgresql_lifecycle import PostgresLifecycle
from .evidence.artifacts import ArtifactRepository, CleanupReport
from .evidence.server_logs import ServerLogCollector
from .execution.longrun import WorkloadGroup
from .execution.remote import RemoteExecutor, RemoteTarget
from .persistence.state import JsonStateStore
from .requirements import (
    COMMON_REQUIREMENTS,
    RequirementRegistry,
    evaluate_requirements,
)
from .runtime import ReportRuntime, ReportSpec
from .sql import SqlSession
from .steps import (
    SUPPORTED_COMMAND_ASSERTIONS,
    SUPPORTED_SQL_ASSERTIONS,
    run_declared_step,
    run_declared_steps,
    run_sql_step,
)
from .suites.executor import CaseRuntimeProtocol, RuntimeBinding, RuntimeExecutorCase

SDK_VERSION = "2"

__all__ = [
    "COMMON_REQUIREMENTS",
    "SDK_VERSION",
    "SUPPORTED_COMMAND_ASSERTIONS",
    "SUPPORTED_SQL_ASSERTIONS",
    "ArtifactRepository",
    "Blocked",
    "Cancelled",
    "CaseCatalog",
    "CaseContext",
    "CaseContractError",
    "CaseDefinition",
    "CaseEnvironment",
    "CaseFailure",
    "CaseResult",
    "CaseRuntimeProtocol",
    "CleanupCase",
    "CleanupReport",
    "CleanupResult",
    "CleanupStatus",
    "CommandResult",
    "DisposablePostgresResources",
    "FileRestoreGuard",
    "JsonStateStore",
    "NodeEndpoint",
    "PgbenchRequest",
    "PostgresFixtures",
    "PostgresLifecycle",
    "RegressionCase",
    "RegressionEngine",
    "RemoteExecutor",
    "RemoteTarget",
    "ReportRuntime",
    "ReportSpec",
    "RequirementRegistry",
    "RuntimeBinding",
    "RuntimeExecutorCase",
    "ServerLogCollector",
    "SetupCase",
    "SqlResult",
    "SqlSession",
    "Verdict",
    "WorkloadGroup",
    "evaluate_requirements",
    "resolve_selector",
    "run_declared_step",
    "run_declared_steps",
    "run_sql_step",
]
