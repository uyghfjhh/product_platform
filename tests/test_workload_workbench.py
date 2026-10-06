from dataclasses import replace
import sys

import pytest
from pydantic import ValidationError

from platform_app.config import load_settings
from platform_app.filestore import FileStore
from platform_app.operations import OperationService
from platform_app.orchestration import Orchestrator
from platform_app.workloads import WorkloadInput
from platform_app.workloads.workbench import Workbench, PlanInput


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    settings = replace(load_settings(), data_dir=tmp_path / 'data',
                       output_dir=tmp_path / 'output', runtime_dir=tmp_path / 'runtime')
    store = FileStore(settings.data_dir)
    store.environments.put_environment({
        'id': 'lab', 'product_id': 'fbase-database', 'title': 'fixture',
        'host': '127.0.0.1', 'port': 7400, 'database_name': 'postgres',
        'database_user': 'postgres', 'deployment_target': 'fixture',
    })
    monkeypatch.setattr('platform_app.workloads.shutil.which', lambda name: sys.executable)
    operations = OperationService(settings, store, lambda identity: None)
    return Workbench(settings, store, Orchestrator(store, operations))


def request(**kwargs):
    return PlanInput(environment_id='lab', workloads=[
        {'id': 'pgbench.connectivity', 'parameters': {'clients': 2, 'duration_seconds': 5}},
        {'id': 'pgbench.catalog', 'parameters': {'clients': 3, 'duration_seconds': 7}},
    ], **kwargs)


def test_review_snapshots_scripts_and_does_not_start_tasks(workbench):
    plan = workbench.preview(request())
    assert plan['ready']
    assert plan['duration_seconds'] == 12
    assert plan['peak_clients'] == 3
    assert len(plan['steps']) == 2
    assert plan['steps'][0]['parameters']['script'] == 'SELECT 1;'
    assert workbench.store.tasks.list_tasks() == []


def test_selection_is_unique_and_parallel_is_not_silently_sequential(workbench):
    with pytest.raises(ValueError):
        workbench.preview(PlanInput(environment_id='lab', workloads=[
            {'id': 'pgbench.connectivity'}, {'id': 'pgbench.connectivity'},
        ]))
    with pytest.raises(ValidationError):
        request(mode='parallel')


def test_stale_environment_blocks_submission(workbench):
    plan = workbench.preview(request())
    environment = workbench.store.environments.get_environment('lab')
    environment['port'] = 7401
    workbench.store.environments.update_environment('lab', environment)
    with pytest.raises(ValueError, match='环境.*变化'):
        workbench.start(plan['id'], True)
    assert workbench.store.orchestration.list('runs') == []


def test_review_is_idempotent_and_cancel_stops_next_workload(workbench):
    plan = workbench.preview(request())
    with pytest.raises(ValueError):
        workbench.start(plan['id'], False)
    first = workbench.start(plan['id'], True)
    assert workbench.start(plan['id'], True)['id'] == first['id']
    workbench.orchestrator.runs()
    run = workbench.run(first['id'])
    assert len(run['tasks']) == 1
    workbench.orchestrator.cancel(run['id'])
    task = workbench.store.tasks.get_task(run['tasks'][0])
    assert task['cancel_requested']
    workbench.store.tasks.transition_task(task['id'], ('QUEUED',), 'RUNNING')
    workbench.store.tasks.finish_task(task['id'], ('RUNNING',), 'CANCELLED', 'fixture')
    workbench.orchestrator.runs()
    assert workbench.run(run['id'])['status'] == 'CANCELLED'
    assert len(workbench.store.tasks.list_tasks()) == 1


@pytest.mark.parametrize('script', [
    'DROP TABLE test;', 'SELECT 1; COMMIT;', '\\shell touch /tmp/nope',
    'SELECT 1 INTO new_table;', 'WITH x AS (DELETE FROM test RETURNING *) SELECT * FROM x;',
    'SELECT * FROM test FOR UPDATE;',
])
def test_custom_scripts_reject_writes_transaction_control_and_pgbench_shell(script):
    with pytest.raises(ValidationError):
        WorkloadInput(script=script)


def test_select_script_accepts_real_postgres_syntax():
    values = WorkloadInput(script="WITH x AS (SELECT ';' AS value) SELECT * FROM x;", jobs=2)
    assert values.script.startswith('WITH')


def test_smoke_uses_bounded_overrides_without_mutating_defaults(workbench):
    plan = workbench.preview(request(smoke=True))
    assert all(step['parameters']['clients'] == 1 for step in plan['steps'])
    assert all(step['parameters']['duration_seconds'] <= 10 for step in plan['steps'])
    assert workbench.catalog('lab')['workloads'][0]['defaults']['clients'] == 4


def test_altered_plan_cannot_execute(workbench):
    plan = workbench.preview(request())
    plan['steps'][0]['parameters']['clients'] = 30
    workbench.store.orchestration.put('pipelines', plan, plan['id'])
    with pytest.raises(ValueError, match='修改'):
        workbench.start(plan['id'], True)


def test_runtime_rechecks_environment_before_creating_artifacts(workbench):
    from platform_app.workloads import command
    plan = workbench.preview(request())
    environment = workbench.environment('lab')
    environment['port'] += 1
    with pytest.raises(ValueError, match='环境.*变化'):
        command(workbench.settings, environment, 'workload.pgbench', {
            **plan['steps'][0]['parameters'], '_workload_task_id': '00000000-0000-0000-0000-000000000000',
        })
    assert not workbench.settings.output_dir.exists()


def test_http_catalog_review_and_pipeline_routes_cannot_bypass_review(workbench):
    from fastapi.testclient import TestClient
    from platform_app.api import create_app
    client = TestClient(create_app(workbench.settings, enqueuer=lambda identity: None))
    assert client.get('/api/v1/environments/lab/workloads').status_code == 200
    response = client.post('/api/v1/workload-plans', json=request().model_dump())
    assert response.status_code == 200
    plan = response.json()
    assert client.post(f"/api/v1/workload-plans/{plan['id']}/run", json={}).status_code == 422
    assert client.put(f"/api/v1/pipelines/{plan['id']}", json={
        'title': 'altered', 'environment_id': 'lab', 'steps': plan['steps'],
    }).status_code == 409
    environment = workbench.environment('lab')
    environment['port'] += 1
    workbench.store.environments.update_environment('lab', environment)
    assert client.post(f"/api/v1/pipelines/{plan['id']}/run", json={'acknowledge_change': True}).status_code == 422
    assert client.get('/api/v1/pipelines').json() == []


def test_pgbench_runner_uses_actual_script_parameters_and_reports_missing_thresholds(tmp_path, monkeypatch):
    import json
    from platform_app.workloads import runner
    output = tmp_path / 'output'
    output.mkdir()
    options = WorkloadInput(script='SELECT 42 -- trailing comment', jobs=2,
                            target_tps=30, connect_per_transaction=True).model_dump()
    path = tmp_path / 'request.json'
    path.write_text(json.dumps({
        'output': str(output), 'options': options, 'driver': 'pgbench',
        'environment': {'host': 'fixture', 'port': 1234, 'database_user': 'user', 'database_name': 'db'},
        'pgbench': '/fixture/pgbench', 'execution_id': 'fixture',
    }))
    captured = []

    class Process:
        stdout = ['progress: 1.0 s, 30.0 tps, lat 1.5 ms\n', 'tps = 30.0\n',
                  'latency average = 1.5 ms\n', 'number of failed transactions: 0\n']

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(runner.subprocess, 'Popen', lambda command, **kwargs: (captured.append((command, kwargs)) or Process()))
    assert runner.main(path) == 0
    command = captured[0][0]
    assert '-C' in command
    assert command[command.index('-j') + 1] == '2'
    assert command[command.index('-R') + 1] == '30.0'
    assert (output / 'workload.sql').read_text() == 'BEGIN READ ONLY;\nSELECT 42;\nCOMMIT;\n'
    result = json.loads((output / 'result.json').read_text())
    assert result['performance_verdict'] == 'NOT_CONFIGURED'
    assert result['correctness_verdict'] == 'NOT_CONFIGURED'
    assert all(check['passed'] for check in result['checks'])


def test_pgbench_metadata_hidden_in_sql_string_is_rejected():
    with pytest.raises(ValidationError):
        WorkloadInput(script='SELECT $$text\n\\shell touch /tmp/nope\n$$;')


def test_jdbc_client_compiles_without_connecting_to_database(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path
    import platform_app.workloads
    if not shutil.which('javac'):
        pytest.skip('JDK is not available')
    source = Path(platform_app.workloads.__file__).with_name('JdbcLoad.java')
    subprocess.run(['javac', '-d', str(tmp_path), str(source)], check=True)
    assert (tmp_path / 'JdbcLoad.class').is_file()
