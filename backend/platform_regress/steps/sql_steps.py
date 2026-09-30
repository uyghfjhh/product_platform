"""SQL-family step runners: direct sql, wait_sql, background pairs."""

from __future__ import annotations

import time
from typing import Any

import psycopg

from ..engine import CaseContext
from .base import (SUPPORTED_SQL_ASSERTIONS, StepExecutionResult, _record_step,
                   evaluate_assertion, format_psql_output, step_user)
from .sql_exec import _db_binary, _sql_execution


def run_sql_step(context: CaseContext, step: dict[str, Any], index: int,
                 node: str) -> None:
    """Execute one SQL step and preserve expected errors as business facts.

    Only declared assertion shapes are accepted. An unsupported shape is an
    executor error, never an implicit PASS or a guessed assertion.
    """
    step = context.expand(step)
    assertion = step.get("assertion") or {}
    kind = assertion.get("type")
    if step.get("type") == "cluster_action":
        if step.get("action") != "reload" or kind != "command_succeeds":
            raise ValueError(f"平台不支持集群动作: {step.get('action')}")
        context.reload(node)
        context.step(f"step-{index}", step["title"], details={"action": "reload"})
        return
    if step.get("type") == "wait_sql":
        deadline = time.monotonic() + float(step.get("timeout", 30))
        interval = float(step.get("interval", 1))
        while True:
            try:
                result = context.sql(node, step["sql"], database=step.get("database") or "postgres",
                                     user=step_user(context, step), password=step.get("password"))
                expected = tuple(tuple(str(cell) for cell in row) for row in assertion.get("rows", []))
                if result.rows == expected:
                    context.step(f"step-{index}", step["title"], details={"poll": "matched"})
                    return
            except psycopg.Error:
                pass
            if time.monotonic() >= deadline:
                context.step(f"step-{index}", step["title"], status="FAIL",
                             details={"timeout": step.get("timeout", 30)})
                raise AssertionError(f"{step['title']}: 轮询超时")
            context.check_cancel()
            time.sleep(interval)
    if step.get("type") != "sql" or kind not in SUPPORTED_SQL_ASSERTIONS:
        raise ValueError(f"平台不支持 SQL 步骤或断言: {kind}")
    key = f"step-{index}"
    title = step["title"]
    query = step["sql"]
    database = step.get("database") or "postgres"
    sql_kwargs = {"database": database}
    if step_user(context, step):
        sql_kwargs["user"] = step_user(context, step)
    if step.get("password"):
        sql_kwargs["password"] = step["password"]
    if kind in {"sql_error", "sql_fails"}:
        try:
            context.sql(node, query, **sql_kwargs)
        except psycopg.Error as exc:
            actual = str(exc)
            state = getattr(exc, "sqlstate", None)
            expected = assertion["message_contains"]
            passed = expected in actual and (kind != "sql_error" or state == assertion["sqlstate"])
            details = {"expected_error": expected, "actual_error": actual, "sqlstate": state}
            context.step(key, title, status="PASS" if passed else "FAIL", details=details)
            if not passed:
                raise AssertionError(f"{title}: SQL 错误与预期不符") from exc
            return
        context.step(key, title, status="FAIL", details={"expected_error": assertion["message_contains"],
                                                       "actual": "SQL 成功"})
        raise AssertionError(f"{title}: SQL 意外成功")

    result = context.sql(node, query, **sql_kwargs)
    if kind == "rows_equal":
        expected_rows = tuple(tuple(None if cell is None else str(cell) for cell in row)
                              for row in assertion["rows"])
        passed = result.rows == expected_rows
        details = {"expected": expected_rows, "actual": result.rows}
    elif kind in {"output_contains_text", "output_contains"}:
        # The structured SQL result contains the values that psql previously
        # printed. Include columns and the command tag for metadata checks.
        display = "\n".join([*result.columns,
                             *("|".join("" if value is None else value for value in row)
                               for row in result.rows), result.command_tag])
        missing = [value for value in assertion["values"] if value not in display]
        passed = not missing
        details = {"missing": missing, "actual": display}
    else:
        passed = True
        details = {"command_tag": result.command_tag}
    context.step(key, title, status="PASS" if passed else "FAIL", details=details)
    if not passed:
        raise AssertionError(f"{title}: SQL 断言失败")


def _run_sql_step(context: CaseContext, step: dict[str, Any], index: int,
                  definition: dict[str, Any] | None) -> None:
    node, execution = _sql_execution(context, step, definition)
    passed, actual, reason = evaluate_assertion(step["assertion"], execution)
    _record_step(context, f"step-{index}", step.get("title") or f"step {index}",
                 passed, actual,
                 format_psql_output(execution.display_output), reason, node)


def _run_wait_sql_step(context: CaseContext, step: dict[str, Any], index: int,
                       definition: dict[str, Any] | None) -> None:
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    deadline = time.time() + float(step.get("timeout", 30))
    last = None
    while time.time() < deadline:
        context.check_cancel()
        node, execution = _sql_execution(context, step, definition)
        passed, actual, reason = evaluate_assertion(step["assertion"], execution)
        last = (node, execution, actual, reason)
        if passed:
            _record_step(context, key, title, True, actual,
                         format_psql_output(execution.display_output), "", node)
            return
        time.sleep(float(step.get("interval", 1)))
    if last is None:
        _record_step(context, key, title, False, "未执行", "<无输出>", "等待超时")
        return
    node, execution, actual, reason = last
    reason = "等待 %ss 超时；%s" % (step.get("timeout", 30), reason)
    _record_step(context, key, title, False, actual,
                 format_psql_output(execution.display_output), reason, node)


def _run_background_sql_step(context: CaseContext, step: dict[str, Any],
                             index: int) -> None:
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    node = context.resolve_node(step.get("node", "primary"))
    endpoint = context.node_endpoint(node)
    name = step["key"]
    running = context.values.setdefault("background_sql", {})
    if name in running:
        raise ValueError("后台 SQL key 已存在: %s" % name)
    script = "%s; SELECT pg_sleep(%s); %s" % (
        step["sql"].rstrip(";"), step["hold_seconds"],
        step["finish_sql"].rstrip(";"))
    argv = [
        _db_binary(context, "psql"), "-X", "-v", "ON_ERROR_STOP=1",
        "-v", "VERBOSITY=verbose", "-P", "pager=off",
        "-h", str(endpoint["host"]), "-p", str(endpoint["port"]),
        "-U", step_user(context, step) or context.environment.get("user") or "postgres",
        "-d", step.get("database") or "postgres", "-c", script,
    ]
    process = context.start_command(argv)
    running[name] = {"process": process, "command": argv, "node": node}

    def cleanup():
        entry = running.pop(name, None)
        if entry and entry["process"].poll() is None:
            entry["process"].terminate()
            context.finish_command(entry["command"], entry["process"], 5)

    context.defer_cleanup(cleanup, priority=300)
    time.sleep(float(step.get("settle_seconds", 1)))
    if process.poll() is not None:
        completed = context.finish_command(argv, process, 1)
        execution = StepExecutionResult(completed.returncode,
                                        output=completed.stdout, command=argv)
    else:
        execution = StepExecutionResult(
            0, output="后台 psql 已启动，pid=%s；事务保持中" % process.pid,
            command=argv)
    passed, actual, reason = evaluate_assertion(step["assertion"], execution)
    _record_step(context, key, title, passed, actual,
                 format_psql_output(execution.output), reason, node)


def _run_wait_background_sql_step(context: CaseContext, step: dict[str, Any],
                                  index: int) -> None:
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    running = context.values.setdefault("background_sql", {})
    entry = running.pop(step["key"], None)
    if not entry:
        raise ValueError("未找到后台 SQL key: %s" % step["key"])
    completed = context.finish_command(
        entry["command"], entry["process"], step.get("timeout", 30))
    execution = StepExecutionResult(completed.returncode,
                                    output=completed.stdout,
                                    command=entry["command"])
    passed, actual, reason = evaluate_assertion(step["assertion"], execution)
    _record_step(context, key, title, passed, actual,
                 format_psql_output(execution.output), reason, entry["node"])
