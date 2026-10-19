"""MMR diagnostic truth must be separated from physical process health."""
import json
from types import SimpleNamespace
from copy import deepcopy
import pytest
from platform_app.deployment.mmr_status import collect, observe, aggregate


class Runtime:
    def __init__(self):
        self.calls=[]
        self.identity={'old':{'in_recovery':True,'installed':True},
                       'new':{'in_recovery':False,'installed':True},
                       'peer':{'in_recovery':False,'installed':True}}
        self.records=[{'nodeid':1,'nodename':'node1','nodestate':'ACTIVE','real_nodestate':'ACTIVE','is_abnormal':'OK','detail':'OK'},
                      {'nodeid':2,'nodename':'node2','nodestate':'ACTIVE','real_nodestate':'ACTIVE','is_abnormal':'OK','detail':'OK'}]
        self.local_states={'new':'ACTIVE','peer':'ACTIVE'}
        self.failed=set()
        self.config=SimpleNamespace(streaming_clusters={'g':{'primary':'old','standbys':[{'instance':'new'}]},
            'h':{'primary':'peer','standbys':[]}},mmr_clusters={'m':{'database':'postgres','group_name':'test',
            'members':{'node1':{'node_name':'node1','streaming_cluster':'g'},'node2':{'node_name':'node2','streaming_cluster':'h'}}}},
            instance=lambda n:{'host_config':{'address':'127.0.0.1'},'port':{'old':15001,'new':15002,'peer':15003}[n]})
        self.executor=SimpleNamespace(run=self.run)
    def _primary(self,g):return {'g':'old','h':'peer'}[g] # Deliberately stale role record.
    def _bin(self,node,_):return '/bin/'+node
    def _host(self,_):return 'local'
    def target_instances(self,_):return list(self.identity)
    def run(self,argv,**kwargs):
        sql=argv[-1];node=next(n for n in self.identity if '/bin/'+n in argv);self.calls.append((node,sql,argv))
        if node in self.failed:return SimpleNamespace(returncode=1,stdout='',stderr='connection refused')
        if "'in_recovery'" in sql:result=self.identity[node]
        elif "'local_id'" in sql:
            ident=1 if node=='new' else 2
            result={'local_id':ident,'nodes':[{'nodeid':r['nodeid'],'nodename':r['nodename'],
                'nodestate':self.local_states[node] if r['nodeid']==ident else 'ACTIVE'} for r in self.records[:2]]}
        else:
            assert not self.identity[node]['in_recovery'],'UDF must never execute on a physical standby'
            result=self.records
        return SimpleNamespace(returncode=0,stdout=json.dumps(result),stderr='')


def test_query_actual_primary_even_when_record_points_to_old_primary():
    r=Runtime();data=collect(r,'mmr.m')
    assert data['applicable'] and not data['unknown']
    assert data['issue_count']==0
    member=next(m for m in data['members'] if m['name']=='node1')
    assert member['state']=='ACTIVE' and member['source']['instance']=='new'
    udf_calls=[node for node,sql,_ in r.calls if 'show_node_info' in sql]
    assert udf_calls==['new','peer']
    assert all('PGOPTIONS=-c statement_timeout=5000 -c lock_timeout=1000' in argv for _,_,argv in r.calls)


def test_preserve_multiple_errors_and_each_observer_evidence():
    r=Runtime();r.records[0]['is_abnormal']='ERR_SUB_RUN';r.records[0]['detail']='subscriber is down'
    r.records.append({**r.records[0],'is_abnormal':'ERR_SLOT_STATE','detail':'slot inactive'})
    data=collect(r,'mmr.m');m=next(m for m in data['members'] if m['name']=='node1')
    assert m['state']=='ACTIVE' # ACTIVE can still have diagnostic faults.
    assert data['issue_count']==2
    assert len(m['issues'])==4 # Two errors observed by two sources.
    assert {i['observer'] for i in m['issues']}=={'new','peer'}
    assert all(o['evidence'][-1]['result'] for o in data['observations'])


def test_peer_cannot_make_unreachable_local_member_look_current():
    r=Runtime();r.failed={'new'}
    data=collect(r,'mmr.m');m=next(m for m in data['members'] if m['name']=='node1')
    assert data['unknown'] and m['state'] is None and m['unknown']
    assert '物理备库' in data['observations'][0]['error']
    assert not any(n=='old' and 'show_node_info' in sql for n,sql,_ in r.calls)


def test_two_writable_instances_do_not_pick_one_arbitrarily():
    r=Runtime();r.identity['old']['in_recovery']=False
    data=collect(r,'mmr.m')
    assert data['unknown']
    assert '多个可写主库' in data['observations'][0]['error']
    assert not any(n in {'new','old'} and 'show_node_info' in sql for n,sql,_ in r.calls)


def test_joining_member_shows_registered_state_and_does_not_call_active_only_udf():
    r=Runtime();r.local_states['new']='DATASYNC'
    data=collect(r,'mmr.m');m=next(m for m in data['members'] if m['name']=='node1')
    assert data['unknown'] and m['state'] is None
    assert 'DATASYNC' in m['registered_states']
    assert not any(n=='new' and 'show_node_info' in sql for n,sql,_ in r.calls)


def test_installed_uninitialized_is_not_physical_offline():
    r=Runtime();run=r.run
    def query(argv,**kwargs):
        if "'local_id'" in argv[-1]:return SimpleNamespace(returncode=0,stdout='{"local_id":null,"nodes":[]}',stderr='')
        return run(argv,**kwargs)
    r.executor.run=query
    o=observe(r,'g',['old','new'],'postgres')
    assert o['installed'] and o['source']['instance']=='new'
    assert '尚未初始化' in o['error']
    assert o['records']==[]


def test_observer_disagreement_keeps_states_separate():
    r=Runtime();data=collect(r,'mmr.m');observations=data['observations']
    observations[1]['records']=deepcopy(observations[1]['records'])
    observations[1]['records'][0]['real_nodestate']='CATCHUP'
    m=next(m for m in aggregate(observations) if m['name']=='node1')
    assert m['state_conflict'] and m['state'] is None
    assert set(m['states'])=={'ACTIVE','CATCHUP'}


def test_endpoint_returns_partial_diagnostics_but_checks_environment(tmp_path,monkeypatch):
    from platform_app.api import create_app
    from test_api import settings_for
    from fastapi.testclient import TestClient
    app=create_app(settings_for(tmp_path),enqueuer=lambda _:None);client=TestClient(app)
    app.state.store.environments.put_environment({'id':'lab','product_id':'fbasecman','title':'lab',
        'host':'127.0.0.1','port':15432,'database_name':'postgres','database_user':'postgres',
        'deployment_config':'/file','deployment_target':'mmr.m'})
    monkeypatch.setattr('platform_app.deployment.mmr_status.read',lambda *_:collect(Runtime(),'mmr.m'))
    assert client.get('/api/v1/environments/lab/mmr-status').json()['members'][0]['state']=='ACTIVE'
    assert client.get('/api/v1/environments/missing/mmr-status').status_code==404
