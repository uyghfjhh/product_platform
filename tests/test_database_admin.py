"""数据库管理补全：会话/锁/复制/参数视图与取消会话——假 psycopg 验证 SQL 与行形状。"""

import platform_app.database as database


ENV = {
    "host": "127.0.0.1", "port": 15432,
    "database_name": "postgres", "database_user": "postgres",
}


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.connection.executed.append((sql, params))
        mapping = self.connection.respond
        self._rows = mapping(sql) if callable(mapping) else mapping.get(sql, [])
        self.description = [
            type("Col", (), {"name": name})() for name in self.connection.columns
        ]

    def fetchall(self):
        return self._rows

    @property
    def statusmessage(self):
        return "SELECT"


class FakeConnection:
    def __init__(self, columns, respond):
        self.columns = columns
        self.respond = respond
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return FakeCursor(self)


class FakePsycopg:
    def __init__(self, columns, respond):
        self.columns = columns
        self.respond = respond
        self.connections = []

    def connect(self, **kwargs):
        connection = FakeConnection(self.columns, self.respond)
        connection.kwargs = kwargs
        self.connections.append(connection)
        return connection


def _fake(monkeypatch, columns, respond):
    fake = FakePsycopg(columns, respond)
    monkeypatch.setattr(database, "psycopg", fake)
    monkeypatch.setattr(database, "tuple_row", object())
    return fake


def test_list_sessions_selects_activity_view(monkeypatch):
    fake = _fake(monkeypatch, ["pid", "state"], lambda sql: [(101, "active")])
    rows = database.list_sessions(ENV)
    assert rows == [{"pid": 101, "state": "active"}]
    sql = fake.connections[0].executed[0][0]
    assert "pg_stat_activity" in sql and "wait_event" in sql


def test_list_locks_includes_blocking_pids(monkeypatch):
    fake = _fake(monkeypatch, ["pid", "blocked_by"], lambda sql: [(5, "{7}")])
    rows = database.list_locks(ENV)
    assert rows[0]["blocked_by"] == "{7}"
    assert "pg_blocking_pids" in fake.connections[0].executed[0][0]


def test_list_replication_three_views(monkeypatch):
    _fake(monkeypatch, ["pid"], lambda sql: [(1,)])
    result = database.list_replication(ENV)
    assert set(result) == {"senders", "receivers", "slots"}
    assert result["senders"] == [{"pid": 1}]


def test_list_settings_default_and_search(monkeypatch):
    fake = _fake(monkeypatch, ["name"], lambda sql: [("max_connections",)])
    database.list_settings(ENV)
    sql = fake.connections[0].executed[0][0]
    assert "source <> 'default'" in sql
    database.list_settings(ENV, "wal")
    sql, params = fake.connections[1].executed[0]
    assert "ILIKE" in sql and params == ("%wal%",)


def test_settings_search_escapes_like_wildcards(monkeypatch):
    fake = _fake(monkeypatch, ["name"], lambda sql: [])
    database.list_settings(ENV, "100%")
    params = fake.connections[0].executed[0][1]
    assert params == ("%100\\%%",)


def test_cancel_backend_variants(monkeypatch):
    fake = _fake(monkeypatch, ["done"], lambda sql: [(True,)])
    assert database.cancel_backend(ENV, 42) is True
    assert "pg_cancel_backend(42)".replace("42", "%s") in fake.connections[0].executed[0][0]
    assert fake.connections[0].executed[0][1] == (42,)
    assert database.cancel_backend(ENV, 42, terminate=True) is True
    assert "pg_terminate_backend(%s)" in fake.connections[1].executed[0][0]


def test_cancel_backend_returns_false_when_backend_gone(monkeypatch):
    _fake(monkeypatch, ["done"], lambda sql: [(False,)])
    assert database.cancel_backend(ENV, 99) is False


def test_admin_routes_enforce_env_and_port(tmp_path, monkeypatch):
    from platform_app.api.routes_environments import register
    from platform_app.api.schemas import CancelBackendInput
    from fastapi import FastAPI, HTTPException
    import pytest

    class Store:
        def get_environment(self, env_id):
            if env_id == "lab":
                return dict(ENV, id="lab")
            return None

    registered = {}
    app = FastAPI()
    register(app, None, Store())
    route_map = {getattr(route, "path", ""): route.endpoint for route in app.routes}

    calls = []
    monkeypatch.setattr(
        "platform_app.api.routes_environments.list_sessions",
        lambda env: calls.append(env["port"]) or [],
    )
    endpoint = route_map["/api/v1/environments/{environment_id}/sessions"]
    assert endpoint("lab", port=29999) == []
    assert calls == [29999]
    with pytest.raises(HTTPException) as exc:
        endpoint("missing")
    assert exc.value.status_code == 404

    monkeypatch.setattr(
        "platform_app.api.routes_environments.cancel_backend",
        lambda env, pid, terminate: terminate,
    )
    cancel = route_map["/api/v1/environments/{environment_id}/sessions/{pid}/cancel"]
    assert cancel("lab", 7, CancelBackendInput(terminate=True))["terminated"] is True
    with pytest.raises(HTTPException) as exc:
        cancel("lab", 7, CancelBackendInput(terminate=False))
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException):
        cancel("lab", 0, CancelBackendInput())
