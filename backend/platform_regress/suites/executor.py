"""Platform case host for product-owned runtime and executor bindings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Generic, Protocol, TypeVar

from ..contracts import Blocked, Cancelled
from ..engine import CaseContext


class CaseRuntimeProtocol(Protocol):
    """Required runtime lifecycle, implemented by each product runtime."""

    def __enter__(self) -> object: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...
    def finish(self, status: str, reason: str | None = None) -> None: ...
    def stop(self) -> None: ...


SpecT = TypeVar("SpecT")
RuntimeT = TypeVar("RuntimeT", bound=CaseRuntimeProtocol)


@dataclass(frozen=True)
class RuntimeBinding(Generic[SpecT, RuntimeT]):
    """Resolved product implementation for one catalog case.

    ``teardown_before_finish`` selects the legacy teardown ordering: suites
    whose ``__exit__`` only releases resources (locks, process) tear down
    before ``finish`` writes the report; suites whose report itself must be
    written while resources are still held (e.g. handover keeps its suite
    lock through the report write) set it to ``False``.
    """

    spec: SpecT
    runtime_factory: Callable[[CaseContext, SpecT], RuntimeT]
    execute: Callable[[CaseContext, RuntimeT], object]
    pass_reason: str
    teardown_before_finish: bool = True
    on_failure: Callable[[RuntimeT, BaseException], None] | None = None
    finalize: Callable[[CaseContext, RuntimeT], None] | None = None


class RuntimeExecutorCase:
    """Run one product executor through a platform-owned lifecycle.

    Products resolve the case spec and construct their runtime from the current
    :class:`CaseContext`; the platform owns setup/run/finish/cleanup ordering and
    verdict propagation. Product runtimes keep only product configuration,
    fixtures and assertions.
    """

    def __init__(self, target, resolver, *, summary="", default_enabled=True):
        self.target = target
        self.summary = summary
        self.default_enabled = default_enabled
        self._resolver = resolver
        self._runtime = None
        self._binding = None
        self._torn_down = False
        self._active = False
        self._teardown_error = None

    def setup(self, context):
        if self._active:
            raise RuntimeError("runtime case is already active")
        self._active = True
        self._runtime = None
        self._binding = None
        self._torn_down = False
        self._teardown_error = None
        self._binding = self._resolver(context)
        self._runtime = self._binding.runtime_factory(context, self._binding.spec)
        self._runtime.__enter__()

    def run(self, context):
        failure = None
        try:
            self._binding.execute(context, self._runtime)
        except BaseException as exc:  # noqa: BLE001 - teardown mirrors ``with``
            failure = exc
        if failure is None:
            if self._binding.teardown_before_finish:
                self._teardown_safely(None)
            self._runtime.finish("PASS", self._binding.pass_reason)
            self._teardown_safely(None)
            return True
        business_failure = isinstance(failure, Exception) and not isinstance(
            failure, (Blocked, Cancelled)
        )
        if not business_failure or self._binding.teardown_before_finish:
            self._teardown_safely(failure)
        if business_failure:
            if self._binding.on_failure is not None:
                try:
                    self._binding.on_failure(self._runtime, failure)
                except Exception as exc:  # noqa: BLE001 - diagnostics never mask verdicts
                    context.emit("runtime.diagnostic_failed", {"reason": str(exc)})
            try:
                self._runtime.finish("FAIL", str(failure))
            except Exception:  # noqa: BLE001 - a broken report still stops
                self._stop()
        self._teardown_safely(failure)
        raise failure

    def cleanup(self, context):
        try:
            if self._runtime is not None:
                try:
                    self._teardown_safely(None)
                    if self._teardown_error is not None:
                        raise self._teardown_error
                finally:
                    if self._binding.finalize is not None:
                        self._binding.finalize(context, self._runtime)
        finally:
            self._active = False

    def _teardown_safely(self, failure):
        # Cleanup failures belong to cleanup, never replace a business failure.
        try:
            self._teardown(failure)
        except Exception:
            pass  # retained in _teardown_error and reported by cleanup()

    def _teardown(self, failure):
        if self._torn_down or self._runtime is None:
            return
        self._torn_down = True
        try:
            if failure is None:
                self._runtime.__exit__(None, None, None)
            else:
                self._runtime.__exit__(type(failure), failure, failure.__traceback__)
        except Exception as exc:
            self._teardown_error = exc
            raise

    def _stop(self):
        self._runtime.stop()
