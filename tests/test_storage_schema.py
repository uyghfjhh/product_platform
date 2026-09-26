import sqlite3

import pytest
from platform_app import migrations
from platform_app.storage import Store


def test_store_records_schema_version(tmp_path):
    store = Store(tmp_path / "platform.sqlite3")
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone() == ("2",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='tasks_environment_status_idx'"
        ).fetchone() == ("tasks_environment_status_idx",)


def test_store_rejects_newer_schema(tmp_path):
    path = tmp_path / "platform.sqlite3"
    Store(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE schema_meta SET value='99' WHERE key='schema_version'"
        )
    with pytest.raises(RuntimeError, match="schema 版本过新"):
        Store(path)


def test_v1_database_upgrades_without_losing_data(tmp_path):
    path = tmp_path / "platform.sqlite3"
    store = Store(path)
    store.put_environment({
        "id": "lab", "product_id": "fbasecman", "title": "Lab",
        "host": "127.0.0.1", "port": 5432, "database_name": "postgres",
        "database_user": "postgres", "deployment_config": None,
        "deployment_target": None,
    })
    with sqlite3.connect(path) as connection:
        for name in ("tasks_environment_status_idx", "results_environment_idx",
                     "diagnoses_environment_idx"):
            connection.execute(f"DROP INDEX {name}")
        connection.execute("UPDATE schema_meta SET value='1' WHERE key='schema_version'")

    upgraded = Store(path)
    assert upgraded.get_environment("lab")["title"] == "Lab"
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone() == ("2",)


def test_failed_migration_rolls_back_schema_and_version(tmp_path, monkeypatch):
    path = tmp_path / "platform.sqlite3"
    Store(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE schema_meta SET value='1' WHERE key='schema_version'")
        connection.execute("DROP INDEX tasks_environment_status_idx")
    monkeypatch.setitem(migrations.MIGRATIONS, 2, (
        "CREATE INDEX tasks_environment_status_idx ON tasks(environment_id, status)",
        "CREATE INDEX broken_idx ON missing_table(id)",
    ))

    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone() == ("1",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='tasks_environment_status_idx'"
        ).fetchone() is None
