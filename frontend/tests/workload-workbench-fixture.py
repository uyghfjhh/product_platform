import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from dataclasses import replace
from pathlib import Path

import uvicorn
from fastapi import HTTPException
from platform_app.api import create_app
from platform_app.config import load_settings
from platform_app import workloads

root = Path(tempfile.mkdtemp(prefix='platform-workloads-browser-'))
settings = replace(load_settings(), data_dir=root / 'data', output_dir=root / 'output',
                   runtime_dir=root / 'runtime', logs_dir=root / 'logs')
workloads.shutil.which = lambda name: sys.executable
app = create_app(settings, enqueuer=lambda identity: None)
from platform_app.workloads import workbench as workbench_module
workbench_module.observation_fingerprint = lambda *_: 'browser-fixture'
app.state.monitoring.tick = lambda: None
store = app.state.store
store.environments.put_environment({
    'id': 'workload-browser', 'product_id': 'fbasecman', 'title': '负载浏览器环境',
    'host': '127.0.0.1', 'port': 17403, 'database_name': 'postgres',
    'database_user': 'postgres', 'deployment_target': 'mmr.fbasecman_regress',
})
store.bindings.put_regression_binding('fbasecman', 'cman', 'workload-browser')


@app.get('/api/v1/workload-browser-fixture')
def identity():
    return {'isolated': True, 'environment_id': 'workload-browser'}


app.router.routes.insert(0, app.router.routes.pop())


@app.post('/api/v1/workload-browser-fixture/finish/{task_id}')
def finish(task_id: str):
    task = store.tasks.get_task(task_id)
    if not task or task['environment_id'] != 'workload-browser':
        raise HTTPException(404, 'fixture task missing')
    store.tasks.transition_task(task_id, ('QUEUED',), 'RUNNING')
    status = 'CANCELLED' if task['cancel_requested'] else 'SUCCEEDED'
    path = settings.artifact_dir('fbasecman', 'workload-browser') / 'runs' / task_id / 'workload'
    path.mkdir(parents=True, exist_ok=True)
    now = time.time()
    stamps = [now-0.2, now-0.1]
    for index, stamp in enumerate(stamps):
        observed = datetime.fromtimestamp(stamp, UTC).isoformat()
        snapshot = {'observed_at': observed, 'fingerprint': 'browser-fixture',
                    'nodes': [{'node': {'id': 'fixture-primary', 'host': '127.0.0.1', 'port': 17403},
                               'sections': {'runtime': {'valid': True, 'rows': [{'recovery': False, 'started_at': 'fixture'}]}}}],
                    'hosts': [{'host': '127.0.0.1', 'identity': 'fixture-host', 'observed_at': observed, 'valid': True,
                               'raw': {'boot_id': 'fixture'}, 'metrics': {'cpu_percent': 20+index*10, 'memory_percent': 50, 'iowait_percent': 1}}],
                    'database_metrics': [{'node_id': 'fixture-primary', 'baseline': 'fixture', 'metrics': {'active': 4, 'instance_clients': 8, 'blocked': 0, 'commit_per_second': 12}}]}
        with app.state.monitoring.connection() as connection:
            connection.execute('INSERT INTO samples VALUES (?,?,?,?)', ('workload-browser', stamp, 'browser-fixture', json.dumps(snapshot)))
    (path / 'metrics.json').write_text(json.dumps({
        'samples': [{'elapsed': index+1, 'tps': 12+index*3, 'latency_ms': 1.5-index*0.2,
                     'observed_at': datetime.fromtimestamp(stamp, UTC).isoformat()} for index, stamp in enumerate(stamps)],
        'summary': {'tps': 13.5, 'latency_ms': 1.4},
    }))
    (path / 'result.json').write_text(json.dumps({
        'reason': 'isolated fixture', 'performance_verdict': 'NOT_CONFIGURED',
        'correctness_verdict': 'NOT_CONFIGURED',
        'checks': [{'name': 'fixture exit', 'expected': 0, 'actual': 0, 'passed': True}],
    }))
    return store.tasks.finish_task(task_id, ('RUNNING',), status, 'isolated fixture')


app.router.routes.insert(0, app.router.routes.pop())
print(str(root), flush=True)
uvicorn.run(app, host='127.0.0.1', port=18773, log_level='warning')
