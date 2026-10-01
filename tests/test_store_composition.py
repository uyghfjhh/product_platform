"""Cross-domain invariants use one commit backend after repository composition."""

import pytest
from platform_app.filestore import ConflictError, FileStore


def test_domains_share_one_backend_and_active_tasks_guard_relationships(tmp_path):
    store = FileStore(tmp_path / 'data')
    for domain in (store.environments, store.tasks, store.bindings, store.deployments,
                   store.results, store.diagnoses):
        assert domain.backend is store.backend
    store.environments.put_environment({'id': 'env', 'title': 'Env', 'product_id': 'demo'})
    store.bindings.put_regression_binding('demo', 'smoke', 'env')
    task = store.tasks.create_task('env', 'test', 'smoke.case', {}, None)
    with pytest.raises(ConflictError):
        store.environments.delete_environment('env')
    with pytest.raises(ConflictError):
        store.bindings.delete_regression_binding('demo', 'smoke')
    store.tasks.finish_task(task['id'], ('QUEUED',), 'SUCCEEDED', 'done')
    assert store.environments.delete_environment('env')
    assert store.bindings.get_regression_binding('demo', 'smoke') is None


def test_diagnosis_becomes_stale_when_result_domain_commits_new_facts(tmp_path, monkeypatch):
    store = FileStore(tmp_path / 'data')
    store.results.put_result('demo', 'env', 'smoke.case', 'default', 'FAIL', 'first', None)
    result = store.results.get_result('demo', 'env', 'smoke.case')
    store.diagnoses.put_diagnosis(result, 'hash', 'model', {'facts': []})
    assert not store.diagnoses.get_diagnosis('demo', 'env', 'smoke.case')['stale']
    monkeypatch.setattr('platform_app.storage.results.now', lambda: '2100-01-01T00:00:00+00:00')
    store.results.put_result('demo', 'env', 'smoke.case', 'default', 'PASS', 'fixed', None)
    assert store.diagnoses.get_diagnosis('demo', 'env', 'smoke.case')['stale']
