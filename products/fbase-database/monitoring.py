"""Read-only, independently valid FBase database monitoring observations."""
from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg.rows import dict_row
from platform_app.postgres_monitoring import QUERIES as POSTGRES_QUERIES, lsn

QUERIES = {
    **POSTGRES_QUERIES,
    'runtime': "SELECT pg_is_in_recovery() AS recovery, pg_postmaster_start_time() AS started_at, CASE WHEN pg_is_in_recovery() THEN pg_last_wal_receive_lsn() ELSE pg_current_wal_lsn() END::text AS wal_position, pg_last_wal_replay_lsn()::text AS replay_position",
    'timeline': "SELECT timeline_id FROM pg_control_checkpoint()",
    'senders': "SELECT r.pid, r.backend_start, r.application_name, r.client_addr::text, r.state, r.sync_state, r.sent_lsn::text, r.write_lsn::text, r.flush_lsn::text, r.replay_lsn::text, r.write_lag::text, r.flush_lag::text, r.replay_lag::text, s.slot_name, s.slot_type FROM pg_stat_replication r LEFT JOIN pg_replication_slots s ON s.active_pid=r.pid",
    'receivers': "SELECT pid, status, written_lsn::text, flushed_lsn::text, latest_end_lsn::text, last_msg_receipt_time, sender_host, sender_port, slot_name FROM pg_stat_wal_receiver",
    'slots': "SELECT slot_name, slot_type, active, active_pid, restart_lsn::text, confirmed_flush_lsn::text, wal_status, safe_wal_size, CASE WHEN NOT pg_is_in_recovery() THEN pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) END AS retained_bytes FROM pg_replication_slots",
    'local_member': "SELECT l.node_id,n.node_name,n.group_id,n.dbname,g.group_uuid::text FROM fdd.mmr_local_node l LEFT JOIN fdd.mmr_node n ON n.node_id=l.node_id LEFT JOIN fdd.mmr_group g ON g.group_id=n.group_id",
    'system': "SELECT system_identifier::text FROM pg_control_system()",
    'members': "SELECT node_id, node_name, group_id, source_node_id, node_state::text, dbname, failover FROM fdd.mmr_node ORDER BY node_id",
    'subscriptions': "SELECT sub_id, sub_name, group_id, origin_node_id, target_node_id, sub_enabled, slot_name, origin_name, num_writers, replication_sets FROM fdd.mmr_subscription",
    'subscription_runtime': "SELECT subid, subname, pid, relid, received_lsn::text, last_msg_receipt_time FROM pg_stat_subscription",
    'apply_configuration': "SELECT s.oid AS sub_id,s.subname,to_jsonb(s)->>'substream' AS subscription_streaming,to_jsonb(n)->>'streaming' AS member_streaming,current_setting('fdd_streaming_parallel',true) AS parallel_subscriptions FROM pg_subscription s LEFT JOIN fdd.mmr_local_node l ON true LEFT JOIN fdd.mmr_node n ON n.node_id=l.node_id",
    'workers': "SELECT pid,backend_start,backend_type,state,wait_event_type,wait_event,application_name,leader_pid FROM pg_stat_activity WHERE datname=current_database() AND backend_type IN ('logical replication worker','logical replication parallel worker')",
    'origins': "SELECT local_id, external_id, remote_lsn::text, local_lsn::text FROM pg_replication_origin_status",
    'conflicts': "SELECT sub_id, local_xid::text, local_lsn::text, local_time, remote_xid::text, remote_commit_lsn::text, remote_commit_time, conflict_type, conflict_resolution, nspname, relname, key_tuple, local_tuple, remote_tuple, apply_tuple FROM fdd.mmr_conflict_history WHERE local_time > now() - interval '1 hour' ORDER BY local_time DESC LIMIT 30",
    'scope': "SELECT s.set_name, s.replicate_inserts, s.replicate_updates, s.replicate_deletes, s.replicate_truncate, t.set_reloid::text AS table_name, t.set_sync_state, t.set_iscopydata FROM fdd.mmr_replication_set s LEFT JOIN fdd.mmr_replication_set_table t ON t.set_id=s.set_id LIMIT 200",
    'catchup': "SELECT node_id, node_source_id, origin_node_id, slot_name, min_node_lsn::text, catchup_state, drop_time FROM fdd.mmr_node_catchup_info LIMIT 100",
    'errors': "SELECT group_name, origin_name, target_name, sub_name, worker_role_name, worker_pid, error_time::text, recovery_time::text, error_age::text, (error_time BETWEEN '0001-01-01'::timestamptz AND '9999-12-31'::timestamptz AND (recovery_time IS NULL OR recovery_time BETWEEN '0001-01-01'::timestamptz AND '9999-12-31'::timestamptz)) AS time_valid, error_message, error_context_message FROM fdd.mmr_worker_errors ORDER BY error_time DESC LIMIT 50",
}


def sample_node(environment, node):
    observed = {'node': node, 'observed_at': datetime.now(UTC).isoformat(), 'sections': {}}
    try:
        with psycopg.connect(host=node['host'], port=node['port'], dbname=environment['database_name'],
                             user=environment['database_user'], connect_timeout=3, row_factory=dict_row,
                             options='-c statement_timeout=2000 -c default_transaction_read_only=on -c application_name=platform_monitor -c client_encoding=UTF8') as conn:
            with conn.transaction():
                for name, sql in QUERIES.items():
                    try:
                        with conn.transaction():
                            with conn.cursor() as cursor:
                                cursor.execute(sql)
                                observed['sections'][name] = {'valid': True, 'rows': cursor.fetchall(), 'observed_at': datetime.now(UTC).isoformat()}
                    except psycopg.Error as exc:
                        observed['sections'][name] = {'valid': False, 'sqlstate': exc.sqlstate, 'error': exc.diag.message_primary or str(exc) or '统计读取失败：权限、字段或扩展不可用'}
    except psycopg.Error:
        observed['error'] = '连接或认证失败，无法观测实例'
    return observed


def snapshot(environment, nodes):
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda node: sample_node(environment, node), nodes))
    return {'observed_at': datetime.now(UTC).isoformat(), 'nodes': values}


def rows(node, section):
    if node.get('error'):
        return []
    value = node.get('sections', {}).get(section, {})
    return value.get('rows', []) if value.get('valid') else []


def difference(first, second):
    a, b = lsn(first), lsn(second)
    return a-b if a is not None and b is not None and a >= b else None


def derive(current, previous):
    old_nodes = {n['node']['id']: n for n in (previous or {}).get('nodes', [])}
    result = []
    for node in current['nodes']:
        old = old_nodes.get(node['node']['id'], {})
        runtime = rows(node, 'runtime')
        old_runtime = rows(old, 'runtime')
        timeline, old_timeline = rows(node, 'timeline'), rows(old, 'timeline')
        baseline = bool(runtime and old_runtime and timeline and old_timeline
                        and runtime[0].get('started_at') == old_runtime[0].get('started_at')
                        and runtime[0].get('recovery') == old_runtime[0].get('recovery')
                        and timeline == old_timeline)
        section = node['sections'].get('senders', {})
        old_section = old.get('sections', {}).get('senders', {})
        try:
            elapsed = (datetime.fromisoformat(section['observed_at']) - datetime.fromisoformat(old_section['observed_at'])).total_seconds()
        except (KeyError, ValueError, TypeError):
            elapsed = 0
        for sender in rows(node, 'senders'):
            positions = ['sent_lsn', 'write_lsn', 'flush_lsn', 'replay_lsn']
            old_sender = next((r for r in rows(old, 'senders') if sender.get('backend_start') is not None
                               and r.get('pid') == sender.get('pid')
                               and r.get('backend_start') == sender.get('backend_start')
                               and r.get('slot_name') == sender.get('slot_name')), None)
            rates = {}
            for key in positions:
                delta = difference(sender.get(key), (old_sender or {}).get(key))
                rates[key] = delta/elapsed if baseline and old_sender and 0 < elapsed <= 45 and delta is not None else None
            result.append({'node_id': node['node']['id'], 'pid': sender.get('pid'), 'backend_start': sender.get('backend_start'),
                           'application_name': sender.get('application_name'), 'timeline_id': timeline[0].get('timeline_id') if timeline else None, 'slot_type': sender.get('slot_type'),
                           'stage_bytes': {'sent_to_write': difference(sender.get('sent_lsn'), sender.get('write_lsn')),
                                           'write_to_flush': difference(sender.get('write_lsn'), sender.get('flush_lsn')),
                                           'flush_to_replay': difference(sender.get('flush_lsn'), sender.get('replay_lsn'))},
                           'bytes_per_second': rates, 'observed_at': section.get('observed_at')})
    return result


def derive_mmr(current):
    result = []
    for node in current['nodes']:
        runtime = rows(node, 'runtime')
        if not runtime or runtime[0].get('recovery') is not False:
            continue
        for sub in rows(node, 'subscriptions'):
            receiver = next((r for r in rows(node, 'subscription_runtime') if r.get('subid') == sub.get('sub_id') and r.get('relid') is None), None)
            origin = next((r for r in rows(node, 'origins') if r.get('external_id') == sub.get('origin_name')), None)
            # This measures source-coordinate distance, never business consistency.
            config = next((r for r in rows(node,'apply_configuration') if r.get('sub_id')==sub.get('sub_id')), {})
            stream=config.get('subscription_streaming')
            serial = stream in ('false','f') or (stream in ('true','t') and config.get('member_streaming') in ('f','t') and config.get('parallel_subscriptions')=='')
            active_workers = [r for r in rows(node,'subscription_runtime') if r.get('subid')==sub.get('sub_id') and r.get('relid') is None and r.get('pid')]
            worker_view = node['sections'].get('workers', {})
            parallel_present = any(w.get('backend_type')=='logical replication parallel worker' for w in rows(node,'workers'))
            serial = serial and len(active_workers)==1 and worker_view.get('valid') and not parallel_present
            mode = 'serial' if serial else 'parallel_or_unknown'
            distance = difference((receiver or {}).get('received_lsn'), (origin or {}).get('remote_lsn')) if serial else None
            errors = [e for e in rows(node, 'errors') if e.get('sub_name') == sub.get('sub_name')]
            result.append({'node_id': node['node']['id'], 'sub_id': sub.get('sub_id'),
                           'received_to_origin_bytes': distance, 'apply_mode':mode, 'observable_workers':len(active_workers),
                           'error_records_without_recovery': sum(e.get('recovery_time') is None for e in errors) if node['sections'].get('errors', {}).get('valid') else None,
                           'invalid_error_timestamps': sum(e.get('time_valid') is False for e in errors) if node['sections'].get('errors', {}).get('valid') else None,
                           'evidence': '确认串行配置时才计算源 WAL 坐标距离，origin 是提交处理进度（可能含跳过），不是数据一致性或精确应用时间延迟'})
    return result


def reconcile(current, configured, previous=None):
    """Resolve identities from database evidence, never labels or node ordering."""
    nodes = current['nodes']
    identities = {}
    observers = []
    for node in nodes:
        local = rows(node, 'local_member')
        runtime = rows(node, 'runtime')
        if len(local) != 1 or not local[0].get('group_uuid'):
            continue
        member = local[0]
        key = (member['group_uuid'], member.get('dbname'), member['node_id'])
        identities.setdefault(key, []).append(node)
        observers.append({'member_key': ':'.join(map(str, key)), 'node_id': node['node']['id'],
                          'member_name': member.get('node_name'), 'recovery': runtime[0].get('recovery') if runtime else None})
    links = []
    seen = set()
    for target in nodes:
        local = rows(target, 'local_member')
        runtime = rows(target, 'runtime')
        if len(local) != 1 or not runtime or runtime[0].get('recovery') is not False:
            continue
        member = local[0]
        for sub in rows(target, 'subscriptions'):
            if sub.get('target_node_id') != member.get('node_id') or sub.get('group_id') != member.get('group_id'):
                continue
            source_key = (member.get('group_uuid'), member.get('dbname'), sub.get('origin_node_id'))
            candidates = [n for n in identities.get(source_key, []) if rows(n, 'runtime') and rows(n, 'runtime')[0].get('recovery') is False]
            source = candidates[0] if len(candidates) == 1 else None
            receiver = next((r for r in rows(target, 'subscription_runtime') if r.get('subid') == sub.get('sub_id') and r.get('relid') is None), None)
            slot = next((r for r in rows(source or {}, 'slots') if r.get('slot_name') == sub.get('slot_name') and r.get('slot_type') == 'logical'), None)
            sender = next((r for r in rows(source or {}, 'senders') if slot and r.get('pid') == slot.get('active_pid')), None)
            unknown = not source or not target['sections'].get('subscription_runtime', {}).get('valid') or not (source or {}).get('sections', {}).get('slots', {}).get('valid')
            status = 'disabled' if not sub.get('sub_enabled') else 'unknown' if unknown else 'interrupted' if not receiver or not receiver.get('pid') or not slot or not slot.get('active') else 'connected'
            key = '%s:%s:%s:%s' % (member.get('group_uuid'), member.get('dbname'), sub.get('origin_node_id'), sub.get('target_node_id'))
            if key in seen:
                status = 'ambiguous'
                for link in links:
                    if link['id'] == key:
                        link['status'] = 'ambiguous'
                key += ':' + target['node']['id'] + ':' + str(sub.get('sub_id'))
            seen.add(key)
            links.append({'id': key, 'kind': 'mmr', 'source': source['node']['id'] if source else None,
                          'target': target['node']['id'], 'source_member': sub.get('origin_node_id'), 'target_member': sub.get('target_node_id'),
                          'sub_id': sub.get('sub_id'), 'sub_name': sub.get('sub_name'), 'slot_name': sub.get('slot_name'),
                          'origin_name': sub.get('origin_name'), 'sender_pid': (sender or {}).get('pid'), 'status': status,
                          'reason': '多个主库承载同一成员身份' if len(candidates)>1 else '发送槽与接收进程连接证据，不等同于应用追平' if status=='connected' else '映射、采集、启用配置及进程证据见详情'})
    # Observe actual physical source endpoints; retain unconfirmed configured edges separately.
    observed_pairs = set()
    for target in nodes:
        if not rows(target, 'runtime') or rows(target, 'runtime')[0].get('recovery') is not True:
            continue
        for receiver in rows(target, 'receivers'):
            candidates = [n for n in nodes if n['node']['port'] == receiver.get('sender_port') and (n['node']['host'] == receiver.get('sender_host') or (receiver.get('sender_host') in ('127.0.0.1', '::1', 'localhost') and n['node']['host'] == target['node']['host']))]
            if len(candidates) != 1:
                continue
            source = candidates[0]
            systems = rows(source, 'system'), rows(target, 'system')
            confirmed = bool(systems[0] and systems[1] and systems[0][0].get('system_identifier') == systems[1][0].get('system_identifier'))
            pair = (source['node']['id'], target['node']['id'])
            observed_pairs.add(pair)
            slot = next((r for r in rows(source, 'slots') if r.get('slot_name') == receiver.get('slot_name') and r.get('slot_type')=='physical'), None)
            sender = next((r for r in rows(source, 'senders') if slot and r.get('pid')==slot.get('active_pid')), None)
            planned = next((e for e in configured.get('edges', []) if e.get('kind')=='streaming' and (e['source'], e['target'])==pair), None)
            links.append({'id': 'physical:'+':'.join(pair), 'kind': 'physical', 'source': pair[0], 'target': pair[1],
                          'configured': planned is not None, 'slot_name': receiver.get('slot_name'), 'sender_pid': (sender or {}).get('pid'),
                          'status': 'connected' if confirmed and receiver.get('status')=='streaming' else 'unknown',
                          'reason': ('接收来源与实例身份已确认' if confirmed else 'system identifier 尚未确认') + ('；实际方向与配置不同' if not planned else '')})
    for edge in configured.get('edges', []):
        pair = (edge['source'], edge['target'])
        if edge.get('kind') != 'streaming' or pair in observed_pairs:
            continue
        old_link = next((l for l in (previous or {}).get('resolved_topology', {}).get('links', []) if l.get('kind')=='physical' and (l.get('source'), l.get('target'))==pair), None)
        source = next((n for n in nodes if n['node']['id']==edge['source']), {})
        old_source = next((n for n in (previous or {}).get('nodes', []) if n['node']['id']==edge['source']), {})
        runtime, old_runtime = rows(source,'runtime'), rows(old_source,'runtime')
        stable = bool(runtime and old_runtime and runtime[0].get('recovery') is False and runtime[0].get('started_at') and runtime[0].get('started_at')==old_runtime[0].get('started_at'))
        slot = next((r for r in rows(source,'slots') if old_link and r.get('slot_name')==old_link.get('slot_name') and r.get('slot_type')=='physical'), None)
        interrupted = bool(stable and slot and slot.get('active') is False)
        links.append({'id': 'physical:'+':'.join(pair), 'kind': 'physical', 'source': edge['source'], 'target': edge['target'],
                      'slot_name': (old_link or {}).get('slot_name'), 'configured': True, 'status': 'interrupted' if interrupted else 'unknown',
                      'reason': '先前确认链路的源端物理槽已断开；目标可能不可观测，不据此判宕机' if interrupted else '配置关系，当前采样未确认实际接收来源'})
    return {'members': observers, 'links': links}



def member_consensus(snapshot):
    members = {}
    groups = {}
    for node in snapshot['nodes']:
        local = rows(node,'local_member')
        runtime = rows(node,'runtime')
        if len(local)!=1 or not runtime or runtime[0].get('recovery') is not False:
            continue
        identity=local[0]
        if not identity.get('group_uuid'):
            continue
        group=(identity['group_uuid'],identity.get('dbname'))
        expected=groups.setdefault(group,set())
        expected.update(m['node_id'] for m in rows(node,'members') if m.get('group_id')==identity.get('group_id') and m.get('node_state')=='ACTIVE')
        for member in rows(node,'members'):
            if member.get('group_id')!=identity.get('group_id'):
                continue
            key=(*group,member['node_id'])
            value=members.setdefault(key,{'key':':'.join(map(str,key)),'member_name':member.get('node_name'),'observations':[]})
            value['observations'].append({'observer':node['node']['id'],'observer_member_id':identity['node_id'],'state':member.get('node_state')})
    result=[]
    for key,member in members.items():
        observed={r['observer_member_id'] for r in member['observations'] if r['state'] is not None}
        states={r['state'] for r in member['observations'] if r['state'] is not None}
        # All accessible primaries must agree; fewer than two is insufficient evidence.
        member['status']='disagree' if len(states)>1 else 'agreed' if len(observed)>=2 and observed==groups[key[:2]] else 'unknown'
        result.append(member)
    return result
