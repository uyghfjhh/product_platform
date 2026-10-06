from uuid import uuid4

import pytest
from platform_app.studio_runtime import cancel, record, snapshot, track


def env(port):
    return {
        "id": "test",
        "host": "localhost",
        "port": port,
        "database_name": "postgres",
        "database_user": "postgres",
    }


class Connection:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def test_cancel_is_bound_to_exact_instance():
    first, other = env(7000), env(7001)
    request = str(uuid4())
    connection = Connection()
    with track(first, request, connection):
        assert cancel(other, request)["active"] is False
        assert not connection.cancelled
        assert cancel(first, request)["active"] is True
        assert connection.cancelled


def test_cancel_before_connection_prevents_late_execution():
    target = env(7002)
    request = str(uuid4())
    cancel(target, request)
    with pytest.raises(ValueError, match="已取消"):
        with track(target, request, Connection()):
            pytest.fail("cancelled query executed")


def test_measurements_are_scoped_and_remove_literals_and_comments():
    target = env(7003)
    record(target, "SELECT 'super-secret', 12 -- password=another-secret", 3.5, 1)
    record(target, "SELECT 'different-value', 13", 4.5, 2)
    result = snapshot(target)
    assert len(result["queries"]) == 1
    query = result["queries"][0]
    assert query["count"] == 2 and query["rowsReturned"] == 3
    assert "secret" not in query["query"] and "different" not in query["query"]
    assert snapshot(env(7004))["queries"] == []
