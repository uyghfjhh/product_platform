"""Opt-in streaming monitor acceptance; only pytest-owned temporary PGDATA."""
import pytest
from fastapi.encoders import jsonable_encoder
from test_deployment_live import lab  # noqa: F401 - shared isolated database fixture
from test_database_monitoring import monitoring
from platform_app.monitoring_alerts import DEFAULT_RULES, evaluate_links


@pytest.mark.parametrize('lab', ['PLATFORM_TEST_POSTGRES_HOME'], indirect=True)
def test_isolated_disconnect_reconnect_and_promotion(lab):
    p=lab.database('primary');s=lab.database('standby',source='primary')
    env={'database_name':'postgres','database_user':'postgres'}
    nodes=[{'id':'primary','host':'127.0.0.1','port':p},{'id':'standby','host':'127.0.0.1','port':s}]
    configured={'edges':[{'id':'p-s','source':'primary','target':'standby','kind':'streaming'}]}
    def sample(previous=None):
        value=jsonable_encoder(monitoring.snapshot(env,nodes))
        value['resolved_topology']=monitoring.reconcile(value,configured,previous)
        return value
    lab.wait(lambda:lab.sql('primary', "SELECT count(*) FROM pg_stat_replication WHERE state='streaming'")=='1')
    connected=sample()
    assert connected['resolved_topology']['links'][0]['status']=='connected'
    lab.command([lab.home/'bin/pg_ctl','stop','-D',lab.root/'standby','-m','fast','-w'])
    disconnected=sample(connected)
    assert disconnected['resolved_topology']['links'][0]['status']=='interrupted'
    previous=connected
    for _ in range(DEFAULT_RULES['consecutive_samples']):
        disconnected=sample(previous)
        disconnected['alerts']=evaluate_links(disconnected,previous,DEFAULT_RULES);previous=disconnected
    assert disconnected['alerts'][0]['active']
    lab.command([lab.home/'bin/pg_ctl','start','-D',lab.root/'standby','-l',lab.root/'standby.log','-w'])
    lab.wait(lambda:lab.sql('primary', "SELECT count(*) FROM pg_stat_replication WHERE state='streaming'")=='1')
    restored=sample(disconnected)
    assert restored['resolved_topology']['links'][0]['status']=='connected'
    previous=disconnected
    for _ in range(DEFAULT_RULES['consecutive_samples']):
        restored=sample(previous)
        restored['alerts']=evaluate_links(restored,previous,DEFAULT_RULES);previous=restored
    assert restored['alerts'][0]['status']=='recovered'
    lab.command([lab.home/'bin/pg_ctl','stop','-D',lab.root/'primary','-m','fast','-w'])
    lab.command([lab.home/'bin/pg_ctl','promote','-D',lab.root/'standby','-w'])
    promoted=sample(restored)
    assert promoted['nodes'][1]['sections']['runtime']['rows'][0]['recovery'] is False
    assert not any(l['status']=='connected' for l in promoted['resolved_topology']['links'])


@pytest.mark.parametrize('lab', ['PLATFORM_TEST_POSTGRES_HOME'], indirect=True)
def test_isolated_lock_monitor_reports_blocker_and_waiter(lab):
    import threading
    import psycopg
    port=lab.database('lock-db')
    lab.sql('lock-db','CREATE TABLE lock_evidence(id integer PRIMARY KEY,value integer); INSERT INTO lock_evidence VALUES(1,0)')
    args={'host':'127.0.0.1','port':port,'dbname':'postgres','user':'postgres','options':'-c statement_timeout=10000'}
    failures=[]
    with psycopg.connect(**args) as blocker, psycopg.connect(**args) as waiter:
        blocker.execute('UPDATE lock_evidence SET value=1 WHERE id=1')
        def wait_on_row():
            try:
                waiter.execute('UPDATE lock_evidence SET value=2 WHERE id=1')
                waiter.rollback()
            except Exception as exc:
                failures.append(exc)
        thread=threading.Thread(target=wait_on_row);thread.start()
        try:
            lab.wait(lambda:lab.sql('lock-db',f'SELECT cardinality(pg_blocking_pids({waiter.info.backend_pid}))')=='1')
            observed=monitoring.sample_node({'database_name':'postgres','database_user':'postgres'},{'id':'lock-db','host':'127.0.0.1','port':port})
            links=observed['sections']['blocking']['rows']
            assert any(r['blocking_pid']==blocker.info.backend_pid and r['waiting_pid']==waiter.info.backend_pid for r in links)
            assert observed['sections']['activity_summary']['rows'][0]['blocked']==1
        finally:
            blocker.rollback();thread.join(timeout=10)
        assert not thread.is_alive() and not failures


@pytest.mark.parametrize('lab', ['PLATFORM_TEST_POSTGRES_HOME'], indirect=True)
def test_diagnostics_use_installed_extension_schema_and_never_install(lab):
    from platform_app.postgres_monitoring import diagnostics
    port=lab.database('diagnostics-db',plugin='pg_stat_statements')
    env={'host':'127.0.0.1','port':port,'database_name':'postgres','database_user':'postgres'}
    assert diagnostics(env,'queries')['available'] is False
    assert lab.sql('diagnostics-db',"SELECT count(*) FROM pg_extension WHERE extname='pg_stat_statements'")=='0'
    lab.sql('diagnostics-db','CREATE SCHEMA stats; CREATE EXTENSION pg_stat_statements WITH SCHEMA stats; SELECT 1')
    query=diagnostics(env,'queries')
    assert query['available'] and query['sections']['ranking']['valid'] and query['sections']['ranking']['rows']
    lab.sql('diagnostics-db','CREATE TABLE maintenance_evidence(id integer); INSERT INTO maintenance_evidence VALUES(1)')
    tables=diagnostics(env,'tables')
    assert tables['sections']['tables']['valid']
    assert any(r['relname']=='maintenance_evidence' for r in tables['sections']['tables']['rows'])


@pytest.mark.parametrize('lab', ['PLATFORM_TEST_POSTGRES_HOME'], indirect=True)
def test_background_collection_persists_snapshot_history_and_can_stop(lab):
    from dataclasses import replace
    from unittest.mock import patch
    from types import SimpleNamespace
    from platform_app.monitoring import MonitoringService
    from platform_app.config import load_settings
    port=lab.database('monitor-db')
    environment={'id':'isolated','product_id':'fbase-database','host':'127.0.0.1','port':port,'database_name':'postgres','database_user':'postgres'}
    node={'id':'monitor-db','host':'127.0.0.1','port':port,'data_dir':str(lab.root/'monitor-db')}
    store=SimpleNamespace(environments=SimpleNamespace(get_environment=lambda _:environment),tasks=SimpleNamespace(list_tasks=lambda *args,**kwargs:[]))
    provider=SimpleNamespace(monitoring_snapshot=monitoring.snapshot,monitoring_metrics=monitoring.derive,monitoring_links=monitoring.reconcile,monitoring_mmr_metrics=monitoring.derive_mmr)
    service=MonitoringService(replace(load_settings(),data_dir=lab.root/'control'),store)
    service.enable('isolated',True)
    with patch('platform_app.monitoring.provider_for',return_value=provider),patch('platform_app.monitoring.configured_topology',return_value={'nodes':[node],'edges':[]}),patch('platform_app.monitoring.monitoring_hosts.collect',return_value=[]):
        service.tick()
        result=service.read('isolated')
        assert result['latest']['nodes'][0]['sections']['connections']['valid']
        assert len(result['history'])==1 and result['enabled']
        service.tick()
        assert len(service.read('isolated')['history'])==1
        service.enable('isolated',False);service.tick()
        assert not service.read('isolated')['enabled']
        assert len(service.read('isolated')['history'])==1
