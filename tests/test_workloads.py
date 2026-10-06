from dataclasses import replace

import pytest
from platform_app.config import load_settings
from platform_app.filestore import FileStore
from platform_app.workloads import WorkloadInput, publish
from pydantic import ValidationError


def test_workload_input_is_bounded_and_has_no_free_shell_or_sql():
    for values in [{'clients':129},{'duration_seconds':0},{'preset':'arbitrary sql'},{'command':'rm -rf'},{'sql':'DROP DATABASE postgres'}]:
        with pytest.raises(ValidationError):WorkloadInput.model_validate(values)


def test_cancelled_workload_does_not_publish_business_failure(tmp_path):
    settings=replace(load_settings(),data_dir=tmp_path/'data',output_dir=tmp_path/'output')
    store=FileStore(settings.data_dir)
    environment={'id':'lab','product_id':'demo','host':'localhost','port':7400,'title':'lab'}
    store.environments.put_environment(environment)
    task=store.tasks.create_task('lab','workload.pgbench','all',{},None)
    store.tasks.request_cancel(task['id'])
    publish(store,settings,environment,task['id'],False)
    result=store.results.get_result('demo','lab','workload.pgbench','workload')
    assert result['status']=='CANCELLED'
    assert '取消' in result['reason']
