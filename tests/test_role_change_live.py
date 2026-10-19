"""Opt-in three-node physical acceptance; never touches registered environments."""
import json
import os
from pathlib import Path
import subprocess
import sys
import yaml
import pytest
from test_deployment_live import lab
from platform_app.config import load_settings


@pytest.mark.parametrize('lab', ['PLATFORM_TEST_POSTGRES_HOME'], indirect=True)
def test_selected_standby_promote_rejoin_switchback_and_full_switchover(lab):
    lab.database('p')
    lab.sql('p','CREATE TABLE evidence(id int PRIMARY KEY); INSERT INTO evidence VALUES(42)')
    lab.database('s1',source='p');lab.database('s2',source='p')
    config=lab.root/'roles.yaml';config.write_text(yaml.safe_dump(lab.raw))
    for name in lab.raw['instances']:
        (lab.root/name/'.pgcluster-managed').write_text(json.dumps({'node':name}))
    runner=Path(__file__).resolve().parents[1]/'backend/platform_app/deployment/role_change.py'
    def run(action,selected,expected):
        result=subprocess.run([sys.executable,str(runner),'--engine-root',str(load_settings().pgcluster_root),
            '--config',str(config),'--group','p','--action',action,'--selected',selected,'--expected',expected],
            text=True,capture_output=True,timeout=150)
        (lab.root/(action.split('.')[-1]+'.log')).write_text(result.stdout+'\n'+result.stderr)
        assert result.returncode==0,result.stdout+'\n'+result.stderr
        assert 'ROLE_STEP ' in result.stdout and 'COMMAND [' in result.stdout
        assert 'SELECT pg_is_in_recovery()' in result.stdout and 'STDOUT:' in result.stdout
        assert 'EXIT_CODE: 0' in result.stdout and 'ROLE_RESULT ' in result.stdout
        return result.stdout
    run('deployment.failover','s2','p')
    assert lab.sql('s2','SELECT pg_is_in_recovery()')=='f'
    stopped=subprocess.run([lab.home/'bin/pg_ctl','status','-D',lab.root/'p'],capture_output=True)
    assert stopped.returncode==3
    assert lab.sql('s1','SELECT pg_is_in_recovery()')=='t'
    assert lab.sql('s2','SELECT id FROM evidence')=='42'
    assert not (lab.root/'rejoin.log').exists()
    run('deployment.rejoin','p','s2')
    assert lab.sql('p','SELECT pg_is_in_recovery()')=='t'
    assert lab.sql('p',"SELECT status FROM pg_stat_wal_receiver")=='streaming'
    assert lab.sql('s1',"SELECT status FROM pg_stat_wal_receiver")=='streaming'
    run('deployment.restore','p','s2')
    assert lab.sql('p','SELECT pg_is_in_recovery()')=='f'
    assert lab.sql('s2','SELECT pg_is_in_recovery()')=='t'
    run('deployment.switchover','s1','p')
    assert lab.sql('s1','SELECT pg_is_in_recovery()')=='f'
    assert lab.sql('p','SELECT pg_is_in_recovery()')=='t'
    assert lab.sql('s2','SELECT pg_is_in_recovery()')=='t'
    lab.sql('s1','INSERT INTO evidence VALUES(43)')
    lab.wait(lambda:lab.sql('p','SELECT count(*) FROM evidence')=='2' and lab.sql('s2','SELECT count(*) FROM evidence')=='2')

    # Submit through platform services as well: task logs and progress must be durable.
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from platform_app.api import create_app
    from platform_app.actions import run_task
    from test_api import settings_for
    settings=replace(settings_for(lab.root/'platform'),pgcluster_root=load_settings().pgcluster_root)
    app=create_app(settings,enqueuer=lambda identity:run_task(app.state.store,settings,identity))
    app.state.store.environments.put_environment({'id':'isolated-role-test','product_id':'fbase-database',
        'title':'isolated','host':'127.0.0.1','port':lab.raw['instances']['s1']['port'],
        'database_name':'postgres','database_user':'postgres','deployment_config':str(config),
        'deployment_target':'streaming.p'})
    client=TestClient(app)
    request={'environment_id':'isolated-role-test','action':'deployment.failover','target':'streaming.p',
        'parameters':{'selected_node':'s2','expected_primary':'s1'},'acknowledge_change':True}
    response=client.post('/api/v1/operations',json=request)
    assert response.status_code==202,response.text
    task=response.json()
    assert task['status']=='SUCCEEDED',task
    assert task['progress']['done']==task['progress']['total']
    events=client.get('/api/v1/operations/'+task['id']+'/events').json()
    output='\n'.join(event['payload'].get('line','') for event in events if event['event_type']=='command.output')
    assert 'ROLE_STEP ' in output and 'COMMAND [' in output and 'STDOUT:' in output

    # Interrupt after the old directory has been replaced by an incomplete clone.
    # Recovery must use our exact ownership checkpoint, not require another promotion.
    from pgclusterlib.config import load
    from pgclusterlib.runtime import Runtime
    from platform_app.deployment.role_change import execute
    runtime=Runtime(load(config))
    def incomplete_clone(group,source,name,slot):
        inst=runtime.config.instance(name)
        runtime.executor.remove_tree(runtime._host(inst),inst['data_dir'])
        runtime.executor.run(['mkdir','-p',inst['data_dir']])
        (Path(inst['data_dir'])/'incomplete').write_text('injected failed clone')
        raise RuntimeError('injected backup failure')
    runtime._rebuild_standby=incomplete_clone
    with pytest.raises(RuntimeError,match='injected backup failure'):
        execute(runtime,'deployment.rejoin','p','s1','s2',lambda _:None)
    assert runtime._state()['primaries']['p']=='s2'
    assert runtime._state()['role_rebuilds']['p']['phase']=='copying'
    response=client.post('/api/v1/operations',json={**request,'action':'deployment.rejoin',
        'parameters':{'selected_node':'s1','expected_primary':'s2'}})
    assert response.status_code==202,response.text
    assert response.json()['status']=='SUCCEEDED',response.json()
    assert lab.sql('s1','SELECT pg_is_in_recovery()')=='t'
    assert 'p' not in runtime._state().get('role_rebuilds',{})
