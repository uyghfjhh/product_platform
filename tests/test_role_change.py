"""Role mutations must honor selected node and fail before touching wrong hosts."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from platform_app.deployment.role_change import execute, snapshot, validate_request


class Runtime:
    def __init__(self):
        self.roles = {'pg_1':False, 'pg_3':True, 'pg_5':True}
        self.state = {'primaries': {}}
        self.calls = []
        self.executor = SimpleNamespace(run=lambda *a,**k:SimpleNamespace(returncode=0),
            write_text=lambda *a,**k:self.calls.append(('claim',a)))
        self.config = SimpleNamespace(streaming_clusters={'g': {'primary':'pg_1', 'standbys':[{'instance':'pg_3'},{'instance':'pg_5'}]}},
            mmr_clusters={}, instance=lambda n:{'host_config':{'address':'localhost'},'port':{'pg_1':19001,'pg_3':19003,'pg_5':19005}[n],'data_dir':'/data/'+n})
    def _host(self,inst):return 'local'
    def _marker(self,n):return '/data/'+n+'/.pgcluster-managed'
    def status_instance(self,n):return {'running': self.roles[n] is not None}
    def _state(self):return deepcopy(self.state)
    def _save_state(self,s):self.state=deepcopy(s)
    def _primary(self,g):return self.state['primaries'].get(g,'pg_1')
    def _managed(self,n):return True
    def _live_role(self,n):return self.roles[n]
    def _psql(self,n,q,**kw):
        self.calls.append(('sql',n,q))
        if 'pg_is_in_recovery()' in q:return 't' if self.roles[n] else 'f'
        if 'system_identifier' in q:return '1234'
        if 'pg_current_wal_flush_lsn' in q or 'pg_last_wal_receive_lsn' in q:return '0/100'
        if 'pg_last_wal_replay_lsn' in q:return 't'
        if 'pg_stat_wal_receiver' in q:return 'streaming'
        return 't'
    def stop_instance(self,n):self.calls.append(('stop',n));self.roles[n]=None
    def _promote(self,n):self.calls.append(('promote',n));self.roles[n]=False
    def _standby_slot(self,g,n):return n+'_slot'
    def _rebuild_standby(self,g,p,n,slot):self.calls.append(('rebuild',n,p));self.roles[n]=True
    def _write_upstream(self,g,n,p):self.calls.append(('upstream',n,p))
    def _upstream_matches(self,g,n,p):return True


def test_selected_one_of_two_standbys_is_the_only_promoted_node():
    r=Runtime();events=[]
    result=execute(r,'deployment.failover','g','pg_5','pg_1',events.append)
    assert ('promote','pg_5') in r.calls
    assert ('promote','pg_3') not in r.calls
    assert not any(c[0]=='rebuild' for c in r.calls)
    assert r.roles=={'pg_1':None,'pg_3':True,'pg_5':False}
    assert result['old_primary_state']=='stopped'
    assert events[-1].startswith('[6/6]')


@pytest.mark.parametrize('selected,expected', [('other','pg_1'),('pg_1','pg_1'),('pg_3','pg_5')])
def test_invalid_selection_or_stale_primary_never_stops_a_node(selected,expected):
    r=Runtime()
    with pytest.raises(ValueError):execute(r,'deployment.failover','g',selected,expected,lambda _:None)
    assert not any(c[0] in {'stop','promote','rebuild'} for c in r.calls)


def test_switchover_rebuilds_old_primary_and_repoints_remaining_standby():
    r=Runtime()
    execute(r,'deployment.switchover','g','pg_5','pg_1',lambda _:None)
    assert r.calls.index(('stop','pg_1'))<r.calls.index(('promote','pg_5'))<r.calls.index(('rebuild','pg_1','pg_5'))
    assert ('upstream','pg_3','pg_5') in r.calls
    assert r.roles=={'pg_1':True,'pg_3':True,'pg_5':False}


def test_rebuild_failure_preserves_new_primary_and_can_resume_without_promotion():
    r=Runtime();original=r._rebuild_standby
    def failed(*_):raise RuntimeError('backup failed')
    r._rebuild_standby=failed
    with pytest.raises(RuntimeError,match='backup failed'):
        execute(r,'deployment.switchover','g','pg_5','pg_1',lambda _:None)
    assert r.state['primaries']['g']=='pg_5'
    assert not r.state['failovers']['g']['rejoined']
    r._rebuild_standby=original
    execute(r,'deployment.rejoin','g','pg_1','pg_5',lambda _:None)
    assert len([c for c in r.calls if c[0]=='promote'])==1
    assert r.state['failovers']['g']['rejoined']


def test_rejoin_cannot_destroy_the_current_primary():
    r=Runtime()
    with pytest.raises(ValueError):execute(r,'deployment.rejoin','g','pg_1','pg_1',lambda _:None)
    assert not any(c[0]=='rebuild' for c in r.calls)


def test_two_actual_primaries_are_rejected():
    r=Runtime();r.roles['pg_3']=False
    with pytest.raises(ValueError,match='多个可写主库'):snapshot(r,'g')


def test_switchback_only_accepts_original_primary_and_rebuilds_current():
    r=Runtime();execute(r,'deployment.switchover','g','pg_5','pg_1',lambda _:None)
    with pytest.raises(ValueError,match='原主库'):
        execute(r,'deployment.restore','g','pg_3','pg_5',lambda _:None)
    execute(r,'deployment.restore','g','pg_1','pg_5',lambda _:None)
    assert r.roles=={'pg_1':False,'pg_3':True,'pg_5':True}
    assert ('rebuild','pg_5','pg_1') in r.calls


def test_api_validates_selection_and_keeps_duplicate_role_task_locked(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from platform_app.api import create_app
    from test_api import settings_for
    settings=settings_for(tmp_path);app=create_app(settings,enqueuer=lambda _:None);client=TestClient(app)
    app.state.store.environments.put_environment({'id':'lab','product_id':'fbasecman','title':'lab','host':'127.0.0.1','port':19001,
        'database_name':'postgres','database_user':'postgres','deployment_target':'mmr.g','deployment_config':'/config'})
    r=Runtime();info=snapshot(r,'g')
    monkeypatch.setattr('platform_app.operations.configured_topology',lambda *_:{'nodes':[{'id':n['id'],'group':'g'} for n in info['nodes']]})
    monkeypatch.setattr('platform_app.deployment.role_change.preview',lambda *_:info)
    request={'environment_id':'lab','action':'deployment.switchover','target':'streaming.g','acknowledge_change':True}
    assert client.post('/api/v1/operations',json=request).status_code==422
    request['parameters']={'selected_node':'pg_5','expected_primary':'pg_1'}
    first=client.post('/api/v1/operations',json=request)
    assert first.status_code==202,first.text
    assert first.json()['parameters']['selected_node']=='pg_5'
    assert client.post('/api/v1/operations',json=request).status_code==409


def test_role_command_uses_selected_target_instead_of_engine_candidate_selection(tmp_path):
    from test_api import settings_for
    from platform_app.providers import command_for
    command=command_for(settings_for(tmp_path),{'id':'lab','product_id':'fbasecman','deployment_config':'/config'},
                        'deployment.failover','streaming.g',{'selected_node':'pg_5','expected_primary':'pg_1'}).command
    assert command[command.index('--selected')+1]=='pg_5'
    assert command[command.index('--expected')+1]=='pg_1'
    assert any(part.endswith('role_change.py') for part in command)


def test_unreachable_old_primary_is_not_treated_as_stopped():
    r=Runtime();status=r.status_instance
    r.status_instance=lambda n:{'running':False,'known':False} if n=='pg_1' else status(n)
    with pytest.raises(ValueError,match='状态未知'):
        execute(r,'deployment.failover','g','pg_5','pg_1',lambda _:None)
    assert not any(c[0] in {'stop','promote'} for c in r.calls)


def test_partial_clone_claim_cannot_authorize_a_different_data_directory():
    from platform_app.deployment.role_change import partial_rebuild_claim
    r=Runtime();r.state['primaries']['g']='pg_5'
    r.state['role_rebuilds']={'g':{'node':'pg_1','source':'pg_5','phase':'copying',
        'host':'localhost','data_dir':'/some-other-directory'}}
    assert not partial_rebuild_claim(r,'g','pg_1')


def test_existing_mmr_abnormality_stops_before_physical_mutations():
    r=Runtime()
    r.config.mmr_clusters={'mmr':{'members':{'node1':{'streaming_cluster':'g','node_name':'node1'}}}}
    def verify(_):raise RuntimeError('ERR_NODE_SET_MODE')
    r.verify_target=verify
    with pytest.raises(ValueError,match='尚未停止旧主'):
        execute(r,'deployment.switchover','g','pg_5','pg_1',lambda _:None)
    assert not any(c[0] in {'stop','promote','rebuild'} for c in r.calls)


def test_mmr_endpoint_update_changes_each_writable_observer_without_logging_password():
    from platform_app.deployment.role_change import update_mmr_endpoints
    r=Runtime();instance=r.config.instance
    r.config.instance=lambda n:{'host_config':{'address':'remote-db'},'port':29001,'data_dir':'/data/peer'} if n=='peer' else instance(n)
    r.config.mmr_clusters={'mmr':{'database':'postgres','members':{
        'node1':{'streaming_cluster':'g','node_name':'node1'},
        'node2':{'streaming_cluster':'h','node_name':'node2'}}}}
    r._primary=lambda group:'pg_5' if group=='g' else 'peer'
    r.executor.is_local=lambda host:host in {'localhost','127.0.0.1'}
    dsns={n:'host=localhost port=19001 dbname=postgres user=postgres password=hidden' for n in ['pg_5','peer']}
    commands=[]
    def sql(node,query,*args):
        if query.startswith('SELECT node_dsn'):return dsns[node]
        commands.append((node,query))
        assert 'hidden' not in query
        assert 'alter_node_interface' in query and 'false' in query
        dsns[node]+=' host=localhost port=19005'
        return ''
    r._psql=sql
    update_mmr_endpoints(r,'g','pg_1','pg_5',lambda _:None)
    assert [node for node,_ in commands]==['pg_5','peer']
    assert all('password=hidden' in dsn for dsn in dsns.values())
    # Replaying a completed metadata step must not execute the non-idempotent UDF twice.
    update_mmr_endpoints(r,'g','pg_1','pg_5',lambda _:None)
    assert len(commands)==2
