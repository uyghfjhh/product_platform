"""Kingbase V9 physical replication evidence for the shared monitoring UI."""

from platform_app.postgres_monitoring import section
from platform_app.postgres_monitoring import snapshot as base_snapshot

from products.kingbase.database import RECEIVER, upstream_matches


def snapshot(environment, nodes):
    return base_snapshot(environment, nodes, query_overrides={
        'wal': None,
        'receivers': RECEIVER,
        'system': 'SELECT system_identifier::text FROM pg_control_system()',
        'senders': 'SELECT r.pid,r.backend_start,r.application_name,r.state,r.sync_state,'
                   'r.sent_lsn::text,r.write_lsn::text,r.flush_lsn::text,r.replay_lsn::text,'
                   's.slot_name FROM sys_stat_replication r '
                   'LEFT JOIN sys_replication_slots s ON s.active_pid=r.pid',
    })


def rows(node, name):
    return section(node, name)[1]


def links(current, configured, previous=None):
    observed = {node['node']['id']: node for node in current['nodes']}
    links = []
    for edge in configured.get('edges', []):
        source, target = observed.get(edge['source'], {}), observed.get(edge['target'], {})
        src_runtime, dst_runtime = rows(source, 'runtime'), rows(target, 'runtime')
        src_system, dst_system = rows(source, 'system'), rows(target, 'system')
        confirmed = bool(src_runtime and dst_runtime and src_runtime[0]['recovery'] is False
                         and dst_runtime[0]['recovery'] is True and src_system and dst_system
                         and src_system[0]['system_identifier'] == dst_system[0]['system_identifier'])
        receiver = next(iter(rows(target, 'receivers')), {})
        src_endpoint, dst_endpoint = source.get('node', {}), target.get('node', {})
        route_matches = bool(src_endpoint and dst_endpoint and upstream_matches(src_endpoint, dst_endpoint, receiver))
        sender = next((r for r in rows(source, 'senders') if r.get('slot_name') == receiver.get('slot_name') and receiver.get('slot_name')), {})
        connected = confirmed and route_matches and receiver.get('status') == 'streaming' and sender.get('state') == 'streaming'
        links.append({'id': edge['id'], 'kind': 'physical', 'source': edge['source'], 'target': edge['target'],
                      'configured': True, 'slot_name': receiver.get('slot_name'), 'sender_pid': sender.get('pid'),
                      'status': 'connected' if connected else 'unknown',
                      'reason': '主备系统标识、上游、物理槽和发送接收状态已确认' if connected else '主备复制关系证据不足，请查看各实例采样'})
    return {'members': [], 'links': links}
