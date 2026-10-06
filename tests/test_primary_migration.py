from copy import deepcopy
from types import SimpleNamespace

import pytest
from platform_app.deployment.change_execution import ChangeExecutor
from platform_app.deployment.primary_migration import PrimaryMigration, primary_cluster
from platform_app.deployment.workbench import diff_operations
from test_deployment_changes import config


def destination():
    raw = config()
    desired = deepcopy(raw)
    desired['instances']['p'].update(port=7100, data_dir='/data/moved')
    return raw, desired


def test_primary_migration_is_independent_and_has_explicit_shutdown_review():
    raw, desired = destination()
    assert primary_cluster(raw, desired, 'p') == 'stream'
    _, operations, executable = diff_operations(raw, desired)
    assert executable
    assert '关闭栅栏' in next(op['operation'] for op in operations if op['kind'] == 'change_node')
    desired['postgresql_config'] = {'parameters': {'work_mem': '16MB'}}
    assert not diff_operations(raw, desired)[2]


@pytest.mark.parametrize('section, topology', [
    ('mmr_clusters', {'members': {'a': {'streaming_cluster': 'stream'}}}),
    ('citus_clusters', {'coordinator': {'streaming_cluster': 'stream'}}),
    ('logical_replications', {'pub': {'streaming_cluster': 'stream'}}),
])
def test_primary_relocation_cannot_leave_replication_metadata_pointing_at_old_endpoint(section, topology):
    raw, desired = destination()
    raw[section] = {'cluster': topology}
    desired[section] = deepcopy(raw[section])
    assert primary_cluster(raw, desired, 'p') is None
    assert not diff_operations(raw, desired)[2]


def test_same_endpoint_fresh_directory_cannot_start_a_parallel_copy():
    raw, desired = destination()
    desired['instances']['p']['port'] = raw['instances']['p']['port']
    assert not diff_operations(raw, desired)[2]


def fake_executor(tmp_path, *, running=False, control_state='shut down'):
    raw, desired = destination()
    def runtime(value):
        return SimpleNamespace(config=SimpleNamespace(raw=value, instance=lambda node: {
            **value['instances'][node], 'host_config': {'address': '127.0.0.1'},
            'installation_config': {'home': '/opt/pgsql'},
        }), status_instance=lambda _: {'running': running})
    old, new = runtime(raw), runtime(desired)
    old.executor = SimpleNamespace(run=lambda *a, **k: SimpleNamespace(stdout=(
        f'Database cluster state: {control_state}\nDatabase system identifier: 123\n'
        'Latest checkpoint location: 0/ABCD\n')))
    e = ChangeExecutor({'id': 'plan'}, raw, new, old, tmp_path/'state.json')
    m = PrimaryMigration(e, 'p', 'stream')
    m.state['system_id'] = '123'
    return e, m


def test_shutdown_fence_uses_clean_shutdown_control_record(tmp_path):
    e, m = fake_executor(tmp_path)
    m.fence()
    assert e.state['primary_migrations']['p']['shutdown_lsn'] == '0/ABCD'
    _, m = fake_executor(tmp_path, control_state='in production')
    with pytest.raises(ValueError, match='正常关闭'):
        m.fence()


def test_fenced_source_restart_blocks_resume_before_any_new_step(tmp_path):
    e, m = fake_executor(tmp_path, running=True)
    e.state['completed'] = ['primary-move:p:shutdown-fence']
    with pytest.raises(ValueError, match='双主'):
        m.run()


def test_promotion_waits_for_final_wal_and_replays_interrupted_promotion(tmp_path):
    _, m = fake_executor(tmp_path)
    m.state['shutdown_lsn'] = '0/ABCD'
    calls = []
    m.sql = lambda *args: 't'
    m.identity = lambda *args: '123'
    m.wait_replay = lambda lsn: calls.append(('wait', lsn))
    m.new._promote = lambda node: calls.append(('promote', node))
    m.promote()
    assert calls == [('wait', '0/ABCD'), ('promote', 'p')]
    m.sql = lambda *args: 'f'
    m.promote()
    assert len(calls) == 2
    m.identity = lambda *args: 'different-system'
    with pytest.raises(ValueError, match='系统标识'):
        m.promote()
