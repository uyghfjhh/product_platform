import json
import sys
import tempfile
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
    (path / 'metrics.json').write_text(json.dumps({
        'samples': [{'elapsed': 1, 'tps': 12, 'latency_ms': 1.5}, {'elapsed': 2, 'tps': 15, 'latency_ms': 1.3}],
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
