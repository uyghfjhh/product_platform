from platform_app.product_adapters.fbasecman.observations import (
    parse_group_routing,
    parse_node_monitor,
    parse_monitor_config,
    parse_group_members,
    parse_nodes,
    parse_groups,
    parse_node_status,
    parse_replication,
)
from platform_app.providers import FbasecmanProvider, FbaseProvider


def test_node_status_is_normalized_to_scene_facts():
    values = parse_node_status("node_name\tstate\tgroup_name\nnode1\tACTIVE\tmmr_group\n")
    assert values[0].kind == "fbasecman.node"
    assert values[0].state == "ready"
    assert values[0].details["entity_id"] == "cman:node:mmr_group:node1"


def test_group_routing_preserves_route_fields():
    values = parse_group_routing("group_name\tcandidate_node\troute_status\tis_write_target\nmmr_group\tpg_2\tAVAILABLE\ttrue\n")
    assert values[0].kind == "fbasecman.route"
    assert values[0].details["is_write_target"] == "true"
    assert values[0].details["entity_id"] == "cman:route:mmr_group:pg_2"


def test_node_monitor_normalizes_effective_status():
    values = parse_node_monitor(
        "group_name | node_name | effective_status\n"
        "------------+-----------+-----------------\n"
        "postgres | pg_220 | HEALTHY\n"
    )
    assert values[0].kind == "fbasecman.monitor"
    assert values[0].state == "ready"
    assert values[0].details["entity_id"] == "cman:monitor:postgres:pg_220"


def test_monitor_config_is_published_as_enabled_fact():
    values = parse_monitor_config(
        "monitor_enabled | monitor_period | monitor_max_retries\n"
        "-----------------+----------------+---------------------\n"
        "true | 5 | 3\n"
    )
    assert values[0].kind == "fbasecman.monitor.config"
    assert values[0].state == "enabled"


def test_group_members_normalizes_mmr_roles():
    values = parse_group_members(
        "group_name | node_name | group_role | state | primary\n"
        "------------+-----------+------------+-------+--------\n"
        "postgres | pg_220 | write-leader | active | \n"
    )
    assert values[0].kind == "fbasecman.mmr.member"
    assert values[0].state == "ready"
    assert values[0].details["entity_id"] == "cman:node:postgres:pg_220"


def test_nodes_normalizes_effective_routing_status():
    values = parse_nodes(
        "node_name | cluster_name | storage_db | effective_role | effective_status\n"
        "-----------+--------------+------------+----------------+------------------\n"
        "pg_220 | mmr_cluster_1 | postgres | PRIMARY | WRITE_ONLY\n"
    )
    assert values[0].kind == "fbasecman.node.effective"
    assert values[0].state == "ready"
    assert values[0].details["effective_role"] == "PRIMARY"
    assert values[0].details["entity_id"] == "cman:node:postgres:pg_220"


def test_groups_preserve_write_and_promoted_clusters():
    values = parse_groups(
        "group_name | group_mode | write_cluster | promoted_cluster\n"
        "------------+------------+---------------+------------------\n"
        "postgres | mmr | mmr_cluster_1 | mmr_cluster_2\n"
    )
    assert values[0].kind == "fbasecman.mmr.group"
    assert values[0].details["write_cluster"] == "mmr_cluster_1"


def test_node_monitor_parses_psql_expanded_records():
    values = parse_node_monitor(
        "$ psql -x -c 'SHOW NODE_MONITOR;'\n"
        "-[ RECORD 1 ]----------+----------------\n"
        "node_name | pg_1\ncluster_name | pg_cluster_1\n"
        "effective_status | READ_WRITE\n"
        "connect_status | ONLINE\n"
        "-[ RECORD 2 ]----------+----------------\n"
        "node_name | pg_3\ncluster_name | pg_cluster_1\n"
        "effective_status | OFFLINE\n"
    )
    assert [item.details["entity_id"] for item in values] == [
        "cman:monitor:pg_cluster_1:pg_1", "cman:monitor:pg_cluster_1:pg_3",
    ]
    assert values[1].state == "offline"


def test_real_refresh_cluster_monitor_fixture_has_online_and_offline_states():
    from pathlib import Path
    import json

    path = Path("data/legacy_cman/cman-lab/output/runs/ha_commands/refresh_cluster/steps.json")
    if not path.is_file():
        return
    values = []
    for step in json.loads(path.read_text(encoding="utf-8"))["steps"]:
        for execution in step.get("execution", []):
            text = execution.get("text", "")
            if text and "SHOW NODE_MONITOR" in text.splitlines()[0]:
                values.extend(parse_node_monitor(text))
    assert values
    assert {value.state for value in values} >= {"ready", "offline"}


def test_replication_normalizes_streaming_state():
    values = parse_replication(
        "application_name\tclient_addr\tstate\tsync_state\nstandby1\t127.0.0.2\tstreaming\tasync\n"
    )
    assert values[0].kind == "postgres.replication"
    assert values[0].state == "streaming"
    assert values[0].details["sync_state"] == "async"


def test_fbasecman_parses_recorded_psql_table():
    output = ("$ psql -c 'SHOW NODE_STATUS;'\n\n"
              " group_name | node_name | state\n"
              "------------+-----------+-------\n"
              " mmr_group  | pg_2      | active\n(1 row)")
    values = FbasecmanProvider().parse_runtime("node_status", output)
    assert len(values) == 1
    assert values[0].details["entity_id"] == "cman:node:mmr_group:pg_2"


def test_fbase_runtime_probe_reads_replication_rows(monkeypatch, tmp_path):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql): assert "pg_stat_replication" in sql
        def fetchall(self): return [("standby1", "127.0.0.2", "streaming", "async")]

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
        def cursor(self): return Cursor()
        def close(self): pass

    monkeypatch.setattr("psycopg.connect", lambda **kwargs: Connection())
    values = FbaseProvider().observe_runtime(tmp_path, {
        "host": "127.0.0.1", "port": 5432,
        "database_name": "postgres", "database_user": "postgres",
    })
    assert values[0].state == "streaming"
    assert values[0].details["entity_id"] == "standby1"
