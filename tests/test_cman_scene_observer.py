import json

from products.fbasecman.reports.artifacts import CaseProgressObserver
from platform_app.storage import Store
from test_api import settings_for


def test_recorded_console_rows_become_scene_entities(tmp_path):
    settings = settings_for(tmp_path)
    path = (settings.data_dir / "legacy_cman" / "lab" / "output" / "runs" /
            "rw_toggle" / "read" / "steps.json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"steps": [{
        "title": "route", "result": "PASS", "execution": [{"text": (
            "$ psql -c 'SHOW GROUP_ROUTING mmr_group;'\n\n"
            " group_name | candidate_node | route_status | effective_state\n"
            "------------+----------------+--------------+----------------\n"
            " mmr_group  | pg_2           | AVAILABLE    | active\n(1 row)"
        )}],
    }]}), encoding="utf-8")
    store = Store(settings.database)
    store.put_environment({
        "id": "lab", "product_id": "fbasecman", "title": "Lab",
        "host": "127.0.0.1", "port": 5432, "database_name": "postgres",
        "database_user": "postgres", "deployment_config": None,
        "deployment_target": None,
    })
    task = store.create_task("lab", "tests.fbasecman", "rw_toggle.read", {}, None)
    observer = CaseProgressObserver(settings, "lab", "rw_toggle.read", 0)
    observer.poll(store, task["id"])
    observer.poll(store, task["id"])

    events = store.list_events(task["id"])
    discovered = [event for event in events if event["event_type"] == "scene.entity.discovered"]
    observed = [event for event in events if event["event_type"] == "scene.entity.observed"]
    assert len(discovered) == len(observed) == 1
    assert observed[0]["payload"]["entity_id"] == "cman:route:mmr_group:pg_2"
    assert observed[0]["payload"]["details"]["route_status"] == "AVAILABLE"
    assert observed[0]["payload"]["details"]["artifact"] == str(path)


def test_repeated_monitor_polls_discover_entity_once(tmp_path):
    settings = settings_for(tmp_path)
    path = (settings.data_dir / "legacy_cman" / "lab" / "output" / "runs" /
            "ha_commands" / "refresh" / "steps.json")
    path.parent.mkdir(parents=True)
    output = ("$ psql -x -c 'SHOW NODE_MONITOR;'\n"
              "-[ RECORD 1 ]----------+----------------\n"
              "node_name | pg_1\ncluster_name | pg_cluster_1\n"
              "effective_status | READ_WRITE\n")
    path.write_text(json.dumps({"steps": [{
        "title": "poll", "result": "PASS", "execution": [{"text": output}, {"text": output}],
    }]}), encoding="utf-8")
    store = Store(settings.database)
    store.put_environment({"id": "lab", "product_id": "fbasecman", "title": "Lab",
                           "host": "127.0.0.1", "port": 5432, "database_name": "postgres",
                           "database_user": "postgres", "deployment_config": None,
                           "deployment_target": None})
    task = store.create_task("lab", "tests.fbasecman", "ha_commands.refresh", {}, None)
    observer = CaseProgressObserver(settings, "lab", "ha_commands.refresh", 0)
    observer.poll(store, task["id"])
    events = store.list_events(task["id"])
    assert len([e for e in events if e["event_type"] == "scene.entity.discovered"]) == 1
    assert len([e for e in events if e["event_type"] == "scene.entity.observed"]) == 2
