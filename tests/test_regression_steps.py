import json

import psycopg
import pytest

from platform_regress.commands import display_command, render_command
from platform_regress.sdk import CaseContext, SqlResult, run_sql_step
from platform_regress.steps import run_command_step


def test_rendered_command_redacts_common_secret_forms():
    command = render_command([
        "env", "PGPASSWORD=secret", "client", "--token", "token-value",
        "host=127.0.0.1 password='dsn-secret' dbname=postgres",
    ])
    assert "secret" not in command and "token-value" not in command
    assert command.count("<redacted>") == 3


def test_display_command_unwraps_shell_and_psql_quoting():
    script = "for i in 1 2; do psql -c \"SELECT 'x'\"; done"
    assert display_command(["sh", "-ec", script]) == script
    sql = "SELECT count(*) WHERE backend_type='fdd mmr supervisor'"
    shown = display_command(["/bin/psql", "-X", "-At", "-c", sql])
    assert shown == "/bin/psql -c\n  %s" % sql
    shown = display_command([
        "psql", "-X", "-v", "ON_ERROR_STOP=1", "-P", "pager=off", "--csv",
        "-h", "127.0.0.1", "-p", "5432", "-U", "sso", "-d", "db",
        "-c", "SELECT 1"])
    assert shown == \
        "psql -h 127.0.0.1 -p 5432 -U sso -d db -c\n  SELECT 1"
    assert display_command(["env", "PGPASSWORD=s3cr3t", "psql", "-c", "SELECT 1"]) \
        == "env 'PGPASSWORD=<redacted>' psql -c\n  SELECT 1"
    assert display_command(["ls", "-l"], override="-- node\nSELECT 1") \
        == "-- node\nSELECT 1"
    assert display_command(["script", "-qefc", "pg_ctl start", "/dev/null"]) \
        == "pg_ctl start"


def test_command_step_records_execution_expectation_and_analysis(tmp_path):
    context = CaseContext("demo.command", tmp_path, environment={
        "nodes": {"primary": {"host": "127.0.0.1", "port": 5432}},
    })
    step = {
        "type": "command", "title": "Check command", "node": "primary",
        "argv": ["printf", "ready\\n"], "expected": "输出 ready",
        "assertion": {"type": "output_contains", "values": ["ready"]},
    }
    run_command_step(context, step, 1)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    fact = next(event for event in events if event["kind"] == "step.finished")
    details = fact["payload"]["details"]
    assert details["command"] == "printf 'ready\\n'"
    assert details["expected"] == "输出 ready"
    assert details["assertion"] == step["assertion"]
    assert details["actual"] == "输出=ready，退出码=0"
    assert "输出包含全部声明值" in details["analysis"]
    assert details["evidence"].endswith("command-1.json")
    evidence = json.loads((tmp_path / details["evidence"]).read_text())
    assert evidence["command"] == details["command"]


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


def test_halted_steps_preserve_planned_expectation(tmp_path):
    from platform_regress.steps import run_declared_steps
    context = CaseContext('demo.halt', tmp_path)
    steps = [
        {'type': 'command', 'title': '失败操作', 'argv': ['false'],
         'expected': '退出码 0', 'assertion': {'type': 'command_succeeds'}},
        {'type': 'command', 'title': '后续检查', 'argv': ['true'],
         'intent': 'verify', 'expected': '正常启动',
         'assertion': {'type': 'command_succeeds'}},
    ]
    with pytest.raises(AssertionError):
        run_declared_steps(context, steps)
    events = [json.loads(line) for line in (tmp_path / 'events.jsonl').read_text().splitlines()]
    blocked = next(e['payload'] for e in events if e['kind'] == 'step.finished' and e['payload'].get('status') == 'BLOCKED')
    assert blocked['details']['expected'] == '正常启动'
    assert blocked['details']['actual'] == '未执行：前序步骤失败'
