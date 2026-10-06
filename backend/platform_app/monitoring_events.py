"""Observed transitions; timestamps describe sampling, not exact event occurrence."""


def transitions(current, previous):
    if not previous:
        return []
    events = []
    old_nodes = {n['node']['id']: n for n in previous.get('nodes', [])}
    for node in current.get('nodes', []):
        old = old_nodes.get(node['node']['id'], {})
        a = old.get('sections', {}).get('runtime', {})
        b = node.get('sections', {}).get('runtime', {})
        if a.get('valid') and b.get('valid') and a.get('rows') and b.get('rows'):
            if a['rows'][0].get('recovery') != b['rows'][0].get('recovery'):
                events.append({'kind': 'role', 'subject': node['node']['id'], 'message': '观测到主备角色变化'})
            if a['rows'][0].get('started_at') != b['rows'][0].get('started_at'):
                events.append({'kind': 'restart', 'subject': node['node']['id'], 'message': '观测到实例启动时间变化'})
    old_links = {l['id']: l for l in previous.get('resolved_topology', {}).get('links', [])}
    for link in current.get('resolved_topology', {}).get('links', []):
        old = old_links.get(link['id'])
        if old and old['status'] != link['status']:
            events.append({'kind': 'link', 'subject': link['id'], 'message': '%s → %s' % (old['status'], link['status'])})
        elif not old:
            events.append({'kind': 'link', 'subject': link['id'], 'message': '新增链路观测：%s' % link['status']})
    old_alerts = {a['key']: a for a in previous.get('alerts', [])}
    for alert in current.get('alerts', []):
        old = old_alerts.get(alert['key'])
        if alert['status'] in ('active', 'recovered', 'unknown') and (not old or old['status'] != alert['status']):
            events.append({'kind': 'alert', 'subject': alert['key'], 'message': '%s：%s' % (alert['status'], alert['reason'])})
    return [{**event, 'observed_at': current['observed_at']} for event in events]


def deployment_events(tasks, start):
    """Project public task lifecycle facts, not presumed database causality."""
    from datetime import datetime
    events=[]
    for task in tasks:
        if not task.get('action','').startswith('deployment.'):
            continue
        for field,phase in [('created_at','任务创建'),('started_at','任务启动'),('finished_at','任务结束')]:
            stamp=task.get(field)
            if not stamp:
                continue
            try:
                if datetime.fromisoformat(stamp).timestamp()<start:
                    continue
            except (TypeError,ValueError):
                continue
            events.append({'kind':'deployment','task_id':task['id'],'subject':task['id'],'observed_at':stamp,
                           'message':f"{task['action']} · {phase}" + (f" · {task['status']}" if field=='finished_at' else ''),
                           'source':'平台任务记录；时间相关不表示因果'})
    return events
