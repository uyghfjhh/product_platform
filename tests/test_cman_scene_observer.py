import json

from products.fbasecman.reports.artifacts import CaseProgressObserver
from platform_app.filestore import FileStore
from test_api import settings_for


def test_recorded_console_rows_become_scene_entities(tmp_path):
    settings = settings_for(tmp_path)
    store = FileStore(settings.data_dir, runtime_dir=settings.runtime_dir, logs_dir=settings.logs_dir)
    path = (settings.output_dir / "fbasecman" / "lab" / "runs" / "test-run" / "cases" /
            "rw_toggle.read" / "steps.json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"steps": [{
        "title": "route", "result": "PASS", "execution": [{"text": (
            "$ psql -c 'SHOW GROUP_ROUTING mmr_group;'\n\n"
            " group_name | candidate_node | route_status | effective_state\n"
            "------------+----------------+--------------+----------------\n"
            " mmr_group  | pg_2           | AVAILABLE    | active\n(1 row)"
        )}],
    }]}), encoding="utf-8")
    store.environments.put_environment({
        "id": "lab", "product_id": "fbasecman", "title": "Lab",
        "host": "127.0.0.1", "port": 5432, "database_name": "postgres",
        "database_user": "postgres", "deployment_config": None,
        "deployment_target": None,
    })
    task = store.tasks.create_task("lab", "tests.fbasecman", "rw_toggle.read", {}, None)
    observer = CaseProgressObserver(settings, "lab", "rw_toggle.read", 0)
    observer.poll(store, task["id"])
    observer.poll(store, task["id"])

    events = store.tasks.list_events(task["id"])
    discovered = [event for event in events if event["event_type"] == "scene.entity.discovered"]
    observed = [event for event in events if event["event_type"] == "scene.entity.observed"]
    assert len(discovered) == len(observed) == 1
    assert observed[0]["payload"]["entity_id"] == "cman:route:mmr_group:pg_2"
    assert observed[0]["payload"]["details"]["route_status"] == "AVAILABLE"
    assert observed[0]["payload"]["details"]["artifact"] == str(path)


def test_repeated_monitor_polls_discover_entity_once(tmp_path):
    settings = settings_for(tmp_path)
    store = FileStore(settings.data_dir, runtime_dir=settings.runtime_dir, logs_dir=settings.logs_dir)
    path = (settings.output_dir / "fbasecman" / "lab" / "runs" / "test-run" / "cases" /
            "ha_commands.refresh" / "steps.json")
    path.parent.mkdir(parents=True)
    output = ("$ psql -x -c 'SHOW NODE_MONITOR;'\n"
              "-[ RECORD 1 ]----------+----------------\n"
              "node_name | pg_1\ncluster_name | pg_cluster_1\n"
              "effective_status | READ_WRITE\n")
    path.write_text(json.dumps({"steps": [{
        "title": "poll", "result": "PASS", "execution": [{"text": output}, {"text": output}],
    }]}), encoding="utf-8")
    store.environments.put_environment({"id": "lab", "product_id": "fbasecman", "title": "Lab",
                           "host": "127.0.0.1", "port": 5432, "database_name": "postgres",
                           "database_user": "postgres", "deployment_config": None,
                           "deployment_target": None})
    task = store.tasks.create_task("lab", "tests.fbasecman", "ha_commands.refresh", {}, None)
    observer = CaseProgressObserver(settings, "lab", "ha_commands.refresh", 0)
    observer.poll(store, task["id"])
    events = store.tasks.list_events(task["id"])
    assert len([e for e in events if e["event_type"] == "scene.entity.discovered"]) == 1
    assert len([e for e in events if e["event_type"] == "scene.entity.observed"]) == 2


def test_string_execution_entries_do_not_crash_poll(tmp_path):
    """Engine steps.json records execution entries as raw strings (SQL/command
    text); a str entry must not kill the suite run (regression: observer crash
    SIGTERMs the regression CLI mid-suite)."""
    settings = settings_for(tmp_path)
    store = FileStore(settings.data_dir, runtime_dir=settings.runtime_dir, logs_dir=settings.logs_dir)
    path = (settings.output_dir / "fbasecman" / "lab" / "runs" / "test-run" / "cases" /
            "ha_commands.sql_parse" / "steps.json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"steps": [
        {"title": "query", "result": "PASS",
         "execution": ["SELECT 1", {"text": "SELECT 2"}]},
        "not-a-dict-step",
    ]}), encoding="utf-8")
    store.environments.put_environment({"id": "lab", "product_id": "fbasecman", "title": "Lab",
                           "host": "127.0.0.1", "port": 5432, "database_name": "postgres",
                           "database_user": "postgres", "deployment_config": None,
                           "deployment_target": None})
    task = store.tasks.create_task("lab", "tests.fbasecman", "ha_commands.sql_parse", {}, None)
    observer = CaseProgressObserver(settings, "lab", "ha_commands.sql_parse", 0)
    observer.poll(store, task["id"])  # must not raise
    observer.poll(store, task["id"])


def test_observer_failure_is_isolated_from_execution(tmp_path):
    from platform_app.actions import _poll_observer

    settings = settings_for(tmp_path)
    store = FileStore(settings.data_dir, runtime_dir=settings.runtime_dir, logs_dir=settings.logs_dir)
    store.environments.put_environment({"id": "lab", "product_id": "fbasecman", "title": "Lab",
                           "host": "127.0.0.1", "port": 5432, "database_name": "postgres",
                           "database_user": "postgres", "deployment_config": None,
                           "deployment_target": None})
    task = store.tasks.create_task("lab", "tests.fbasecman", "ha_commands.x", {}, None)

    class Broken:
        def poll(self, store, task_id):
            raise AttributeError("'str' object has no attribute 'get'")

    _poll_observer(Broken(), store, task["id"])  # must not raise
    errors = [e for e in store.tasks.list_events(task["id"])
              if e["event_type"] == "observer.error"]
    assert errors and "no attribute" in errors[0]["payload"]["error"]
