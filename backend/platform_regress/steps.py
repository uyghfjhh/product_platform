"""Product-neutral assertions for exported declarative SQL steps."""

from __future__ import annotations

from typing import Any

import psycopg

from .engine import CaseContext


SUPPORTED_SQL_ASSERTIONS = frozenset({
    "rows_equal", "output_contains_text", "sql_error", "sql_fails", "command_succeeds",
})


def run_sql_step(context: CaseContext, step: dict[str, Any], index: int,
                 node: str) -> None:
    """Execute one SQL step and preserve expected errors as business facts.

    Only declared assertion shapes are accepted. An unsupported shape is an
    executor error, never an implicit PASS or a guessed assertion.
    """
    assertion = step.get("assertion") or {}
    kind = assertion.get("type")
    if step.get("type") == "cluster_action":
        if step.get("action") != "reload" or kind != "command_succeeds":
            raise ValueError(f"平台不支持集群动作: {step.get('action')}")
        context.reload(node)
        context.step(f"step-{index}", step["title"], details={"action": "reload"})
        return
    if step.get("type") != "sql" or kind not in SUPPORTED_SQL_ASSERTIONS:
        raise ValueError(f"平台不支持 SQL 步骤或断言: {kind}")
    key = f"step-{index}"
    title = step["title"]
    query = step["sql"]
    database = step.get("database") or "postgres"
    sql_kwargs = {"database": database}
    if step.get("user"):
        sql_kwargs["user"] = step["user"]
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
    elif kind == "output_contains_text":
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
