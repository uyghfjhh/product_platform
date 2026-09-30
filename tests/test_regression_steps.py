import json

import psycopg
import pytest

from platform_regress.sdk import CaseContext, SqlResult, run_sql_step


def test_declarative_sql_rows_mismatch_is_failure(tmp_path):
    context = CaseContext("demo.rows", tmp_path)
    context.sql = lambda node, query, database="postgres": SqlResult((("wrong",),), (), "SELECT 1")
    step = {"type": "sql", "title": "Check rows", "sql": "SELECT value",
            "assertion": {"type": "rows_equal", "rows": [["expected"]]}}

    with pytest.raises(AssertionError, match="SQL 断言失败"):
        run_sql_step(context, step, 1, "primary")
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert events[-1]["payload"]["status"] == "FAIL"


def test_declarative_sql_expected_error_requires_text_and_state(tmp_path):
    context = CaseContext("demo.denial", tmp_path)
    context.sql = lambda node, query, database="postgres": (
        (_ for _ in ()).throw(psycopg.errors.InsufficientPrivilege("permission denied"))
    )
    step = {"type": "sql", "title": "Check denial", "sql": "SELECT secret",
            "assertion": {"type": "sql_error", "sqlstate": "42501",
                          "message_contains": "permission denied"}}
    run_sql_step(context, step, 1, "primary")
    step["assertion"]["message_contains"] = "different error"
    with pytest.raises(AssertionError, match="错误与预期不符"):
        run_sql_step(context, step, 2, "primary")


def test_declarative_sql_unexpected_success_is_failure(tmp_path):
    context = CaseContext("demo.unexpected", tmp_path)
    context.sql = lambda node, query, database="postgres": SqlResult((), (), "ALTER SYSTEM")
    step = {"type": "sql", "title": "Must be denied", "sql": "SELECT 1",
            "assertion": {"type": "sql_fails", "message_contains": "permission denied"}}
    with pytest.raises(AssertionError, match="意外成功"):
        run_sql_step(context, step, 1, "primary")
