import importlib.util
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'products/fbase-database/monitoring.py'
spec = importlib.util.spec_from_file_location('monitoring_test', path)
monitoring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitoring)


def test_connection_failure_remains_unknown():
    node = {'id': 'a', 'host': 'example.invalid', 'port': 5432}
    with patch.object(monitoring.psycopg, 'connect', side_effect=monitoring.psycopg.OperationalError()):
        result = monitoring.sample_node({'database_name': 'db', 'database_user': 'u'}, node)
    assert result['error']
    assert result['sections'] == {}


def test_queries_do_not_expose_member_connection_credentials():
    assert 'node_dsn' not in monitoring.QUERIES['members']
    assert 'conninfo' not in monitoring.QUERIES['receivers']


def test_background_history_does_not_repeat_raw_conflicts(tmp_path):
    from types import SimpleNamespace
    from platform_app.monitoring import MonitoringService
    env = {'id': 'env', 'product_id': 'fbase-database'}
    store = SimpleNamespace(environments=SimpleNamespace(get_environment=lambda _: env), tasks=SimpleNamespace(list_tasks=lambda *args, **kwargs: []))
    service = MonitoringService(SimpleNamespace(data_dir=tmp_path), store)
    service.enable('env', True)
    value = {'observed_at': '2026-10-03T00:00:00+00:00', 'nodes': [{'node': {'id': 'a'}, 'sections': {'conflicts': {'valid': True, 'rows': [{'local_tuple': {'id': 1}}]}, 'slots': {'valid': True, 'rows': []}}}]}
    collect = SimpleNamespace(monitoring_snapshot=lambda *_: value)
    with patch('platform_app.monitoring.provider_for', return_value=collect), patch('platform_app.monitoring.configured_topology', return_value={'nodes': []}):
        service.tick()
        service.tick()
    result = service.read('env')
    assert len(result['history']) == 1  # Shared collector coalesces repeated ticks.
    assert 'conflicts' in result['latest']['nodes'][0]['sections']
    assert 'conflicts' not in result['history'][0]['nodes'][0]['sections']
    service.rules('env', {**service.rules('env'),'retained_mib':1024,'consecutive_samples':4})
    assert len(service.read('env')['history']) == 1
    assert service.read('env')['latest'] is not None
    service.enable('env', False)
    assert service.read('env')['enabled'] is False


def progress_sample(stamp, position, started='start', timeline=1):
    sections = {'runtime': {'valid': True, 'rows': [{'started_at': started, 'recovery': False}]},
                'timeline': {'valid': True, 'rows': [{'timeline_id': timeline}]},
                'senders': {'valid': True, 'observed_at': stamp, 'rows': [{'pid': 5, 'backend_start': 'connection', 'slot_name': 'physical', 'sent_lsn': position, 'write_lsn': position, 'flush_lsn': position, 'replay_lsn': position}]}}
    return {'nodes': [{'node': {'id': 'a'}, 'sections': sections}]}


def test_progress_rates_and_baseline_resets():
    old = progress_sample('2026-10-03T00:00:00+00:00', '0/100')
    current = progress_sample('2026-10-03T00:00:15+00:00', '0/1F0')
    assert monitoring.derive(current, old)[0]['bytes_per_second']['sent_lsn'] == 16
    for changed in [progress_sample('2026-10-03T00:00:15+00:00', '0/1F0', started='restart'),
                    progress_sample('2026-10-03T00:00:15+00:00', '0/1F0', timeline=2),
                    progress_sample('2026-10-03T00:01:00+00:00', '0/1F0'),
                    progress_sample('2026-10-03T00:00:15+00:00', '0/80')]:
        assert monitoring.derive(changed, old)[0]['bytes_per_second']['sent_lsn'] is None
    assert monitoring.lsn('-1/123') is None
    assert monitoring.difference('0/10', None) is None


def test_alert_trigger_recovery_and_unknown():
    from platform_app.monitoring_alerts import DEFAULT_RULES, evaluate
    def sample(size, valid=True):
        return {'observed_at': 'now', 'nodes': [{'node': {'id': 'a'}, 'sections': {'slots': {'valid': valid, 'rows': [{'slot_name': 'peer', 'wal_status': 'reserved', 'retained_bytes': size}]}}}]}
    previous = None
    for _ in range(3):
        alerts = evaluate(sample(600*1048576), previous, DEFAULT_RULES)
        previous = {'alerts': alerts}
    assert alerts[0]['active']
    unknown = evaluate(sample(0, False), previous, DEFAULT_RULES)
    assert unknown[0]['status'] == 'unknown' and unknown[0]['active']
    previous = {'alerts': unknown}
    for _ in range(3):
        alerts = evaluate(sample(0), previous, DEFAULT_RULES)
        previous = {'alerts': alerts}
    assert alerts[0]['status'] == 'recovered'


def test_mmr_distance_never_assumes_parallel_apply_or_missing_error_data():
    node = {'node': {'id': 'a'}, 'sections': {
        'runtime': {'valid': True, 'rows': [{'recovery': False}]},
        'subscriptions': {'valid': True, 'rows': [{'sub_id': 1, 'sub_name': 'sub', 'origin_name': 'pg_1', 'num_writers': 1}]},
        'subscription_runtime': {'valid': True, 'rows': [{'subid': 1, 'relid': None, 'pid':42, 'received_lsn': '0/200'}]},
        'origins': {'valid': True, 'rows': [{'external_id': 'pg_1', 'remote_lsn': '0/100'}]},
        'apply_configuration': {'valid': True, 'rows': [{'sub_id':1,'subscription_streaming':'false'}]},
        'workers': {'valid': True, 'rows': [{'pid':42,'backend_type':'logical replication worker'}]},
    }}
    result = monitoring.derive_mmr({'nodes': [node]})[0]
    assert result['received_to_origin_bytes'] == 256
    assert result['error_records_without_recovery'] is None
    node['sections']['apply_configuration']['rows'][0]['subscription_streaming'] = 'true'
    node['sections']['apply_configuration']['rows'][0]['member_streaming'] = 'p'
    assert monitoring.derive_mmr({'nodes': [node]})[0]['received_to_origin_bytes'] is None
    node['sections']['runtime']['rows'][0]['recovery'] = True
    assert monitoring.derive_mmr({'nodes': [node]}) == []


def link_fixture():
    def node(identity, member_id, recovery, port, system):
        sections = {k: {'valid': True, 'rows': []} for k in ['subscriptions','senders','slots','subscription_runtime','receivers','origins','errors']}
        sections.update(local_member={'valid': True, 'rows': [{'node_id':member_id,'node_name':f'member{member_id}','group_id':1,'group_uuid':'group','dbname':'db'}]},
                        runtime={'valid': True, 'rows': [{'recovery':recovery}]},
                        system={'valid': True, 'rows': [{'system_identifier':system}]})
        return {'node':{'id':identity,'host':'host','port':port},'sections':sections}
    a=node('a',1,False,5001,'system-a');b=node('b',2,False,5002,'system-b')
    back=node('a-back',1,True,5003,'system-a')
    a['sections']['slots']['rows']=[{'slot_name':'ab','slot_type':'logical','active':True,'active_pid':101}]
    a['sections']['senders']['rows']=[{'pid':101}]
    b['sections']['subscriptions']['rows']=[{'sub_id':5,'sub_name':'sub-ab','group_id':1,'origin_node_id':1,'target_node_id':2,'slot_name':'ab','sub_enabled':True,'origin_name':'pg_5'}]
    b['sections']['subscription_runtime']['rows']=[{'subid':5,'relid':None,'pid':202}]
    back['sections']['receivers']['rows']=[{'sender_host':'host','sender_port':5001,'status':'streaming'}]
    configured={'edges':[{'id':'a-back','source':'a','target':'a-back','kind':'streaming'}]}
    return {'nodes':[a,b,back]},configured


def test_links_merge_primary_identity_and_verify_physical_endpoint():
    sample,configured=link_fixture()
    result=monitoring.reconcile(sample,configured)
    logical=next(l for l in result['links'] if l['kind']=='mmr')
    physical=next(l for l in result['links'] if l['kind']=='physical')
    assert logical['source']=='a' and logical['target']=='b' and logical['status']=='connected'
    assert logical['sender_pid']==101
    assert physical['status']=='connected'
    sample['nodes'][2]['sections']['system']['rows'][0]['system_identifier']='other'
    assert next(l for l in monitoring.reconcile(sample,configured)['links'] if l['kind']=='physical')['status']=='unknown'


def test_missing_observation_and_duplicate_writer_never_report_connected():
    from copy import deepcopy
    sample,configured=link_fixture()
    sample['nodes'][1]['sections']['subscription_runtime']['valid']=False
    assert next(l for l in monitoring.reconcile(sample,configured)['links'] if l['kind']=='mmr')['status']=='unknown'
    sample['nodes'][1]['sections']['subscription_runtime']['valid']=True
    duplicate=deepcopy(sample['nodes'][0]);duplicate['node']['id']='a-extra';sample['nodes'].append(duplicate)
    result=next(l for l in monitoring.reconcile(sample,configured)['links'] if l['kind']=='mmr')
    assert result['source'] is None and result['status']=='unknown'
    assert '多个主库' in result['reason']


def test_primary_promotion_changes_member_carrier_without_using_configured_name():
    sample,configured=link_fixture()
    old,new=sample['nodes'][0],sample['nodes'][2]
    old['sections']['runtime']['rows'][0]['recovery']=True
    new['sections']['runtime']['rows'][0]['recovery']=False
    new['sections']['slots']=old['sections']['slots'];new['sections']['senders']=old['sections']['senders']
    old['sections']['receivers']['rows']=[{'sender_host':'host','sender_port':5003,'status':'streaming'}]
    links=monitoring.reconcile(sample,configured)['links']
    assert next(l for l in links if l['kind']=='mmr')['source']=='a-back'
    actual=next(l for l in links if l['kind']=='physical' and l['source']=='a-back')
    assert actual['target']=='a' and actual['status']=='connected' and actual['configured'] is False



def test_loopback_receiver_is_relative_to_remote_host_not_platform_host():
    sample,configured=link_fixture()
    sample['nodes'][2]['sections']['receivers']['rows'][0]['sender_host']='127.0.0.1'
    assert next(l for l in monitoring.reconcile(sample,configured)['links'] if l['kind']=='physical')['status']=='connected'
    sample['nodes'][0]['node']['host']='another-host'
    assert next(l for l in monitoring.reconcile(sample,configured)['links'] if l['kind']=='physical')['status']=='unknown'


def test_observed_events_do_not_invent_role_changes_from_unknown_samples():
    from copy import deepcopy
    from platform_app.monitoring_events import transitions
    old,_=link_fixture();old['observed_at']='2026-10-03T00:00:00+00:00'
    current=deepcopy(old);current['observed_at']='2026-10-03T00:00:15+00:00'
    current['nodes'][0]['sections']['runtime']['valid']=False
    assert transitions(current,old)==[]
    current['nodes'][0]['sections']['runtime']['valid']=True
    current['nodes'][0]['sections']['runtime']['rows'][0]['recovery']=True
    assert transitions(current,old)[0]['kind']=='role'


def test_minute_history_preserves_peaks_and_weights(tmp_path):
    from types import SimpleNamespace
    from platform_app.monitoring import MonitoringService
    from platform_app import monitoring_history
    service=MonitoringService(SimpleNamespace(data_dir=tmp_path),None)
    sample={'observed_at':'2026-10-03T00:00:01+00:00','nodes':[{'node':{'id':'a'},'sections':{'runtime':{'valid':True,'rows':[{'started_at':'start','recovery':False}]},'slots':{'valid':True,'rows':[{'slot_name':'peer','retained_bytes':1048576}]}}}]}
    with service.connection() as db:
        monitoring_history.append(db,'env',sample)
        sample['observed_at']='2026-10-03T00:00:16+00:00'
        sample['nodes'][0]['sections']['slots']['rows'][0]['retained_bytes']=9*1048576
        monitoring_history.append(db,'env',sample)
        series=monitoring_history.read(db,'env',0,60)
        assert series[0]['points'][0]['value']==5
        assert series[0]['points'][0]['peak']==9
        assert series[0]['points'][0]['valid_samples']==2
        sample['nodes'][0]['sections']['slots']['valid']=False
        monitoring_history.append(db,'env',sample)
        assert monitoring_history.read(db,'env',0,10080)[0]['points'][0]['valid_samples']==2


def test_member_disagreement_unknown_and_recovery():
    from copy import deepcopy
    from platform_app.monitoring_alerts import evaluate_members
    sample,_=link_fixture()
    for n in sample['nodes']:
        n['sections']['members']={'valid':True,'rows':[{'node_id':1,'node_name':'member1','group_id':1,'node_state':'ACTIVE'},{'node_id':2,'node_name':'member2','group_id':1,'node_state':'ACTIVE'}]}
    assert all(m['status']=='agreed' for m in monitoring.member_consensus(sample))
    sample['nodes'][1]['sections']['members']['rows'][0]['node_state']='PART_START'
    assert monitoring.member_consensus(sample)[0]['status']=='disagree'
    previous=None
    for second in (0,15,30):
        current={'observed_at':f'2026-10-03T00:00:{second:02d}+00:00','member_consensus':monitoring.member_consensus(sample)}
        current['alerts']=evaluate_members(current,previous,{'consecutive_samples':3});previous=deepcopy(current)
    assert current['alerts'][0]['active']
    sample['nodes'][1]['sections']['members']['valid']=False
    current={'observed_at':'2026-10-03T00:00:45+00:00','member_consensus':monitoring.member_consensus(sample)}
    assert current['member_consensus'][0]['status']=='unknown'
    assert evaluate_members(current,previous,{'consecutive_samples':3})[0]['status']=='unknown'


def test_deployment_projection_only_uses_task_facts_in_window():
    from platform_app.monitoring_events import deployment_events
    tasks=[{'id':'deploy','action':'deployment.switchover','status':'SUCCEEDED','created_at':'2026-10-03T00:00:00+00:00','started_at':'2026-10-03T00:01:00+00:00','finished_at':'2026-10-03T00:02:00+00:00'},
           {'id':'other','action':'regression.run','status':'SUCCEEDED','created_at':'2026-10-03T00:01:00+00:00'}]
    from datetime import datetime
    events=deployment_events(tasks,datetime.fromisoformat('2026-10-03T00:01:30+00:00').timestamp())
    assert len(events)==1 and events[0]['task_id']=='deploy'
    assert '任务结束' in events[0]['message'] and '不表示因果' in events[0]['source']


def test_task_environment_filter_applies_before_limit(tmp_path):
    from platform_app.filestore import FileStore
    store=FileStore(tmp_path)
    for identity in ('wanted','other'):
        store.environments.put_environment({'id':identity,'product_id':'fbasecman','title':identity,'host':'localhost','port':5432,'database_name':'postgres','database_user':'postgres'})
    wanted=store.tasks.create_task('wanted','deployment.status','cluster',{},None)
    store.tasks.create_task('other','deployment.status','cluster',{},None)
    result=store.tasks.list_tasks(1,environment_id='wanted')
    assert result[0]['id']==wanted['id']


def test_database_rates_reset_and_no_access_hit_ratio():
    from copy import deepcopy
    from platform_app.postgres_monitoring import derive
    def sample(stamp,commit,hit,read,reset=None):
        return {'nodes':[{'node':{'id':'a'},'sections':{
            'runtime':{'valid':True,'observed_at':stamp,'rows':[{'started_at':'start','recovery':False}]},
            'database':{'valid':True,'observed_at':stamp,'rows':[{'datid':1,'xact_commit':commit,'xact_rollback':0,'blks_hit':hit,'blks_read':read,'deadlocks':0,'temp_bytes':0,'stats_reset':reset}]},
            'connections':{'valid':True,'rows':[{'instance_clients':5,'database_clients':2,'active':1,'idle':4,'idle_in_transaction':0,'max_connections':100}]},
        }}]}
    old=sample('2026-10-03T00:00:00+00:00',10,10,10)
    current=sample('2026-10-03T00:00:15+00:00',40,100,20)
    result=derive(current,old)[0]['metrics']
    assert result['commit_per_second']==2 and result['buffer_hit_percent']==90
    assert result['instance_clients']==5 and result['database_clients']==2
    unchanged=sample('2026-10-03T00:00:15+00:00',10,10,10)
    assert derive(unchanged,old)[0]['metrics']['buffer_hit_percent'] is None
    changed=deepcopy(current);changed['nodes'][0]['sections']['database']['rows'][0]['stats_reset']='reset'
    assert derive(changed,old)[0]['metrics']['commit_per_second'] is None
    changed=deepcopy(current);changed['nodes'][0]['sections']['runtime']['rows'][0]['started_at']='restart'
    assert derive(changed,old)[0]['metrics']['commit_per_second'] is None


def test_host_cache_shares_transport_and_scopes_filesystems(tmp_path):
    from types import SimpleNamespace
    from platform_app.monitoring import MonitoringService
    from platform_app import monitoring_hosts, monitoring_history
    service=MonitoringService(SimpleNamespace(data_dir=tmp_path/'state'),None)
    a={'id':'a','host':'127.0.0.1','data_dir':str(tmp_path/'a')}
    b={'id':'b','host':'127.0.0.1','data_dir':str(tmp_path/'b')}
    calls=[]
    def probe(host,request,*args,**kwargs):
        calls.append(request)
        count=len(calls)
        return {'sample_epoch':count*60,'uptime_seconds':count*60,'boot_id':'boot','cpu_total':count*1000,'cpu_idle':900 if count==1 else 1600,'memory_total':2000,'memory_available':1000,'devices':{},'filesystems':[{'device':'fs','paths':request['paths'],'total_bytes':1000,'available_bytes':500}]}
    with patch('platform_app.monitoring_hosts.probe',side_effect=probe):
        first=monitoring_hosts.collect(service,{'id':'env-a'},[a,a])
        assert len(first)==1 and len(calls)==1 and first[0]['metrics']['cpu_percent'] is None
        second=monitoring_hosts.collect(service,{'id':'env-b'},[b])
        assert len(calls)==2 and second[0]['metrics']['cpu_percent']==30
        assert all(str(tmp_path/'a') not in p for p in second[0]['raw']['filesystems'][0]['paths'])
        cached=monitoring_hosts.collect(service,{'id':'env-a'},[a])
        assert len(calls)==2 and cached[0]['new_sample'] is False
        with service.connection() as db:
            snapshot={'observed_at':'2026-10-03T00:00:00+00:00','nodes':[],'hosts':cached}
            monitoring_history.append(db,'env-a',snapshot)
            monitoring_history.append(db,'env-a',snapshot)
            monitoring_history.append(db,'env-b',snapshot)
            counts=db.execute('SELECT environment,max(count) FROM minute_metrics GROUP BY environment').fetchall()
        assert counts==[('env-a',1),('env-b',1)]


def test_host_reboot_invalidates_cpu_and_io_rates():
    from platform_app.monitoring_hosts import derive
    old={'sample_epoch':0,'boot_id':'old','cpu_total':100,'cpu_idle':10}
    current={'sample_epoch':60,'boot_id':'new','cpu_total':1000,'cpu_idle':100,'memory_total':100,'memory_available':40}
    metrics=derive(current,old)
    assert metrics['cpu_percent'] is None and metrics['memory_percent']==60


def test_host_pressure_only_counts_distinct_host_samples_and_recovers():
    from copy import deepcopy
    from platform_app.monitoring_alerts import DEFAULT_RULES,evaluate_hosts
    def sample(stamp,cpu,valid=True):
        return {'observed_at':stamp,'hosts':[{'host':'server','identity':'host','valid':valid,'observed_at':stamp,'raw':{'boot_id':'boot','filesystems':[]},'metrics':{'cpu_percent':cpu,'memory_percent':20}}]}
    previous=None
    for second in (0,60,120):
        stamp=f'2026-10-03T00:{second//60:02d}:00+00:00'
        current=sample(stamp,95);current['alerts']=evaluate_hosts(current,previous,DEFAULT_RULES)
        cached=deepcopy(current);cached['observed_at']=f'2026-10-03T00:{second//60:02d}:15+00:00'
        cached['alerts']=evaluate_hosts(cached,current,DEFAULT_RULES)
        assert cached['alerts'][0]['hits']==current['alerts'][0]['hits']
        previous=cached
    assert previous['alerts'][0]['active']
    unknown=sample('2026-10-03T00:03:00+00:00',None,False)
    unknown['alerts']=evaluate_hosts(unknown,previous,DEFAULT_RULES)
    assert unknown['alerts'][0]['status']=='unknown' and unknown['alerts'][0]['active']
    previous=unknown
    for minute in (4,5,6):
        current=sample(f'2026-10-03T00:{minute:02d}:00+00:00',30)
        current['alerts']=evaluate_hosts(current,previous,DEFAULT_RULES);previous=current
    assert current['alerts'][0]['status']=='recovered'


def test_full_filesystem_alert_is_immediate_and_bad_rules_rejected():
    from platform_app.monitoring_alerts import DEFAULT_RULES,evaluate_hosts,validate_rules
    import pytest
    value={'observed_at':'2026-10-03T00:00:00+00:00','hosts':[{'host':'server','identity':'host','valid':True,'observed_at':'2026-10-03T00:00:00+00:00','raw':{'boot_id':'boot','filesystems':[{'device':'disk','total_bytes':100,'available_bytes':0}]},'metrics':{}}]}
    alerts=evaluate_hosts(value,None,DEFAULT_RULES)
    assert alerts[0]['active'] and alerts[0]['severity']=='critical'
    with pytest.raises(ValueError):
        validate_rules({**DEFAULT_RULES,'disk_free_percent':60})



def test_extra_ssh_path_probe_does_not_accelerate_pressure_confirmation():
    from platform_app.monitoring_alerts import DEFAULT_RULES,evaluate_hosts
    def sample(stamp):
        return {'observed_at':stamp,'hosts':[{'host':'server','identity':'host','valid':True,'observed_at':stamp,'raw':{'boot_id':'boot','filesystems':[]},'metrics':{'memory_percent':95}}]}
    first=sample('2026-10-03T00:00:00+00:00');first['alerts']=evaluate_hosts(first,None,DEFAULT_RULES)
    second=sample('2026-10-03T00:00:15+00:00')
    assert evaluate_hosts(second,first,DEFAULT_RULES)[0]['hits']==1


def test_database_alerts_confirm_unknown_and_recovery():
    from platform_app.monitoring_alerts import DEFAULT_RULES,evaluate_database
    previous=None
    for seconds in (0,15,30):
        value={'observed_at':f'2026-10-03T00:00:{seconds:02d}+00:00','database_metrics':[{'node_id':'a','baseline':'boot','metrics':{'instance_clients':90,'max_connections':100,'blocked':2,'longest_transaction_seconds':200}}]}
        value['alerts']=evaluate_database(value,previous,DEFAULT_RULES);previous=value
    assert len(value['alerts'])==3 and all(a['active'] for a in value['alerts'])
    value={'observed_at':'2026-10-03T00:00:45+00:00','database_metrics':[{'node_id':'a','baseline':None,'metrics':{}}]}
    value['alerts']=evaluate_database(value,previous,DEFAULT_RULES)
    assert all(a['status']=='unknown' for a in value['alerts'])
    previous=value
    for minute,second in ((1,0),(1,15),(1,30)):
        value={'observed_at':f'2026-10-03T00:{minute:02d}:{second:02d}+00:00','database_metrics':[{'node_id':'a','baseline':'boot','metrics':{'instance_clients':20,'max_connections':100,'blocked':0,'longest_transaction_seconds':0}}]}
        value['alerts']=evaluate_database(value,previous,DEFAULT_RULES);previous=value
    assert all(a['status']=='recovered' for a in value['alerts'])


def test_hidden_transaction_stats_do_not_become_zero():
    from platform_app.postgres_monitoring import derive
    snapshot={'nodes':[{'node':{'id':'a'},'sections':{'activity_summary':{'valid':True,'rows':[{'blocked':0,'long_transactions':0,'longest_transaction_seconds':None,'hidden_clients':1}]}}}]}
    values=derive(snapshot,None)[0]['metrics']
    assert values['longest_transaction_seconds'] is None and values['long_transactions'] is None
    snapshot['nodes'][0]['sections']['activity_summary']['rows'][0]['hidden_clients']=0
    assert derive(snapshot,None)[0]['metrics']['longest_transaction_seconds']==0


def test_metrics_export_escaping_missing_values_and_staleness():
    from platform_app.monitoring_export import exposition
    from datetime import datetime
    stamp='2026-10-03T00:00:00+00:00';now=datetime.fromisoformat(stamp).timestamp()
    snapshot={'observed_at':stamp,'nodes':[{'node':{'id':'a'},'sections':{}}],
              'database_metrics':[{'node_id':'a','metrics':{'commit_per_second':2,'buffer_hit_percent':None,'wal_mib_per_second':1}}],
              'conflicts':[{'local_tuple':{'private':'business-row'}}]}
    text=exposition('env"\\\n',True,snapshot,now)
    assert 'environment_id="env\\"\\\\\\n"' in text
    assert 'platform_postgres_commits_per_second' in text
    assert 'platform_postgres_wal_bytes_per_second' in text and '1048576' in text
    assert 'buffer_hit_percent' not in text and 'business-row' not in text
    assert text.endswith('\n')
    stale=exposition('env',True,snapshot,now+61)
    assert 'platform_postgres_commits_per_second' not in stale
    assert 'platform_monitor_sample_available{environment_id="env"} 0' in stale


def test_export_reads_only_cache_without_collector_or_task_projection(tmp_path):
    from types import SimpleNamespace
    from platform_app.monitoring import MonitoringService
    service=MonitoringService(SimpleNamespace(data_dir=tmp_path),None)
    service.tick=lambda: (_ for _ in ()).throw(AssertionError('must not collect'))
    text=service.metrics('env')
    assert 'platform_monitor_enabled{environment_id="env"} 0' in text
    assert 'platform_monitor_sample_available{environment_id="env"} 0' in text
