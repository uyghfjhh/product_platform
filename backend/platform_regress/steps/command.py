"""Exported ``command`` step: argv over local or ssh transport."""

from __future__ import annotations

import os
from typing import Any, Callable

from ..engine import CaseContext
from .base import (DEFAULT_COMMAND_TIMEOUT, StepExecutionResult, _record_step,
                   _shell_command, evaluate_assertion, format_psql_output)


def run_command_step(context: CaseContext, step: dict[str, Any], index: int,
                     before_command: Callable[[list[str]], None] | None = None) -> None:
    """Execute one exported command step through the node's transport.

    Local nodes run the argv directly (or via ``sh -lc`` when cwd/env are
    declared); remote nodes run through the system ssh client — the same
    shape the legacy executor used, including merged stderr and the rc=124
    timeout convention so ``command_fails`` assertions keep their meaning.
    """
    assertion = step.get("assertion") or {}
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    node = context.resolve_node(step.get("node") or "primary")
    endpoint = context.node_endpoint(node)
    argv = [str(item) for item in step.get("argv") or []]
    if not argv:
        raise ValueError("command 步骤缺少 argv")
    if before_command is not None:
        before_command(argv)
    timeout = step.get("timeout")
    timeout_seconds = float(timeout) if timeout is not None else DEFAULT_COMMAND_TIMEOUT
    host = endpoint.get("host")
    cwd, env = step.get("cwd"), step.get("env")
    if context.is_local(host):
        run_argv = (["sh", "-lc", _shell_command(argv, cwd, env)]
                    if (cwd or env) else argv)
    else:
        cluster = context.environment.get("cluster")
        transport = context.environment.get("transport") or {}
        if isinstance(cluster, dict):
            transport = cluster.get("transport") or transport
        ssh_user = (transport.get("ssh_user") or context.environment.get("ssh_user") or
                    os.environ.get("USER") or "postgres")
        ssh_port = transport.get("ssh_port") or context.environment.get("ssh_port") or 22
        run_argv = ["ssh", "-p", str(ssh_port), "%s@%s" % (ssh_user, host),
                    _shell_command(argv, cwd, env)]
    try:
        result = context.command(run_argv, timeout_seconds=timeout_seconds,
                                 input_text=step.get("input"), merge_stderr=True)
        returncode, output = result.returncode, result.stdout
    except TimeoutError as exc:
        returncode = 124
        output = ((getattr(exc, "partial_stdout", "") or "") +
                  f"\n命令执行超时（{timeout_seconds}s）")
    execution = StepExecutionResult(returncode, output=output, command=argv)
    passed, actual, reason = evaluate_assertion(assertion, execution)
    _record_step(context, key, title, passed, actual,
                 format_psql_output(execution.output), reason, node)
