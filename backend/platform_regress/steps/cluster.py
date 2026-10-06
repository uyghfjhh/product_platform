"""Cluster/node action and system-clock steps driven through pg_ctl."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..engine import CaseContext
from .base import (StepExecutionResult, _record_step, evaluate_assertion,
                   format_psql_output)
from .sql_exec import _db_binary


def _pg_ctl_argv(context: CaseContext, endpoint: dict[str, Any],
                 action: str) -> list[str]:
    """Build the legacy pg_ctl argv for one managed node action."""
    data_dir = endpoint.get("data_dir")
    if not data_dir:
        raise ValueError("节点缺少 data_dir，无法执行 pg_ctl")
    argv = [_db_binary(context, "pg_ctl"), "-D", str(data_dir)]
    if action == "start":
        argv += ["-l", str(Path(str(data_dir)) / "startup.log"), "-w", "start"]
    elif action == "stop":
        argv += ["-w", "-m", "fast", "stop"]
    elif action == "stop_immediate":
        argv += ["-w", "-m", "immediate", "stop"]
    elif action == "restart":
        argv += ["-l", str(Path(str(data_dir)) / "startup.log"), "-w", "restart"]
    elif action == "reload":
        argv += ["reload"]
    elif action == "status":
        argv += ["status"]
    else:
        raise ValueError("unknown pg_ctl action: %s" % action)
    return argv


def _run_pg_ctl(context: CaseContext, node: str, action: str,
                check: bool = True):
    """Run pg_ctl for one managed node like the legacy environment manager."""
    endpoint = context.node_endpoint(node)
    argv = _pg_ctl_argv(context, endpoint, action)
    try:
        result = context.command(argv, merge_stderr=True)
        returncode, output = result.returncode, result.stdout
    except TimeoutError as exc:
        returncode = 124
        output = getattr(exc, "partial_stdout", "") or ""
    if check and returncode != 0:
        raise RuntimeError("命令执行失败(%s): %s\n%s" % (
            returncode, " ".join(argv), output.rstrip()))
    return returncode, output


def _managed_node_order(context: CaseContext) -> list[str]:
    """Physical node keys in environment order for cluster-wide actions."""
    order = context.environment.get("node_order")
    if order:
        return [name for name in order if name in (context.environment.get("nodes") or {})]
    return list(context.environment.get("nodes") or {})


def _run_cluster_action(context: CaseContext, step: dict[str, Any],
                        index: int) -> None:
    """Apply start/stop/restart/reload across every managed node in order."""
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    action = step.get("action")
    if action not in ("reload", "restart", "start", "stop"):
        raise ValueError("不支持的 cluster action: %s" % action)
    order = _managed_node_order(context)
    if action == "stop":
        order = list(reversed(order))
    if action == "reload":
        if not any(
                _run_pg_ctl(context, node, "status", check=False)[0] == 0
                for node in order):
            raise ValueError(
                "cluster %s 未运行，不能 reload"
                % context.environment.get("cluster") or context.environment.get("cluster_name"))
        for node in order:
            _run_pg_ctl(context, node, "reload")
    elif action == "start":
        for node in order:
            if _run_pg_ctl(context, node, "status", check=False)[0] != 0:
                _run_pg_ctl(context, node, "start")
    elif action == "stop":
        for node in order:
            if _run_pg_ctl(context, node, "status", check=False)[0] == 0:
                _run_pg_ctl(context, node, "stop")
    elif action == "restart":
        for node in order:
            status = _run_pg_ctl(context, node, "status", check=False)[0]
            _run_pg_ctl(context, node, "restart" if status == 0 else "start")
    execution = StepExecutionResult(0, output="%s complete" % action)
    passed, actual, reason = evaluate_assertion(step["assertion"], execution)
    _record_step(
        context, key, title, passed, actual, execution.output, reason,
        expected=step.get("expected"), assertion=step["assertion"], execution=execution,
    )


def _run_node_action(context: CaseContext, step: dict[str, Any],
                     index: int) -> None:
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    action = step.get("action")
    if action not in ("start", "stop", "stop_immediate", "restart"):
        raise ValueError("不支持的 node action: %s" % action)
    node = context.resolve_node(step["node"])
    _run_pg_ctl(context, node, action)
    execution = StepExecutionResult(0, output="%s %s complete" % (action, node))
    passed, actual, reason = evaluate_assertion(step["assertion"], execution)
    _record_step(
        context, key, title, passed, actual, execution.output, reason, node,
        expected=step.get("expected"), assertion=step["assertion"], execution=execution,
    )


def _run_system_time_shift(context: CaseContext, step: dict[str, Any],
                           index: int) -> None:
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    state = context.values.get("system_clock")
    if not state:
        raise ValueError("system_time_shift 需要 system_clock fixture")
    seconds = int(step["seconds"])
    if seconds <= 0:
        raise ValueError("system_time_shift seconds 必须大于 0")
    target = int(state["epoch"]) + seconds
    ntp = context.command(["sudo", "-n", "timedatectl", "set-ntp", "false"],
                          merge_stderr=True)
    if ntp.returncode != 0:
        raise RuntimeError("命令执行失败(%s): %s\n%s" % (
            ntp.returncode, "sudo -n timedatectl set-ntp false",
            ntp.stdout.rstrip()))
    try:
        process = context.command(
            ["sudo", "-n", "date", "-s", "@%s" % target], merge_stderr=True)
        returncode, output = process.returncode, process.stdout
    except TimeoutError as exc:
        returncode = 124
        output = getattr(exc, "partial_stdout", "") or ""
    if returncode == 0:
        state["changed"] = True
    argv = ["sudo", "-n", "date", "-s", "@%s" % target]
    execution = StepExecutionResult(
        returncode,
        output="原始 epoch=%s，目标 epoch=%s\n%s" % (state["epoch"], target, output),
        command=argv)
    passed, actual, reason = evaluate_assertion(step["assertion"], execution)
    _record_step(
        context, key, title, passed, actual,
        format_psql_output(execution.output), reason,
        expected=step.get("expected"), assertion=step["assertion"], execution=execution,
    )
