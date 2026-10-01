"""Crash recovery, resource ownership and public runtime boundaries."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from platform_app.api import create_app
from platform_app.deployment.service import DeploymentService
from platform_app.filestore import ConflictError, FileStore
from platform_app.operations import OperationError, OperationRequest, OperationService
from platform_app.providers import provider_for, registry_for
from platform_app.queue import create_queue
from platform_app.resources import resource_lock, validate_registration
from test_api import settings_for
from test_deployment_workbench import create_plan, workbench  # noqa: F401


def test_imports_have_no_runtime_filesystem_side_effects(tmp_path):
    data = tmp_path / 'not-created'
    env = {**os.environ, 'PRODUCT_PLATFORM_DATA_DIR': str(data)}
    subprocess.run([sys.executable, '-c',
                    'import platform_app.api, platform_app.queue, platform_app.worker'],
                   env=env, check=True)
    assert not data.exists()


def test_queues_use_the_exact_supplied_data_directory(tmp_path):
    first, enqueue_first = create_queue(settings_for(tmp_path / 'a'))
    second, _enqueue_second = create_queue(settings_for(tmp_path / 'b'))
    enqueue_first('task-a')
    assert first.pending_count() == 1
    assert second.pending_count() == 0


def test_api_submission_is_consumed_by_an_independent_worker_queue(tmp_path):
    settings = settings_for(tmp_path)
    app = create_app(settings)
    store = app.state.store
    store.environments.put_environment({'id': 'demo-worker', 'title': 'Demo', 'product_id': 'demo',
                           'host': 'localhost', 'port': 5432, 'deployment_target': 'demo.local'})
    store.bindings.put_regression_binding('demo', 'smoke', 'demo-worker')
    task = app.state.operations.submit(OperationRequest(
        environment_id='demo-worker', action='tests.demo', target='smoke.context'))
    worker_queue, _execute = create_queue(settings)
    queued = worker_queue.dequeue()
    assert queued is not None
    worker_queue.execute(queued)
    assert store.tasks.get_task(task['id'])['status'] == 'SUCCEEDED'
    assert store.results.get_result('demo', 'demo-worker', 'smoke.context')['status'] == 'PASS'


def test_dispatch_failure_is_durable_and_retried_without_duplicate_task(tmp_path):
    settings = settings_for(tmp_path)
    def fail(_task_id):
        raise OSError('queue temporarily unavailable')
    app = create_app(settings, enqueuer=fail)
    store = app.state.store
    store.environments.put_environment({'id': 'demo-env', 'title': 'Demo', 'product_id': 'demo',
                           'host': 'localhost', 'port': 5432, 'deployment_target': 'demo.local'})
    store.bindings.put_regression_binding('demo', 'smoke', 'demo-env')
    request = OperationRequest(environment_id='demo-env', action='tests.demo',
                               target='smoke.context', submission_key='once')
    with pytest.raises(OperationError) as failure:
        app.state.operations.submit(request)
    assert failure.value.status_code == 503
    task = store.tasks.list_tasks()[0]
    assert task['status'] == 'QUEUED' and task['dispatch_pending']
    queued = []
    restarted = create_app(settings, enqueuer=queued.append)
    restarted.state.operations.retry_dispatch()
    restarted.state.operations.submit(request)
    assert queued == [task['id']]
    assert len(store.tasks.list_tasks()) == 1
    assert not store.tasks.get_task(task['id'])['dispatch_pending']


def test_unclaimed_delivery_is_retried_after_its_lease(tmp_path):
    settings = settings_for(tmp_path)
    store = FileStore(settings.data_dir, runtime_dir=settings.runtime_dir, logs_dir=settings.logs_dir)
    task = store.tasks.create_task('env', 'test', 'target', {}, None)
    store.tasks.record_dispatch(task['id'], pending=False)
    row = store.tasks.get_task(task['id'])
    row['last_dispatched_at'] = '2000-01-01T00:00:00+00:00'
    store.tasks._write_task(row)
    queued = []
    service = OperationService(settings, store, queued.append)
    service.retry_dispatch()
    service.retry_dispatch()
    assert queued == [task['id']]
    assert len(store.tasks.list_tasks()) == 1


class SimulatedCrash(BaseException):
    pass


@pytest.mark.parametrize('crash_after_task', [False, True])
def test_deployment_recovers_association_and_submission_gaps(workbench, monkeypatch, crash_after_task):  # noqa: F811
    _client, work, _spec, queued = workbench
    _draft, plan = create_plan(workbench)
    operations = OperationService(work.settings, work.store, queued.append)
    service = DeploymentService(work.settings, work.store, operations)
    with monkeypatch.context() as patch:
        if crash_after_task:
            original = work.store.deployments.update_deployment_request
            def crash_on_submission(request_id, **changes):
                if changes.get('state') == 'SUBMITTED':
                    raise SimulatedCrash()
                return original(request_id, **changes)
            patch.setattr(work.store.deployments, 'update_deployment_request', crash_on_submission)
        else:
            patch.setattr(operations, 'submit', lambda *_: (_ for _ in ()).throw(SimulatedCrash()))
        with pytest.raises(SimulatedCrash):
            service.apply(plan['id'], True)
    # Recover the same request, including a durable task created before its
    # linkage was recorded. No new deployment attempt or approval is invented.
    service.recover()
    service.recover()
    rows = work.store.deployments.deployment_requests()
    assert len(rows) == 1 and rows[0]['state'] == 'SUBMITTED'
    assert queued == [rows[0]['task_id']]
    environment = work.store.environments.get_environment(plan['environment_id'])
    assert environment['desired_deployment_plan_id'] == plan['id']
    assert environment['deployment_status'] == 'PENDING'
    assert not environment.get('applied_deployment_plan_id')


def test_recovery_rejects_a_reviewed_plan_whose_draft_changed(workbench):  # noqa: F811
    _client, work, spec, queued = workbench
    draft, plan = create_plan(workbench)
    work.store.deployments.begin_deployment_request(plan['id'], draft['id'], True)
    from platform_app.deployment.models import DeploymentSpec
    work.save(draft['id'], DeploymentSpec(**{**spec, 'title': 'changed'}), draft['revision'])
    DeploymentService(work.settings, work.store, OperationService(work.settings, work.store, queued.append)).recover()
    assert work.store.deployments.deployment_requests()[0]['state'] == 'REJECTED'
    assert not queued and work.store.environments.get_environment(draft['id']) is None


def cluster_environment(tmp_path, name, directory, port, host='localhost'):
    config = tmp_path / (name + '.yaml')
    config.write_text(yaml.safe_dump({'hosts': {'h': {'address': host}}, 'instances': {
        'p': {'host': 'h', 'port': port, 'data_dir': str(directory)},
    }}))
    return {'id': name, 'product_id': 'demo', 'title': name, 'host': host,
            'port': port, 'deployment_config': str(config)}


def test_resource_locks_cover_aliases_nested_directories_and_allow_siblings(tmp_path):
    settings = settings_for(tmp_path)
    root = tmp_path / 'database'
    first = cluster_environment(tmp_path, 'a', root, 15001)
    nested = cluster_environment(tmp_path, 'b', root / 'nested', 15002, '127.0.0.1')
    sibling = cluster_environment(tmp_path, 'c', tmp_path / 'other', 15003)
    with resource_lock(settings, first):
        with pytest.raises(ConflictError):
            with resource_lock(settings, nested):
                pass
        with resource_lock(settings, sibling):
            pass
    store = FileStore(settings.data_dir, runtime_dir=settings.runtime_dir, logs_dir=settings.logs_dir)
    store.environments.put_environment(first, validator=validate_registration)
    with pytest.raises(ConflictError):
        store.environments.put_environment(nested, validator=validate_registration)


def test_provider_registry_reuses_refreshes_and_removes_instances(tmp_path, monkeypatch):
    from platform_app.config import Settings
    package = tmp_path / 'products' / 'demo'
    package.mkdir(parents=True)
    manifest = package / 'product.yaml'
    manifest.write_text('id: demo\ntitle: Demo\nplugin_api: v1\nprovider: provider.py\n')
    source = '''from platform_app.providers import CommandSpec
class Provider:
    def command(self, *args): return CommandSpec(['true'], None)
    def discover(self, *args): return []
    def observe_database(self, *args): return []
    def observe_runtime(self, *args): return []
    def validate_target(self, *args): return True
PROVIDER = Provider()
'''
    path = package / 'provider.py'
    path.write_text(source)
    monkeypatch.setattr(Settings, 'products_root', property(lambda _self: package.parent))
    settings = settings_for(tmp_path)
    first = provider_for(settings, 'demo')
    assert first is provider_for(settings, 'demo')
    path.write_text(source.replace("['true']", "['false']"))
    second = provider_for(settings, 'demo')
    assert second is not first and second.command().command == ['false']
    registry_for(settings).refresh()
    assert provider_for(settings, 'demo') is not second
    manifest.unlink()
    with pytest.raises(ValueError, match='产品未安装'):
        provider_for(settings, 'demo')


def test_segmented_events_pagination_and_archive_preserve_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(__import__('platform_app.storage.tasks', fromlist=['TasksStore']).TasksStore, 'EVENT_SEGMENT_SIZE', 3)
    store = FileStore(tmp_path)
    tasks = [store.tasks.create_task('env', 'test', str(index), {}, 'key-' + str(index)) for index in range(4)]
    assert [t['id'] for t in store.tasks.list_tasks(2)] == [tasks[3]['id'], tasks[2]['id']]
    assert [t['id'] for t in store.tasks.list_tasks(2, before=tasks[2]['id'])] == [tasks[1]['id'], tasks[0]['id']]
    task = tasks[0]
    for n in range(8):
        store.tasks.add_event(task['id'], 'sample', {'n': n})
    assert len(list((tmp_path / 'tasks' / task['id']).glob('events*.jsonl'))) == 3
    assert [e['sequence'] for e in store.tasks.list_events(task['id'], 4)] == [5, 6, 7, 8]
    store.tasks.finish_task(task['id'], ('QUEUED',), 'SUCCEEDED', 'done')
    row = store.tasks.get_task(task['id'])
    row['finished_at'] = '2000-01-01T00:00:00+00:00'
    store.tasks._write_task(row)
    assert store.tasks.archive_tasks(30) == [task['id']]
    restarted = FileStore(tmp_path)
    assert restarted.tasks.get_task(task['id'])['status'] == 'SUCCEEDED'
    duplicate, created = restarted.tasks.create_task_once('env', 'test', '0', {}, 'key-0')
    assert not created and duplicate['id'] == task['id']
    assert len(restarted.tasks.list_events(task['id'])) == 9


def test_generated_frontend_types_match_response_contracts(tmp_path):
    from platform_app.export_contracts import render_contracts
    target = tmp_path / 'contracts.ts'
    subprocess.run([sys.executable, '-m', 'platform_app.export_contracts', '--output', str(target)], check=True)
    assert target.read_text() == render_contracts()
