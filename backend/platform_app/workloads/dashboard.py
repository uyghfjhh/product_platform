import json
import math
import time
from datetime import UTC, datetime

from ..monitoring_history import metrics
from ..postgres_monitoring import lsn
from .sql import environment_fingerprint


def epoch(value):
    return datetime.fromisoformat(value).timestamp()


def iso(value):
    return datetime.fromtimestamp(value, UTC).isoformat()


def dashboard(monitoring, run, store):
    start = epoch(run['created_at'])
    running = run['status'] in {'RUNNING', 'CANCELLING'}
    end = time.time() if running else epoch(run['updated_at'])
    end = max(start, end)
    identity = run['definition']['environment_id']
    fingerprint = run['definition'].get('monitoring_fingerprint')
    interval = max(15, math.ceil((end-start)/180))
    notes = []
    rows, events, count = [], [], 0
    with monitoring.connection() as connection:
        enabled = connection.execute('SELECT 1 FROM enabled WHERE id=?', (identity,)).fetchone() is not None
        if fingerprint:
            lower = max(start, time.time()-86400)
            count = connection.execute('SELECT count(*) FROM samples WHERE environment=? AND fingerprint=? AND stamp>=? AND stamp<=?',
                                       (identity, fingerprint, lower, end)).fetchone()[0]
            if count <= 180:
                rows = connection.execute('SELECT stamp,payload FROM samples WHERE environment=? AND fingerprint=? AND stamp>=? AND stamp<=? ORDER BY stamp',
                                          (identity, fingerprint, lower, end)).fetchall()
            else:
                rows = connection.execute('SELECT max(stamp),payload FROM samples WHERE environment=? AND fingerprint=? AND stamp>=? AND stamp<=? GROUP BY CAST((stamp-?)/? AS INTEGER) ORDER BY max(stamp)',
                                          (identity, fingerprint, lower, end, start, interval)).fetchall()
            events = connection.execute('SELECT payload FROM events WHERE environment=? AND stamp>=? AND stamp<=? ORDER BY stamp DESC LIMIT 200',
                                        (identity, lower, end)).fetchall()
        else:
            notes.append('该运行未记录监控拓扑指纹，不关联其他时间或环境的样本。')
    if not enabled and running:
        notes.append('环境后台采集未启用，已有采样不代表当前状态。')
    if start < time.time()-86400:
        notes.append('原始环境采样保留 24 小时，较早运行区间已超出保留范围。')
    environment = store.environments.get_environment(identity)
    if environment and environment_fingerprint(environment) != run['definition'].get('environment_fingerprint'):
        notes.append('环境登记或部署配置发生变化；仅展示本次运行指纹对应的历史采样。')
    snapshots = [json.loads(row[1]) for row in rows]
    for snapshot in snapshots:
        for host in snapshot.get('hosts', []):
            try:
                in_window = start <= epoch(host['observed_at']) <= end
            except (KeyError, ValueError, TypeError):
                in_window = False
            if not in_window:
                host.update(valid=False, error='主机缓存不在本次运行区间，不能作为本次测试数据')
    values, definitions = [], {}
    for snapshot in snapshots:
        current = {}
        for key, label, unit, value in metrics(snapshot):
            definitions[key] = {'key': key, 'label': label, 'unit': unit, 'category': key.split(':')[0]}
            current[key] = value
        for node in snapshot.get('nodes', []):
            senders = node.get('sections', {}).get('senders', {})
            if node.get('error') or not senders.get('valid'):
                continue
            for sender in senders.get('rows', []):
                sent, replay = lsn(sender.get('sent_lsn')), lsn(sender.get('replay_lsn'))
                key = f"replication:{node['node']['id']}:{sender.get('pid')}:{sender.get('backend_start')}"
                definitions[key] = {'key': key, 'label': f"{node['node']['id']} / {sender.get('application_name', '')} / 已发送到回放位置差", 'unit': 'MiB', 'category': 'replication'}
                current[key] = (sent-replay)/1048576 if sent is not None and replay is not None and sent >= replay else None
        values.append(current)
    series = []
    for key, definition in definitions.items():
        points = []
        for snapshot, current in zip(snapshots, values):
            stamp = snapshot['observed_at']
            if definition['category'] == 'host':
                host = next((item for item in snapshot.get('hosts', []) if key.startswith('host:'+item.get('identity', '')+':') and item.get('valid')), None)
                if host:
                    stamp = host['observed_at']
            if points and points[-1]['time'] == stamp:
                continue
            points.append({'time': stamp, 'value': current.get(key)})
        series.append({**definition, 'points': points})
    timeline = [json.loads(row[0]) for row in reversed(events)]
    for index, task in enumerate(run.get('task_details', [])):
        if not task:
            continue
        title = run['definition'].get('workloads', [{}]*len(run.get('tasks', [])))[index].get('title', task.get('target', '负载'))
        for name, field in [('开始', 'started_at'), ('结束', 'finished_at')]:
            stamp = task.get(field)
            if stamp and start <= epoch(stamp) <= end:
                timeline.append({'observed_at': stamp, 'kind': 'workload.'+field, 'subject': title,
                                 'message': name+(' · '+task['status'] if field == 'finished_at' else ''), 'task_id': task['id'], 'source': '平台任务生命周期'})
    return {
        'run_id': run['id'], 'environment_id': identity, 'running': running, 'enabled': enabled,
        'range': {'start': iso(start), 'end': iso(end)}, 'interval_seconds': interval,
        'sample_count': count, 'displayed_samples': len(snapshots),
        'latest': snapshots[-1] if snapshots else None, 'series': series,
        'events': sorted(timeline, key=lambda event: event['observed_at'])[-200:], 'notes': notes,
        'capabilities': {
            'pool': {'available': False, 'reason': '当前负载直连登记的数据库；尚未绑定实际代理的连接池采集端点。'},
            'prepared_statements': {'available': False, 'reason': '数据库监控会话不能代表其他连接的预备语句缓存；需要代理或驱动专用采集器。'},
            'percentiles': {'available': False, 'reason': '当前客户端未采集 P95 / P99，不以平均延迟替代。'},
        },
    }
