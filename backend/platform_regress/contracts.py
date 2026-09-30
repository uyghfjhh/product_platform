"""Stable regression results, failures and case protocols."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .engine import CaseContext


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


class CleanupStatus(StrEnum):
    PASS = "PASS"
    ERROR = "ERROR"


class Blocked(RuntimeError):
    """A required environment condition prevented business verification."""


class Cancelled(RuntimeError):
    """The execution received a cooperative cancellation request."""


class CaseFailure(Exception):
    """A business check failed; maps to the FAIL verdict.

    Product runtime adapters raise domain failures derived from this base so
    the engine can distinguish a genuine test failure from an executor or
    infrastructure defect (which remains ERROR).
    """


class RegressionCase(Protocol):
    def run(self, context: "CaseContext") -> bool | None: ...


@runtime_checkable
class SetupCase(Protocol):
    def setup(self, context: CaseContext) -> None: ...


@runtime_checkable
class CleanupCase(Protocol):
    def cleanup(self, context: CaseContext) -> None: ...


@dataclass(frozen=True)
class CleanupResult:
    status: CleanupStatus
    reason: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "status", CleanupStatus(self.status))


@dataclass(frozen=True)
class SqlResult:
    rows: tuple[tuple[Any, ...], ...]
    columns: tuple[str, ...]
    command_tag: str


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass(frozen=True)
class CaseResult:
    schema_version: str
    execution_id: str
    operation_id: str | None
    target: str
    verdict: Verdict
    business_verdict: Verdict
    reason: str | None
    cleanup: CleanupResult
    duration_seconds: float
    evidence: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "verdict", Verdict(self.verdict))
        object.__setattr__(self, "business_verdict", Verdict(self.business_verdict))

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence"] = list(self.evidence)
        return result
