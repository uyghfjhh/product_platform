import json
from dataclasses import replace
from pathlib import Path

import pytest

from platform_app.config import load_settings
from platform_app.filestore import FileStore
from platform_app.workloads import WorkloadInput, command
from platform_app.workloads.workbench import PlanInput, Workbench
from products.fbasecman.test_settings import regression_snapshot, resolve
from products.fbasecman.workload_runtime import comparison, render_config


@pytest.fixture
def configured(tmp_path):
    settings = replace(load_settings(), data_dir=tmp_path/'data', output_dir=tmp_path/'output',
                       runtime_dir=tmp_path/'runtime')
    binary = tmp_path/'tested-fbasecman'
    binary.write_text('#!/bin/sh\nprintf "fbasecman test-build\\n"\n')
    binary.chmod(0o755)
    license_dir = tmp_path/'license'
    license_dir.mkdir()
    environment = {'id': 'lab', 'product_id': 'fbasecman', 'title': 'fixture',
                   'host': '127.0.0.1', 'port': 15011, 'database_name': 'postgres',
                   'database_user': 'postgres', 'deployment_target': None,
                   'product_test_settings': {'fbasecman_bin': str(binary), 'license_dir': str(license_dir)}}
    return settings, environment, binary


def test_explicit_build_overrides_hidden_environment_and_legacy_defaults(configured, monkeypatch):
    settings, environment, binary = configured
    monkeypatch.setenv('PRODUCT_PLATFORM_FBASECMAN_BIN', '/missing/hidden-build')
    value = resolve(settings, environment, inspect=True)
    assert value['resolved_path'] == str(binary)
    assert value['version'] == 'fbasecman test-build'
    snapshot = regression_snapshot(settings, environment)
    import yaml
    config = yaml.safe_load(Path(snapshot['regress_extra_configs'][0]).read_text())
    assert config['fbasecman']['fbasecman_bin'] == str(binary)
    assert snapshot['tested_build']['sha256'] == value['sha256']


def test_compare_requires_valid_phases_and_keeps_negative_changes():
    direct = {'status': 'PASS', 'summary': {'tps': 100, 'latency_ms': 2}}
    proxy = {'status': 'PASS', 'summary': {'tps': 80, 'latency_ms': 3}}
    value = comparison(direct, proxy)
    assert value['tps_loss_percent'] == 20
    assert value['latency_increase_percent'] == 50
    assert comparison(direct, {**proxy, 'status': 'FAIL'})['valid'] is False
    assert comparison(direct, {'status': 'PASS', 'summary': {'tps': 120, 'latency_ms': 1}})['tps_loss_percent'] == -20
    direct['summary']['latency_ms'] = 0
    assert comparison(direct, proxy)['latency_increase_percent'] is None


def test_review_counts_both_phases_and_rejects_changed_build(configured, monkeypatch):
    settings, environment, binary = configured
    monkeypatch.setattr('platform_app.workloads.shutil.which', lambda name: '/bin/true')
    store = FileStore(settings.data_dir)
    store.environments.put_environment(environment)
    class Orchestrator:
        def start(self, *_args, **_kwargs):
            pytest.fail('changed build must never reach submission')
    workbench = Workbench(settings, store, Orchestrator())
    catalog = workbench.catalog('lab')
    assert catalog['workloads'][0]['defaults']['connection_mode'] == 'proxy'
    plan = workbench.preview(PlanInput(environment_id='lab', workloads=[
        {'id': 'pgbench.connectivity', 'parameters': {'connection_mode': 'compare', 'duration_seconds': 5}}
    ]))
    assert plan['ready']
    assert plan['duration_seconds'] == 10
    binary.write_text(binary.read_text()+'# rebuilt\n')
    with pytest.raises(ValueError, match='构建文件已变化'):
        workbench.start(plan['id'], True)
    with pytest.raises(ValueError, match='构建文件已变化'):
        command(settings, environment, 'workload.pgbench', {
            **plan['steps'][0]['parameters'], '_workload_task_id': '00000000-0000-0000-0000-000000000000'})


def test_proxy_config_pins_the_registered_backend_and_owned_process(configured, tmp_path):
    settings, environment, _ = configured
    runtime = resolve(settings, environment)
    config = render_config(tmp_path, environment, runtime, 32001, '1234')
    assert 'daemonize no' in config and 'coroutine_stack_size 16' in config
    assert 'port 15011' in config and 'ports "32001"' in config
    assert 'group_mode "single"' in config
    assert 'storage_db "postgres"' in config
    assert 'rw_split_method "none"' in config


def test_environment_edits_preserve_product_build_selection(configured):
    settings, environment, _ = configured
    store = FileStore(settings.data_dir)
    store.environments.put_environment(environment)
    payload = {key: value for key, value in environment.items() if key != 'product_test_settings'}
    payload['title'] = 'renamed'
    updated = store.environments.update_environment('lab', payload)
    assert updated['product_test_settings'] == environment['product_test_settings']


def test_settings_api_is_shared_and_rejects_invalid_binary(configured):
    from fastapi.testclient import TestClient
    from platform_app.api import create_app
    settings, environment, binary = configured
    app = create_app(settings, enqueuer=lambda _identity: None)
    app.state.store.environments.put_environment(environment)
    client = TestClient(app)
    path = '/api/v1/environments/lab/fbasecman-test-settings'
    assert client.get(path).json()['fbasecman_bin'] == str(binary)
    response = client.put(path, json=environment['product_test_settings'])
    assert response.status_code == 200
    assert response.json()['source'] == '执行环境配置'
    assert client.get('/api/v1/environments').json()[0]['product_test_settings'] == environment['product_test_settings']
    assert client.put(path, json={**environment['product_test_settings'], 'fbasecman_bin': '/missing/binary'}).status_code == 422
    assert resolve(settings, app.state.store.environments.get_environment('lab'))['fbasecman_bin'] == str(binary)


def test_platform_sigterm_always_stops_its_worker(configured, monkeypatch):
    import signal
    from platform_app import cli
    settings, _, _ = configured
    stopped = []
    class Consumer:
        def terminate(self):
            stopped.append('terminate')
        def wait(self, timeout):
            stopped.append('wait')
    monkeypatch.setattr(cli, 'load_settings', lambda: settings)
    monkeypatch.setattr(cli, 'recover_unfinished', lambda _store: [])
    monkeypatch.setattr('platform_app.api.create_app', lambda *_args, **_kwargs: object())
    monkeypatch.setattr('platform_app.queue.create_queue', lambda *_args: (None, lambda _identity: None))
    monkeypatch.setattr(cli.subprocess, 'Popen', lambda *_args, **_kwargs: Consumer())
    previous = signal.getsignal(signal.SIGTERM)
    def shutdown(*_args, **_kwargs):
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    monkeypatch.setattr(cli.uvicorn, 'run', shutdown)
    monkeypatch.setattr('sys.argv', ['platform', 'start'])
    assert cli.main() == 0
    assert stopped == ['terminate', 'wait']
    assert signal.getsignal(signal.SIGTERM) == previous


def test_failed_comparison_report_never_displays_a_loss_percentage(tmp_path):
    from products.fbasecman.workload_runtime import write_report
    request = {'options': {'connection_mode': 'compare', 'clients': 2, 'duration_seconds': 5, 'script': 'SELECT 1;'}}
    result = {'status': 'FAIL', 'reason': 'backend identity mismatch',
              'comparison': {'valid': False, 'reason': '测量无效，不计算性能损失'}}
    write_report(tmp_path, result, request)
    report = (tmp_path/'report.md').read_text()
    assert '测量无效' in report
    assert '吞吐损失' not in report
    assert '未验证' in report


def test_multiple_builds_switch_and_snapshot_keep_original_selection(configured):
    from fastapi.testclient import TestClient
    from platform_app.api import create_app
    settings, environment, binary = configured
    alternate = binary.with_name('alternate')
    alternate.write_text('#!/bin/sh\nprintf "alternate build\\n"\n')
    alternate.chmod(0o755)
    app = create_app(settings, enqueuer=lambda _identity: None)
    app.state.store.environments.put_environment(environment)
    client = TestClient(app)
    path = '/api/v1/environments/lab/fbasecman-test-settings'
    assert client.get(path).json()['builds'][0]['name'] == '默认版本'
    payload = {'builds': [
        {'id': 'baseline', 'name': '基线', **environment['product_test_settings']},
        {'id': 'hint', 'name': 'Hint 修改版', **environment['product_test_settings'], 'fbasecman_bin': str(alternate)},
    ], 'active_build_id': 'baseline'}
    assert client.put(path, json=payload).status_code == 200
    snapshot = regression_snapshot(settings, app.state.store.environments.get_environment('lab'))
    payload['active_build_id'] = 'hint'
    response = client.put(path, json=payload)
    assert response.status_code == 200
    assert response.json()['build_name'] == 'Hint 修改版'
    assert response.json()['version'] == 'alternate build'
    assert snapshot['tested_build']['build_id'] == 'baseline'
    assert snapshot['fbasecman_bin'] == str(binary)
    assert client.get(path).json()['active_build_id'] == 'hint'
    assert client.put(path, json={**payload, 'active_build_id': 'missing'}).status_code == 422
    payload['builds'][1]['fbasecman_bin'] = '/missing/binary'
    assert client.put(path, json=payload).status_code == 422
    assert client.get(path).json()['fbasecman_bin'] == str(alternate)
    payload['builds'][1]['id'] = 'baseline'
    assert client.put(path, json=payload).status_code == 422
