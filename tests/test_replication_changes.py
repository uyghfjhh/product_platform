from copy import deepcopy
from types import SimpleNamespace

import pytest
from platform_app.deployment.change_execution import ChangeExecutor
from platform_app.deployment.logical_changes import LogicalChanges
from platform_app.deployment.member_changes import MemberChanges
from platform_app.deployment.workbench import diff_operations
from test_deployment_changes import config


def clusters():
    raw = config()
    raw['instances']['t'] = {**raw['instances']['p'], 'port':7002, 'data_dir':'/data/t'}
    raw['instances']['u'] = {**raw['instances']['p'], 'port':7003, 'data_dir':'/data/u'}
    raw['streaming_clusters']['third'] = {'primary':'t','standbys':[]}
    raw['streaming_clusters']['fourth'] = {'primary':'u','standbys':[]}
    return raw


def member_config(section):
    raw = clusters()
    if section=='mmr_clusters':
        raw[section] = {'cluster':{'group_name':'group','database':'postgres','extensions':['fdd_mmr'],
                                'members':{'a':{'node_name':'a','streaming_cluster':'stream'},'b':{'node_name':'b','streaming_cluster':'third'}}}}
        desired = deepcopy(raw)
        desired[section]['cluster']['members']['c'] = {'node_name':'c','streaming_cluster':'fourth'}
    else:
        raw[section] = {'cluster':{'database':'postgres','extensions':['citus'],'coordinator':{'streaming_cluster':'stream'},'workers':{'b':{'streaming_cluster':'third'}}}}
        desired = deepcopy(raw)
        desired[section]['cluster']['workers']['c'] = {'streaming_cluster':'fourth'}
    return raw,desired


@pytest.mark.parametrize('section',['mmr_clusters','citus_clusters'])
def test_membership_changes_are_reviewed_on_existing_streams_without_deleting_instances(section):
    raw,desired=member_config(section)
    diff,operations,allowed=diff_operations(raw,desired)
    assert allowed and diff['removed']==[]
    assert next(op for op in operations if op['kind']=='replication_members')['executable']
    key='members' if section=='mmr_clusters' else 'workers'
    del desired[section]['cluster'][key]['b']
    assert diff_operations(raw,desired)[2]
    # Renaming a live member is not equivalent to adding and retiring one.
    desired=deepcopy(raw);desired[section]['cluster'][key]['b']['streaming_cluster']='fourth'
    assert not diff_operations(raw,desired)[2]


def test_mmr_duplicate_identity_and_entire_group_replacement_are_blocked():
    raw,desired=member_config('mmr_clusters')
    desired['mmr_clusters']['cluster']['members']['c']['node_name']='b'
    assert not diff_operations(raw,desired)[2]
    desired['mmr_clusters']['cluster']['members']={'c':{'node_name':'c','streaming_cluster':'fourth'}}
    assert not diff_operations(raw,desired)[2]


def test_logical_link_add_remove_and_repoint_retain_cluster_data():
    raw=clusters();desired=deepcopy(raw)
    desired['logical_replications']={'link':{'pub':{'streaming_cluster':'stream'},'sub':{'streaming_cluster':'third','copy_data':False}}}
    assert diff_operations(raw,desired)[2]
    assert diff_operations(desired,raw)[2]
    changed=deepcopy(desired);changed['logical_replications']['link']['sub']['streaming_cluster']='fourth'
    assert diff_operations(desired,changed)[2]
    changed['logical_replications']['link']['sub']['slot']={'failover':True}
    assert diff_operations(desired,changed)[2]  # actual server capability is checked before mutation


@pytest.mark.parametrize('section',['mmr_clusters','citus_clusters'])
def test_membership_cannot_mix_with_parameter_or_node_changes(section):
    raw,desired=member_config(section)
    desired['postgresql_config']={'parameters':{'work_mem':'16MB'}}
    assert not diff_operations(raw,desired)[2]


def executor(tmp_path, raw,desired):
    def runtime(config):
        return SimpleNamespace(config=SimpleNamespace(raw=config,instance=lambda node:{
            **config['instances'][node], 'host_config':{'address':'127.0.0.1'}}))
    return ChangeExecutor({'id':'plan'},raw,runtime(desired),runtime(raw),tmp_path/'checkpoint.json',lambda _:None)


def test_mmr_existing_business_tables_are_rejected_before_extension_or_join(tmp_path):
    raw,desired=member_config('mmr_clusters');m=MemberChanges(executor(tmp_path,raw,desired),'mmr_clusters','cluster')
    calls=[];m.identity=lambda _:None
    def sql(member,statement,**kwargs):
        calls.append(statement)
        if 'group_uuid' in statement:return 'uuid'
        if 'WHERE node_state' in statement:return 'a\nb'
        if 'SHOW track_commit' in statement:return 'on'
        if 'SHOW wal_level' in statement:return 'logical'
        if 'SHOW shared_preload' in statement:return 'fdd_mmr'
        return '1'
    m.sql=sql
    with pytest.raises(ValueError,match='业务表'):
        m.mmr_preflight()
    assert not any('CREATE' in sql or 'part_node' in sql for sql in calls)


def test_mmr_drop_failure_resumes_without_parting_twice_and_never_forces(tmp_path):
    raw,desired=member_config('mmr_clusters')
    del desired['mmr_clusters']['cluster']['members']['b']
    e=executor(tmp_path,raw,desired);m=MemberChanges(e,'mmr_clusters','cluster')
    calls=[];failed=True
    def sql(member,statement,**kwargs):
        nonlocal failed
        calls.append(statement)
        if 'SELECT node_state' in statement:return 'ACTIVE'
        if 'count(*)' in statement:return '1'
        if 'drop_node' in statement and failed:
            failed=False;raise RuntimeError('interrupted')
        return ''
    m.sql=sql
    with pytest.raises(RuntimeError):m.mmr_remove('b',m.after['members']['a'])
    # Reload the actual durable checkpoint rather than reusing object state.
    m=MemberChanges(executor(tmp_path,raw,desired),'mmr_clusters','cluster');m.sql=sql
    m.mmr_remove('b',m.after['members']['a'])
    assert sum('part_node' in s for s in calls)==1
    assert all('true,false' in s for s in calls if 'part_node' in s)
    assert not any('drop_node' in s and ',true' in s for s in calls)


def test_logical_subscription_identity_rejects_another_publisher_and_slot(tmp_path):
    raw=clusters();desired=deepcopy(raw)
    desired['logical_replications']={'link':{'pub':{'streaming_cluster':'stream'},'sub':{'streaming_cluster':'third'}}}
    m=LogicalChanges(executor(tmp_path,raw,desired),'link')
    row={'subpublications':['link_pub'],'subslotname':'link_slot','subconninfo':'host=127.0.0.1 port=7000 dbname=postgres user=postgres'}
    m.check_subscription(m.after,row)
    row['subconninfo']='host=192.168.1.2 port=7000 dbname=postgres'
    with pytest.raises(ValueError,match='基线'):m.check_subscription(m.after,row)
    row['subconninfo']='host=127.0.0.1 port=7000 dbname=postgres';row['subslotname']='another_slot'
    with pytest.raises(ValueError,match='基线'):m.check_subscription(m.after,row)


@pytest.mark.parametrize('section', ['mmr_clusters', 'citus_clusters'])
def test_new_member_can_create_a_physical_pair_and_retire_old_group(section):
    raw, desired = member_config(section)
    key = 'members' if section == 'mmr_clusters' else 'workers'
    for node, port in [('v', 7004), ('w', 7005)]:
        desired['instances'][node] = {**raw['instances']['p'], 'port': port, 'data_dir': '/data/'+node}
    desired['streaming_clusters']['fifth'] = {'primary': 'v', 'standbys': [{'instance': 'w', 'slot': 'lab_w'}]}
    desired[section]['cluster'][key]['c']['streaming_cluster'] = 'fifth'
    diff, operations, executable = diff_operations(raw, desired)
    assert executable and set(diff['added']) == {'v', 'w'}
    assert sum(op['kind'] == 'create_member_instance' for op in operations) == 2
    del desired[section]['cluster'][key]['b']
    del desired['streaming_clusters']['third']; del desired['instances']['t']
    assert diff_operations(raw, desired)[2]
    # Reusing an existing node as a newly initialized member is forbidden.
    desired['streaming_clusters']['fifth']['primary'] = 'u'
    assert not diff_operations(raw, desired)[2]


def test_retiring_a_group_still_used_by_another_link_is_not_executable():
    raw, desired = member_config('citus_clusters')
    raw['logical_replications'] = {'link': {'pub': {'streaming_cluster': 'third'}, 'sub': {'streaming_cluster': 'stream'}}}
    desired['logical_replications'] = deepcopy(raw['logical_replications'])
    del desired['citus_clusters']['cluster']['workers']['b']
    del desired['streaming_clusters']['third']; del desired['instances']['t']
    assert not diff_operations(raw, desired)[2]


def test_new_member_never_rewrites_an_existing_managed_directory(tmp_path):
    raw, desired = member_config('citus_clusters')
    desired['instances']['v'] = {**raw['instances']['p'], 'port': 7004, 'data_dir': '/data/v'}
    desired['streaming_clusters']['fifth'] = {'primary': 'v', 'standbys': []}
    desired['citus_clusters']['cluster']['workers']['c']['streaming_cluster'] = 'fifth'
    diff, _, allowed = diff_operations(raw, desired)
    assert allowed
    e = executor(tmp_path, raw, desired); e.plan.update(target='citus.cluster', diff=diff)
    e.runtime.target_instances = lambda _: list(desired['instances'])
    e.runtime._marker = lambda node: '/data/'+node+'/.pgcluster-managed'
    e.runtime.executor = SimpleNamespace(
        is_nonempty_dir=lambda *args: True,
        exists=lambda *args: True,
        read_text=lambda *args: '{"node":"v","deployment_plan":"other-plan"}',
    )
    with pytest.raises(ValueError, match='不属于当前部署计划'):
        e.run()
    assert e.state['completed'] == []


def test_duplicate_worker_alias_cannot_drain_a_worker_that_should_remain():
    raw, desired = member_config('citus_clusters')
    desired['citus_clusters']['cluster']['workers']['c']['streaming_cluster'] = 'third'
    assert not diff_operations(raw, desired)[2]


def test_logical_self_subscription_and_truncated_catalog_names_are_rejected():
    raw = clusters(); desired = deepcopy(raw)
    desired['logical_replications'] = {'link': {'pub': {'streaming_cluster': 'stream'}, 'sub': {'streaming_cluster': 'stream'}}}
    assert not diff_operations(raw, desired)[2]
    link = {'pub': {'streaming_cluster': 'stream'}, 'sub': {'streaming_cluster': 'third'}}
    desired['logical_replications'] = {'l'*60: link}
    assert not diff_operations(raw, desired)[2]
