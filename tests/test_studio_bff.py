"""Prisma Studio BFF 协议：$N 翻译、Either 响应、序列化、事务/序列/诊断。"""

from datetime import datetime
from decimal import Decimal

import platform_app.studio as studio
import pytest

ENV = {
    "host": "127.0.0.1", "port": 15432,
    "database_name": "postgres", "database_user": "postgres",
}


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.connection.executed.append((sql, params))
        result = self.connection.respond(sql, params)
        if isinstance(result, Exception):
            raise result
        columns, rows = result
        self.description = [type("Col", (), {"name": c})() for c in columns] or None
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, respond):
        self.respond = respond
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakePsycopg:
    def __init__(self, respond):
        self.respond = respond
        self.connections = []

    def connect(self, **kwargs):
        connection = FakeConnection(self.respond)
        connection.kwargs = kwargs
        self.connections.append(connection)
        return connection


def _fake(monkeypatch, respond):
    fake = FakePsycopg(respond)
    monkeypatch.setattr(studio, "psycopg", fake)
    monkeypatch.setattr(studio, "tuple_row", object())
    return fake


# ---------- $N 翻译 ----------

def test_translate_dollar_params():
    sql, order = studio._translate_parameters("select $1, $2 where $1")
    assert sql == "select %s, %s where %s"
    assert order == [1, 2, 1]


def test_translate_out_of_order_params():
    sql, order = studio._translate_parameters("select $2, $1")
    assert order == [2, 1]


def test_translate_skips_literals_and_comments():
    sql, order = studio._translate_parameters(
        "select '$1' as s, \"$2\" as c, $3 -- $4\n/* $5 */ , $$body $6$$, $tag$x$7$tag$"
    )
    assert sql.count("%s") == 1
    assert order == [3]


def test_translate_dollar_quote_contents_ignored():
    sql, order = studio._translate_parameters("do $$ begin raise notice '$1'; end $$; select $2")
    assert sql.endswith("select %s")
    assert order == [2]


# ---------- 序列化 ----------

def test_encode_value_safety():
    assert studio._encode_value(Decimal("1.5"), "c", {}) == "1.5"
    assert studio._encode_value(2**60, "c", {}) == str(2**60)
    assert studio._encode_value(42, "c", {}) == 42
    when = datetime(2026, 1, 2, 3, 4, 5)
    assert studio._encode_value(when, "c", {}) == "2026-01-02T03:04:05"
    assert studio._encode_value({"a": 1}, "c", {"c": "json-parse"}) == '{"a": 1}'
    assert studio._encode_value({"a": 1}, "c", {}) == {"a": 1}
    assert studio._encode_value(b"\x01\xff", "c", {}) == "\\x01ff"


# ---------- dispatch ----------

def test_query_returns_either_rows_and_binds_params(monkeypatch):
    captured = {}

    def respond(sql, params):
        captured.setdefault("queries", []).append((sql, params))
        if "set_config" in sql:
            return [], []
        return ["id", "created"], [(1, datetime(2026, 1, 1))]

    fake = _fake(monkeypatch, respond)
    result = studio.studio_dispatch(ENV, {
        "procedure": "query",
        "schema": "tenant1",
        "query": {"sql": "select * from t where a=$2 and b=$1", "parameters": ["p1", "p2"]},
    })
    assert result[0] is None
    assert result[1] == [{"id": 1, "created": "2026-01-01T00:00:00"}]
    queries = captured["queries"]
    assert queries[0][0] == "SELECT set_config('search_path', %s, false)"
    assert queries[0][1] == ("tenant1",)
    # $2 先出现 → 参数按出现序重排
    assert queries[1][0] == "select * from t where a=%s and b=%s"
    assert queries[1][1] == ("p2", "p1")
    assert fake.connections[0].kwargs["autocommit"] is True


def test_query_param_index_out_of_range_errors(monkeypatch):
    _fake(monkeypatch, lambda sql, params: ([], []))
    result = studio.studio_dispatch(ENV, {
        "procedure": "query",
        "query": {"sql": "select $5", "parameters": [1]},
    })
    assert result[0]["name"] == "ValueError"


def test_sequence_collects_both_results(monkeypatch):
    def respond(sql, params):
        if "boom" in sql:
            return ValueError("relation missing")
        return ["v"], [(7,)]

    _fake(monkeypatch, respond)
    result = studio.studio_dispatch(ENV, {
        "procedure": "sequence",
        "sequence": [
            {"sql": "select ok", "parameters": []},
            {"sql": "select boom", "parameters": []},
        ],
    })
    assert result[0] == [None, [{"v": 7}]]
    assert result[1][0]["message"] == "relation missing"


def test_sequence_requires_two_queries(monkeypatch):
    _fake(monkeypatch, lambda sql, params: ([], []))
    result = studio.studio_dispatch(ENV, {"procedure": "sequence", "sequence": [{"sql": "x"}]})
    assert result[0]["name"] == "ValueError"


def test_transaction_commits_all_queries(monkeypatch):
    _fake(monkeypatch, lambda sql, params: (["n"], [(1,)]))
    result = studio.studio_dispatch(ENV, {
        "procedure": "transaction",
        "queries": [{"sql": "a"}, {"sql": "b"}],
    })
    assert result == [None, [[{"n": 1}], [{"n": 1}]]]
    connection = studio.psycopg.connections[0]
    assert connection.kwargs["autocommit"] is False
    assert connection.commits == 1 and connection.rollbacks == 0


def test_transaction_rolls_back_on_failure(monkeypatch):
    def respond(sql, params):
        if sql == "b":
            return ValueError("constraint")
        return ["n"], [(1,)]

    _fake(monkeypatch, respond)
    result = studio.studio_dispatch(ENV, {
        "procedure": "transaction",
        "queries": [{"sql": "a"}, {"sql": "b"}],
    })
    assert result[0]["message"] == "constraint"
    connection = studio.psycopg.connections[0]
    assert connection.commits == 0 and connection.rollbacks == 1


def test_sql_lint_clean_and_error(monkeypatch):
    _fake(monkeypatch, lambda sql, params: (["plan"], [("Seq Scan",)]))
    result = studio.studio_dispatch(ENV, {"procedure": "sql-lint", "sql": "select 1"})
    assert result == [None, {"diagnostics": []}]
    connection = studio.psycopg.connections[0]
    assert connection.executed[0][0] == "EXPLAIN select 1"
    assert connection.rollbacks == 1  # 总是回滚，ANALYZE 也不落盘

    def bad(sql, params):
        return ValueError('syntax error at or near "selct"')

    _fake(monkeypatch, bad)
    result = studio.studio_dispatch(ENV, {"procedure": "sql-lint", "sql": "selct 1"})
    diagnostic = result[1]["diagnostics"][0]
    assert diagnostic["severity"] == "error"
    assert "selct" in diagnostic["message"]


def test_unknown_procedure_errors(monkeypatch):
    _fake(monkeypatch, lambda sql, params: ([], []))
    result = studio.studio_dispatch(ENV, {"procedure": "query-insights"})
    assert result[0]["name"] == "ValueError"
    result = studio.studio_dispatch(ENV, {"procedure": "nope"})
    assert result[0]["name"] == "ValueError"


def test_missing_psycopg_dependency_errors(monkeypatch):
    monkeypatch.setattr(studio, "psycopg", None)
    result = studio.studio_dispatch(ENV, {"procedure": "query", "query": {}})
    assert result[0]["name"] == "RuntimeError"


def test_query_error_wrapped_as_either(monkeypatch):
    _fake(monkeypatch, lambda sql, params: ValueError("boom"))
    result = studio.studio_dispatch(ENV, {
        "procedure": "query", "query": {"sql": "select", "parameters": []},
    })
    assert result[0]["message"] == "boom" and len(result) == 1


def test_studio_route_env_validation(tmp_path, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from platform_app.api.routes_environments import register

    class Store:
        def __init__(self):
            from types import SimpleNamespace
            self.environments = SimpleNamespace(get_environment=self.get_environment)

        def get_environment(self, env_id):
            return dict(ENV, id="lab") if env_id == "lab" else None

    app = FastAPI()
    register(app, None, Store())
    endpoint = {
        getattr(route, "path", ""): route.endpoint for route in app.routes
    }["/api/v1/environments/{environment_id}/studio/nodes/{node_id}"]

    seen = []
    monkeypatch.setattr(
        "platform_app.api.routes_environments.studio_dispatch",
        lambda env, body: seen.append((env["host"], env["port"], body)) or [None, []],
    )
    monkeypatch.setattr("platform_app.api.routes_environments.configured_topology", lambda *_: {
        "nodes": [{"id": "local", "host": "127.0.0.1", "port": 29999},
                  {"id": "remote", "host": "10.0.0.2", "port": 29999}]})
    assert endpoint("lab", node_id="remote", item={"procedure": "query"}) == [None, []]
    assert seen == [("10.0.0.2", 29999, {"procedure": "query"})]
    with pytest.raises(HTTPException) as invalid:
        endpoint("lab", node_id="foreign", item={})
    assert invalid.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        endpoint("missing", node_id="remote", item={})
    assert exc.value.status_code == 404


def test_literal_percent_sql_does_not_activate_driver_bind_parsing(monkeypatch):
    original = FakeCursor.execute

    def execute_without_bindings(self, sql, *bindings):
        assert not bindings, 'Percent-bearing SQL with no binds must be sent as raw SQL'
        return original(self, sql)

    monkeypatch.setattr(FakeCursor, 'execute', execute_without_bindings)
    _fake(monkeypatch, lambda sql, params: (['value'], [('%example%',)]))
    result = studio.studio_dispatch(ENV, {
        'procedure': 'query', 'query': {'sql': "SELECT '%example%' AS value", 'parameters': []},
    })
    assert result == [None, [{'value': '%example%'}]]


def test_percent_literals_and_modulo_survive_bound_parameters(monkeypatch):
    def respond(sql, params):
        assert sql == "SELECT %s::int %% 2, '%%example%%'"
        assert params == (7,)
        return ['value'], [(1,)]

    _fake(monkeypatch, respond)
    result = studio.studio_dispatch(ENV, {
        'procedure': 'query', 'query': {'sql': "SELECT $1::int % 2, '%example%'", 'parameters': [7]},
    })
    assert result == [None, [{'value': 1}]]
