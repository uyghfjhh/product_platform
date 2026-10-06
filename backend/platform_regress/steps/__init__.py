"""Product-neutral assertions for exported declarative SQL steps.

Re-exports every name the old flat ``steps`` module published (including the
``_private`` helpers consumed by ``environment/postgresql_lifecycle`` and
product fixtures) so existing ``platform_regress.steps`` imports keep working.
"""

from __future__ import annotations

from typing import Any, Callable

from ..contracts import Cancelled
from ..engine import CaseContext
from .base import (DEFAULT_COMMAND_TIMEOUT, SUPPORTED_COMMAND_ASSERTIONS,
                   SUPPORTED_SQL_ASSERTIONS, StepExecutionResult,
                   _record_step, _shell_command, evaluate_assertion,
                   evaluate_command_assertion, format_psql_output,
                   meaningful_lines, step_user)
from .cluster import (_managed_node_order, _pg_ctl_argv, _run_cluster_action,
                      _run_node_action, _run_pg_ctl, _run_system_time_shift)
from .command import run_command_step
from .sql_exec import _db_binary, _sql_execution, execute_psql
from .sql_steps import (run_sql_step, _run_background_sql_step, _run_sql_step,
                        _run_wait_background_sql_step, _run_wait_sql_step)

__all__ = [
    "DEFAULT_COMMAND_TIMEOUT", "SUPPORTED_COMMAND_ASSERTIONS",
    "SUPPORTED_SQL_ASSERTIONS", "StepExecutionResult",
    "evaluate_assertion", "evaluate_command_assertion", "execute_psql",
    "format_psql_output", "meaningful_lines", "run_command_step",
    "run_declared_step", "run_declared_steps", "run_sql_step", "step_user",
    # private names kept importable for environment/ and product fixtures
    "_db_binary", "_managed_node_order", "_pg_ctl_argv", "_record_step",
    "_run_pg_ctl", "_shell_command", "_sql_execution",
]


def run_declared_step(context: CaseContext, step: dict[str, Any], index: int,
                      before_command: Callable[[list[str]], None] | None = None,
                      definition: dict[str, Any] | None = None) -> None:
    """Expand and dispatch one exported step to its platform runner."""
    step = context.expand(step)
    kind = step.get("type", "sql")
    if kind == "command":
        run_command_step(context, step, index, before_command)
        return
    if kind == "sql":
        _run_sql_step(context, step, index, definition)
        return
    if kind == "wait_sql":
        _run_wait_sql_step(context, step, index, definition)
        return
    if kind == "background_sql":
        _run_background_sql_step(context, step, index)
        return
    if kind == "wait_background_sql":
        _run_wait_background_sql_step(context, step, index)
        return
    if kind == "cluster_action":
        _run_cluster_action(context, step, index)
        return
    if kind == "node_action":
        _run_node_action(context, step, index)
        return
    if kind == "system_time_shift":
        _run_system_time_shift(context, step, index)
        return
    raise ValueError("平台暂不支持步骤类型: %s" % kind)


def run_declared_steps(context: CaseContext, steps: list[dict[str, Any]],
                       before_command: Callable[[list[str]], None] | None = None,
                       definition: dict[str, Any] | None = None) -> None:
    """Run exported steps with the legacy halt/continue contract.

    A failed step halts the case unless it declares ``continue_on_failure``;
    steps skipped by a halt are recorded BLOCKED.  Executor errors inside a
    step — including unresolvable node selectors — are recorded as step
    failures like the legacy runner; only cancellation propagates.
    """
    failures = []
    halted = False
    for index, step in enumerate(steps, 1):
        title = step.get("title") or f"step {index}"
        if halted:
            context.step(f"step-{index}", title, status="BLOCKED", details={
                "reason": "前序步骤失败，当前步骤不再具备有效前置条件",
                "expected": step.get("expected"),
                "assertion": step.get("assertion"),
                "intent": step.get("intent"),
                "actual": "未执行：前序步骤失败",
            })
            continue
        try:
            context.values['_step_intent']=step.get('intent')
            run_declared_step(context, step, index, before_command,
                              definition=definition)
        except AssertionError as exc:
            failures.append("第 %s 步 %s: %s" % (index, title, exc))
            halted = not step.get("continue_on_failure", False)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - executor errors are step failures
            context.step(f"step-{index}", title, status="FAIL",
                         details={"executor_error": str(exc), "expected": step.get("expected"),
                                  "assertion": step.get("assertion"), "intent": step.get("intent"),
                                  "actual": "执行器异常：" + str(exc)})
            failures.append("第 %s 步 %s: 执行器异常: %s" % (index, title, exc))
            halted = not step.get("continue_on_failure", False)
        finally:
            context.values.pop('_step_intent',None)
    if failures:
        raise AssertionError("; ".join(failures))
