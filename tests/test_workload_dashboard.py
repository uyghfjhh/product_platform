import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from platform_app.monitoring import MonitoringService
from platform_app.workloads.dashboard import dashboard
from platform_app.workloads.sql import environment_fingerprint


def iso(value):
    return datetime.fromtimestamp(value, UTC).isoformat()


def setup(tmp_path):
    start = time.time() - 60
    environment = {'id': 'lab', 'product_id': 'demo', 'host': 'localhost', 'port': 1234}
    store = SimpleNamespace(environments=SimpleNamespace(get_environment=lambda identity: environment))
    service = MonitoringService(SimpleNamespace(data_dir=tmp_path), store)
    run = {'id': 'run', 'created_at': iso(start), 'updated_at': iso(start+50), 'status': 'SUCCEEDED',
           'environment_id': 'lab', 'tasks': [], 'task_details': [], 'definition': {
               'environment_id': 'lab', 'environment_fingerprint': environment_fingerprint(environment), 'monitoring_fingerprint': 'original',
               'environment': environment,
           }}
    return service, store, run, start


def sample(stamp, fingerprint='original', cpu=25):
    return {'observed_at': iso(stamp), 'fingerprint': fingerprint,
            'nodes': [{'node': {'id': 'primary', 'host': 'localhost', 'port': 1234},
                       'sections': {'runtime': {'valid': True, 'rows': [{'recovery': False, 'started_at': 'epoch'}]}}}],
            'hosts': [{'host': 'localhost', 'identity': 'host', 'valid': True, 'observed_at': iso(stamp),
                       'raw': {'boot_id': 'boot'}, 'metrics': {'cpu_percent': cpu, 'memory_percent': 40}}],
            'database_metrics': [{'node_id': 'primary', 'baseline': 'epoch', 'metrics': {'active': 2, 'blocked': None}}]}


def insert(service, identity, stamp, value):
    with service.connection() as connection:
        connection.execute('INSERT INTO samples VALUES (?,?,?,?)', (identity, stamp, value['fingerprint'], json.dumps(value)))


def test_dashboard_is_scoped_to_run_window_environment_and_fingerprint(tmp_path):
    service, store, run, start = setup(tmp_path)
    for identity, stamp, fingerprint in [('lab', start-1, 'original'), ('lab', start+10, 'original'),
                                         ('lab', start+20, 'changed'), ('other', start+30, 'original'),
                                         ('lab', start+51, 'original')]:
        insert(service, identity, stamp, sample(stamp, fingerprint))
    response = dashboard(service, run, store)
    assert response['run_id'] == 'run'
    assert response['sample_count'] == 1
    assert response['latest']['observed_at'] == iso(start+10)
    assert any(series['label'].endswith('cpu_percent') for series in response['series'])
    assert response['range']['end'] == iso(start+50)
    assert response['capabilities']['pool']['available'] is False
    assert response['capabilities']['prepared_statements']['available'] is False


def test_missing_metrics_are_not_zero_and_finished_run_does_not_use_current_sample(tmp_path):
    service, store, run, start = setup(tmp_path)
    insert(service, 'lab', start+10, sample(start+10, cpu=None))
    response = dashboard(service, run, store)
    assert not any(series['label'].endswith('cpu_percent') for series in response['series'])
    assert not any(series['label'].endswith('blocked') for series in response['series'])
    assert response['latest']['database_metrics'][0]['metrics']['blocked'] is None


def test_host_cache_is_deduplicated_and_before_run_cache_is_not_relabelled(tmp_path):
    service, store, run, start = setup(tmp_path)
    first = sample(start+10)
    second = sample(start+20)
    second['hosts'][0]['observed_at'] = iso(start+10)
    insert(service, 'lab', start+10, first)
    insert(service, 'lab', start+20, second)
    response = dashboard(service, run, store)
    cpu = next(series for series in response['series'] if series['label'].endswith('cpu_percent'))
    assert len(cpu['points']) == 1
    assert cpu['points'][0]['time'] == iso(start+10)
    older = sample(start+30)
    older['hosts'][0]['observed_at'] = iso(start-1)
    insert(service, 'lab', start+30, older)
    response = dashboard(service, run, store)
    assert response['latest']['hosts'][0]['valid'] is False


def test_old_run_without_monitoring_identity_never_uses_unrelated_history(tmp_path):
    service, store, run, start = setup(tmp_path)
    del run['definition']['monitoring_fingerprint']
    insert(service, 'lab', start+10, sample(start+10))
    response = dashboard(service, run, store)
    assert response['sample_count'] == 0
    assert response['latest'] is None
    assert response['notes']
