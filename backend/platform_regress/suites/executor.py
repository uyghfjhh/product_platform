"""Platform case host for product-owned runtime and executor bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..engine import Blocked, Cancelled, CaseContext


@dataclass(frozen=True)
class RuntimeBinding:
    """Resolved product implementation for one catalog case.

    ``teardown_before_finish`` selects the legacy teardown ordering: suites
    whose ``__exit__`` only releases resources (locks, process) tear down
    before ``finish`` writes the report; suites whose report itself must be
    written while resources are still held (e.g. handover keeps its suite
    lock through the report write) set it to ``False``.
    """

    spec: object
    runtime_factory: Callable[[CaseContext, object], object]
    executor: Callable[[object], object]
    pass_reason: str
    teardown_before_finish: bool = True
    on_failure: Callable[[object, BaseException], None] | None = None
    finalize: Callable[[CaseContext, object], None] | None = None


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

    def setup(self, context):
        self._binding = self._resolver(context)
        self._runtime = self._binding.runtime_factory(context, self._binding.spec)
        enter = getattr(self._runtime, "__enter__", None)
        if enter is not None:
            enter()

    def run(self, context):
        failure = None
        try:
            self._binding.executor(self._runtime)
        except BaseException as exc:  # noqa: BLE001 - teardown mirrors ``with``
            failure = exc
        if failure is None:
            self._teardown(None)
            self._runtime.finish("PASS", self._binding.pass_reason)
            return True
        business_failure = isinstance(failure, Exception) and not isinstance(
            failure, (Blocked, Cancelled))
        if not business_failure or self._binding.teardown_before_finish:
            self._teardown(failure)
        if business_failure:
            if self._binding.on_failure is not None:
                try:
                    self._binding.on_failure(self._runtime, failure)
                except Exception:  # noqa: BLE001 - diagnostics never mask verdicts
                    pass
            try:
                self._runtime.finish("FAIL", str(failure))
            except Exception:  # noqa: BLE001 - a broken report still stops
                self._stop()
        self._teardown(failure)
        raise failure

    def cleanup(self, context):
        if self._runtime is None:
            return
        try:
            self._teardown(None)
        finally:
            finalize = self._binding.finalize if self._binding else None
            if finalize is not None:
                finalize(context, self._runtime)

    def _teardown(self, failure):
        if self._torn_down or self._runtime is None:
            return
        self._torn_down = True
        exit_method = getattr(self._runtime, "__exit__", None)
        if exit_method is not None:
            if failure is None:
                exit_method(None, None, None)
            else:
                exit_method(type(failure), failure, failure.__traceback__)
            return
        self._stop()

    def _stop(self):
        stop = getattr(self._runtime, "stop", None)
        if stop is not None:
            stop()
