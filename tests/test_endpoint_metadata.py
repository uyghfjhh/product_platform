import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from platform_app.deployment.endpoint_metadata import EndpointMetadata
from platform_app.deployment.primary_migration import primary_cluster
from platform_app.deployment.workbench import diff_operations
from test_deployment_changes import config
from test_primary_migration import fake_executor


def context(kind='logical'):
    raw = config()
    raw['instances']['q'] = {**raw['instances']['p'], 'port': 7010, 'data_dir': '/data/q'}
    raw['streaming_clusters']['peer'] = {'primary': 'q', 'standbys': []}
    if kind == 'logical':
        raw['logical_replications'] = {'link': {'pub': {'streaming_cluster': 'stream'}, 'sub': {'streaming_cluster': 'peer'}}}
    elif kind == 'mmr':
        raw['mmr_clusters'] = {'group': {'database': 'postgres', 'group_name': 'group',
                                       'members': {'a': {'streaming_cluster': 'stream', 'node_name': 'a'},
                                                   'b': {'streaming_cluster': 'peer', 'node_name': 'b'}}}}
    else:
        raw['citus_clusters'] = {'group': {'database': 'postgres', 'coordinator': {'streaming_cluster': 'peer'},
                                         'workers': {'a': {'streaming_cluster': 'stream'}}}}
    return raw


def metadata(kind='logical'):
    raw = context(kind)
    source = {'host_config': {'address': '127.0.0.1'}, 'port': 7000}
    destination = {'host_config': {'address': '192.168.0.20'}, 'port': 7020}
    executor = SimpleNamespace(current=raw)
    desired = deepcopy(raw)
    desired['hosts']['destination'] = {'address': destination['host_config']['address']}
    desired['instances']['p'].update(host='destination', port=destination['port'])
    def runtime(value):
        return SimpleNamespace(config=SimpleNamespace(raw=value, instance=lambda node: {
            **value['instances'][node], 'host_config': value['hosts'][value['instances'][node]['host']]}))
    migration = SimpleNamespace(e=executor, old=runtime(raw), new=runtime(desired), cluster='stream', source=source,
                                destination=destination, state={})
    return EndpointMetadata(migration)


def test_declared_logical_subscriber_can_move_but_unprotected_publisher_cannot():
    raw = context(); desired = deepcopy(raw)
    desired['instances']['q'].update(port=7021, data_dir='/data/moved-q')
    assert primary_cluster(raw, desired, 'q') == 'peer'
    assert diff_operations(raw, desired)[2]
    desired = deepcopy(raw); desired['instances']['p']['data_dir'] = '/data/moved-p'
    desired['instances']['p']['port'] = 7022
    assert primary_cluster(raw, desired, 'p') is None
    desired['instances']['p']['data_dir'] = '/data/p'
    assert diff_operations(raw, desired)[2]


@pytest.mark.parametrize('kind', ['mmr', 'citus'])
def test_declared_replication_endpoint_move_has_a_reviewed_plan(kind):
    raw = context(kind); desired = deepcopy(raw)
    desired['instances']['p'].update(port=7021, data_dir='/data/moved')
    assert diff_operations(raw, desired)[2]
    if kind == 'mmr':
        raw['mmr_clusters']['group']['members']['a']['mmr_node'] = {'failover_slot': False}
        assert primary_cluster(raw, desired, 'p') is None


def test_subscription_change_keeps_credentials_in_server_catalog_and_overrides_hostaddr():
    endpoint = metadata()
    statement = []
    row = {'subslotname': 'link_slot', 'subpublications': ['link_pub'],
           'subconninfo': "host=127.0.0.1 hostaddr=127.0.0.1 port=7000 dbname=postgres password=private_test_secret sslmode=verify-full"}
    endpoint.sub_row = lambda *args: row
    endpoint.sql = lambda *args: statement.append(args[-1])
    endpoint.update_subscription(endpoint.contexts[0])
    assert len(statement) == 1 and 'ALTER SUBSCRIPTION' in statement[0]
    assert 'private_test_secret' not in statement[0]
    assert 'hostaddr=192.168.0.20' in statement[0]
    assert 'private_test_secret' not in json.dumps(endpoint.state)
    row['subslotname'] = 'foreign_slot'
    with pytest.raises(ValueError, match='身份'):
        endpoint.update_subscription(endpoint.contexts[0])
    assert len(statement) == 1


def test_citus_resume_rejects_node_id_reused_for_a_different_endpoint():
    endpoint = metadata('citus')
    endpoint.state['citus:group:worker'] = {'nodeid': 7, 'groupid': 2}
    calls = []
    def sql(*args):
        calls.append(args[-1])
        return json.dumps({'nodename': 'other-host', 'nodeport': 7050, 'groupid': 2, 'noderole': 'primary'})
    endpoint.sql = sql
    with pytest.raises(ValueError, match='身份'):
        endpoint.update_citus(endpoint.contexts[0])
    assert not any('citus_update_node' in call for call in calls)


def test_unregistered_nonfailover_slot_blocks_full_migration_before_clone(tmp_path):
    _, migration = fake_executor(tmp_path)
    migration.identity = lambda *args: '123'
    migration.logical_slots = lambda *args: [{'slot_name': 'external', 'failover': False}]
    with pytest.raises(ValueError, match='不能延续'):
        migration.preflight()


def test_failover_flag_without_restart_wal_does_not_authorize_promotion(tmp_path):
    _, migration = fake_executor(tmp_path)
    slot = {'slot_name': 'mmr_peer', 'plugin': 'fddoutput', 'database': 'postgres', 'slot_type': 'logical',
            'failover': True, 'active': False, 'restart_lsn': '0/1000000', 'confirmed_flush_lsn': '0/1000100'}
    migration.state['logical_slots'] = [slot]
    migration.logical_slots = lambda _: [slot]
    migration.sql = lambda runtime, node, sql: '16777216' if 'pg_size_bytes' in sql else ''
    with pytest.raises(ValueError, match='缺少逻辑槽所需 WAL'):
        migration.check_logical_slots()
    migration.sql = lambda runtime, node, sql: '16777216' if 'pg_size_bytes' in sql else '000000010000000000000001'
    migration.check_logical_slots()
    slot['active'] = True
    with pytest.raises(ValueError, match='未准备完成'):
        migration.check_logical_slots()


def test_slot_added_after_review_stops_fencing_while_source_remains_running(tmp_path):
    executor, migration = fake_executor(tmp_path, running=True)
    migration.logical_slots = lambda _: [{'slot_name': 'late', 'failover': True}]
    stops = []
    executor.stop = lambda *args: stops.append(args)
    with pytest.raises(ValueError, match='未审阅变化'):
        migration.fence()
    assert stops == []


def test_failed_endpoint_statement_cannot_expose_existing_connection_credentials():
    endpoint = metadata()
    captured = []
    def execute(args, **kwargs):
        captured.append(args)
        raise RuntimeError('server context contains password=private_test_secret')
    runtime = SimpleNamespace(config=SimpleNamespace(instance=lambda _: {
        'host_config': {'address': '127.0.0.1'}, 'port': 7000, 'installation_config': {'home': '/opt/pgsql'}}),
        executor=SimpleNamespace(run=execute))
    with pytest.raises(ValueError) as error:
        endpoint.sql(runtime, 'p', 'postgres', 'SELECT 1')
    assert 'private_test_secret' not in str(error.value)
    assert error.value.__suppress_context__
    assert '-c' not in captured[0] and 'SELECT 1' not in captured[0]


def test_failover_link_on_an_unsupported_server_is_rejected_before_any_mutation(tmp_path):
    from platform_app.deployment.logical_changes import LogicalChanges
    from test_replication_changes import executor
    raw = context(); raw.pop('logical_replications')
    desired = context(); desired['logical_replications']['link']['sub']['slot'] = {'failover': True}
    change = LogicalChanges(executor(tmp_path, raw, desired), 'link')
    calls = []
    def sql(link, role, statement):
        calls.append(statement)
        if 'data_directory' in statement:
            node = 'p' if role == 'pub' else 'q'
            return desired['instances'][node]['data_dir']+'|'+str(desired['instances'][node]['port'])+'|false'
        return '0'
    change.sql = sql
    with pytest.raises(ValueError, match='实际支持 failover'):
        change.preflight()
    assert not any(statement.startswith(('CREATE', 'ALTER', 'DROP')) for statement in calls)


def test_loopback_dsn_is_relative_to_observer_and_cannot_be_a_crosshost_destination():
    endpoint = metadata()
    remote = {'host_config': {'address': '192.168.0.15'}, 'port': 7000}
    destination = {'host_config': {'address': '192.168.0.12'}, 'port': 7000}
    conn = {'host': '127.0.0.1', 'port': '7000'}
    assert endpoint.matches(conn, remote, remote)
    assert not endpoint.matches(conn, destination, remote)
    assert not endpoint.matches(conn, destination, global_route=True)
    assert endpoint.matches({'hostaddr': '192.168.0.12', 'host': 'db-cert-name', 'port': '7000'}, destination)


def test_mmr_endpoint_receipt_contains_no_password_derived_value():
    endpoint = metadata('mmr')
    assert endpoint.receipt('host=127.0.0.1 port=7000 dbname=postgres password=first_secret') == endpoint.receipt(
        'host=127.0.0.1 port=7000 dbname=postgres password=second_secret')


def test_crosshost_plan_requires_routable_peer_resources():
    raw = context('mmr'); desired = deepcopy(raw)
    desired['hosts']['remote'] = {'address': '192.168.0.15'}
    desired['instances']['p'].update(host='remote', data_dir='/data/remote')
    assert not diff_operations(raw, desired)[2]
    raw['hosts']['local']['address'] = '192.168.0.12'
    desired['hosts']['local']['address'] = '192.168.0.12'
    assert diff_operations(raw, desired)[2]


def test_unregistered_receiver_is_rejected_before_any_clone_or_fence(tmp_path):
    _, migration = fake_executor(tmp_path)
    migration.identity = lambda *args: '123'
    migration.logical_slots = lambda _: []
    migration.sql = lambda *args: json.dumps([{'name': 'manual_sub', 'database': 'postgres'}])
    with pytest.raises(ValueError, match='未纳入方案的逻辑订阅'):
        migration.preflight()


def test_receiver_quarantine_is_an_explicit_gate_before_endpoint_changes(tmp_path):
    _, migration = fake_executor(tmp_path)
    migration.state['receiver_worker_limit'] = 4
    migration.sql = lambda runtime,node,statement: '0'
    migration.check_receiver_quarantine()
    migration.sql = lambda runtime,node,statement: '0' if statement.startswith('SHOW') else '1'
    with pytest.raises(ValueError, match='未暂停'):
        migration.check_receiver_quarantine()
