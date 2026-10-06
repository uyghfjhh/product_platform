"""Evidence-based WAL retention alerts with explicit recovery hysteresis."""
from datetime import datetime

DEFAULT_RULES = {'retained_mib':512,'consecutive_samples':3,'cpu_percent':85,'memory_percent':90,'disk_free_percent':10,'connection_percent':80,'blocked_sessions':1,'long_transaction_seconds':180}


def validate_rules(value):
    if set(value) != set(DEFAULT_RULES):
        raise ValueError('告警规则字段不完整')
    if any(isinstance(v, bool) or not isinstance(v, int) for v in value.values()):
        raise ValueError('阈值必须为整数')
    if not 1 <= value['retained_mib'] <= 1048576 or not 1 <= value['consecutive_samples'] <= 20:
        raise ValueError('阈值或连续样本数超出范围')
    if not 1<=value['cpu_percent']<=100 or not 1<=value['memory_percent']<=100 or not 1<=value['disk_free_percent']<=50:
        raise ValueError('CPU／内存阈值需为1到100，磁盘可用阈值需为1到50')
    if not 1<=value['connection_percent']<=100 or not 1<=value['blocked_sessions']<=100000 or not 1<=value['long_transaction_seconds']<=86400:
        raise ValueError('连接、阻塞或长事务阈值超出范围')
    return value


def evaluate(snapshot, previous, rules):
    old = {a['key']: a for a in (previous or {}).get('alerts', []) if a.get('category') not in ('link','member','host','database')}
    alerts = []
    for node in snapshot['nodes']:
        slots = node.get('sections', {}).get('slots', {})
        if not slots.get('valid'):
            continue
        for slot in slots.get('rows', []):
            key = '%s:%s' % (node['node']['id'], slot['slot_name'])
            state = dict(old.get(key, {}))
            try:
                gap = (datetime.fromisoformat(snapshot['observed_at']) - datetime.fromisoformat((previous or {})['observed_at'])).total_seconds()
                if gap <= 0 or gap > 45:
                    state.update(hits=0, healthy=0)
            except (KeyError, ValueError, TypeError):
                pass
            retained = slot.get('retained_bytes')
            threshold = rules['retained_mib'] * 1048576
            lost = slot.get('wal_status') == 'lost'
            issue = lost or slot.get('wal_status') == 'unreserved' or (retained is not None and retained > threshold)
            clear = slot.get('wal_status') not in ('lost', 'unreserved') and retained is not None and retained < threshold * .8
            hits = state.get('hits', 0) + 1 if issue else 0
            healthy = state.get('healthy', 0) + 1 if clear else 0
            active = lost or hits >= rules['consecutive_samples'] or (state.get('active', False) and healthy < rules['consecutive_samples'])
            if issue or active or state.get('active'):
                alerts.append({'key': key, 'node_id': node['node']['id'], 'slot_name': slot['slot_name'],
                               'active': active, 'hits': hits, 'healthy': healthy,
                               'status': 'active' if active else 'recovered' if state.get('active') else 'pending',
                               'severity': 'critical' if lost else 'warning',
                               'reason': '复制槽所需 WAL 已丢失' if lost else '复制槽 WAL 未保留或保留量超过阈值',
                               'observed_at': snapshot['observed_at']})
    # Unknown samples never count as healthy or silently resolve an existing alert.
    present = {a['key'] for a in alerts}
    valid_slots = {f"{n['node']['id']}:{r['slot_name']}" for n in snapshot['nodes'] if n.get('sections', {}).get('slots', {}).get('valid') for r in n['sections']['slots'].get('rows', [])}
    for key, state in old.items():
        if key not in present and key not in valid_slots and state.get('active'):
            alerts.append({**state, 'status': 'unknown', 'hits': 0, 'healthy': 0})
    return alerts


def evaluate_links(snapshot, previous, rules):
    old={a['key']:a for a in (previous or {}).get('alerts',[]) if a.get('category')=='link'}
    result=[]
    links=snapshot.get('resolved_topology',{}).get('links',[])
    seen=set()
    for link in links:
        key='link:'+link['id'];seen.add(key);state=dict(old.get(key,{}))
        try:
            gap=(datetime.fromisoformat(snapshot['observed_at'])-datetime.fromisoformat((previous or {})['observed_at'])).total_seconds()
            if gap<=0 or gap>45:
                state.update(hits=0,healthy=0)
        except (KeyError,TypeError,ValueError):
            pass
        if link['status'] in ('unknown','ambiguous'):
            if state.get('active'):
                result.append({**state,'status':'unknown','hits':0,'healthy':0})
            continue
        # Disabled subscriptions are an explicit configuration, not a failed connection.
        if link['status']=='disabled':
            if state.get('active'):
                result.append({**state,'status':'disabled','active':False,'hits':0,'healthy':0})
            continue
        hits=state.get('hits',0)+1 if link['status']=='interrupted' else 0
        healthy=state.get('healthy',0)+1 if link['status']=='connected' else 0
        active=hits>=rules['consecutive_samples'] or (state.get('active',False) and healthy<rules['consecutive_samples'])
        if hits or active or state.get('active'):
            result.append({'key':key,'category':'link','node_id':link['target'],'slot_name':link.get('slot_name') or link['id'],
                           'active':active,'hits':hits,'healthy':healthy,'status':'active' if active else 'recovered' if state.get('active') else 'pending',
                           'severity':'warning','reason':'有向复制连接证据中断','observed_at':snapshot['observed_at']})
    for key,state in old.items():
        if key not in seen and state.get('active'):
            result.append({**state,'status':'unknown','hits':0,'healthy':0})
    return result


def evaluate_members(snapshot, previous, rules):
    old={a['key']:a for a in (previous or {}).get('alerts',[]) if a.get('category')=='member'}
    result=[];seen=set()
    for member in snapshot.get('member_consensus',[]):
        key='member:'+member['key'];seen.add(key);state=dict(old.get(key,{}))
        try:
            gap=(datetime.fromisoformat(snapshot['observed_at'])-datetime.fromisoformat((previous or {})['observed_at'])).total_seconds()
            if gap<=0 or gap>45:
                state.update(hits=0,healthy=0)
        except (KeyError,TypeError,ValueError):
            pass
        if member['status']=='unknown':
            if state.get('active'):
                result.append({**state,'status':'unknown','hits':0,'healthy':0})
            continue
        hits=state.get('hits',0)+1 if member['status']=='disagree' else 0
        healthy=state.get('healthy',0)+1 if member['status']=='agreed' else 0
        active=hits>=rules['consecutive_samples'] or (state.get('active',False) and healthy<rules['consecutive_samples'])
        if hits or active or state.get('active'):
            result.append({'key':key,'category':'member','node_id':member['member_name'],'slot_name':'成员状态对照',
                           'active':active,'hits':hits,'healthy':healthy,'status':'active' if active else 'recovered' if state.get('active') else 'pending',
                           'severity':'warning','reason':'多个主成员观察到的登记状态不一致；不是脑裂结论','observed_at':snapshot['observed_at']})
    for key,state in old.items():
        if key not in seen and state.get('active'):
            result.append({**state,'status':'unknown','hits':0,'healthy':0})
    return result



def evaluate_hosts(snapshot, previous, rules):
    old={a['key']:a for a in (previous or {}).get('alerts',[]) if a.get('category')=='host'}
    result=[];seen=set()
    for host in snapshot.get('hosts',[]):
        if not host.get('valid') or not host.get('identity'):
            continue
        metrics=host.get('metrics',{})
        checks=[('cpu','CPU 忙碌率',metrics.get('cpu_percent'),rules['cpu_percent'],False),
                ('memory','内存使用率',metrics.get('memory_percent'),rules['memory_percent'],False)]
        for fs in host.get('raw',{}).get('filesystems',[]):
            if fs.get('device') is None:
                continue
            total,available=fs.get('total_bytes'),fs.get('available_bytes')
            percent=100*available/total if total and available is not None and 0<=available<=total else None
            checks.append(('disk:'+str(fs['device']),'文件系统可用空间 '+str(fs['device']),percent,rules['disk_free_percent'],True))
        for suffix,label,value,threshold,inverse in checks:
            key='host:'+host['identity']+':'+suffix;seen.add(key);state=dict(old.get(key,{}))
            stamp=host.get('observed_at');boot=host.get('raw',{}).get('boot_id')
            if value is None or not stamp:
                if state.get('active'):
                    result.append({**state,'status':'unknown','hits':0,'healthy':0})
                continue
            try:
                age=(datetime.fromisoformat(snapshot['observed_at'])-datetime.fromisoformat(stamp)).total_seconds()
                if age>180:
                    if state.get('active'):
                        result.append({**state,'status':'unknown','hits':0,'healthy':0})
                    continue
            except (TypeError,ValueError):
                continue
            if state.get('sample_at')==stamp:
                result.append(state)
                continue
            try:
                gap=(datetime.fromisoformat(stamp)-datetime.fromisoformat(state['sample_at'])).total_seconds()
                if 0<gap<45 and state.get('boot_id')==boot and not (inverse and value==0):
                    result.append(state)
                    continue
                if gap<=0 or gap>180 or state.get('boot_id')!=boot:
                    state.update(hits=0,healthy=0)
            except (KeyError,ValueError,TypeError):
                pass
            issue=value<=threshold if inverse else value>=threshold
            clear=value>=threshold*1.25 if inverse else value<=threshold*.8
            hits=state.get('hits',0)+1 if issue else 0
            healthy=state.get('healthy',0)+1 if clear else 0
            full=inverse and value==0
            active=full or hits>=rules['consecutive_samples'] or (state.get('active',False) and healthy<rules['consecutive_samples'])
            if issue or active or state.get('active'):
                result.append({'key':key,'category':'host','node_id':host['host'],'slot_name':label,
                               'active':active,'hits':hits,'healthy':healthy,'sample_at':stamp,'boot_id':boot,
                               'status':'active' if active else 'recovered' if state.get('active') else 'pending',
                               'severity':'critical' if full else 'warning','reason':f"{label} {value:.2f}%：超出阈值，按独立主机样本确认",
                               'observed_at':snapshot['observed_at']})
    for key,state in old.items():
        if key not in seen and state.get('active'):
            result.append({**state,'status':'unknown','hits':0,'healthy':0})
    return result



def evaluate_database(snapshot, previous, rules):
    old={a['key']:a for a in (previous or {}).get('alerts',[]) if a.get('category')=='database'}
    result=[];seen=set()
    for observation in snapshot.get('database_metrics',[]):
        metric=observation['metrics'];node=observation['node_id']
        clients,maximum=metric.get('instance_clients'),metric.get('max_connections')
        percent=100*clients/maximum if clients is not None and maximum else None
        checks=[('connections','实例客户端连接占比',percent,rules['connection_percent'],'%'),
                ('blocked','被阻塞客户端会话',metric.get('blocked'),rules['blocked_sessions'],'个'),
                ('long','最长客户端事务',metric.get('longest_transaction_seconds'),rules['long_transaction_seconds'],'秒')]
        for suffix,label,value,threshold,unit in checks:
            key='database:'+node+':'+suffix;seen.add(key);state=dict(old.get(key,{}))
            if value is None:
                if state.get('active'):
                    result.append({**state,'status':'unknown','hits':0,'healthy':0})
                continue
            try:
                gap=(datetime.fromisoformat(snapshot['observed_at'])-datetime.fromisoformat((previous or {})['observed_at'])).total_seconds()
                if gap<=0 or gap>45 or state.get('baseline')!=observation.get('baseline'):
                    state.update(hits=0,healthy=0)
            except (KeyError,ValueError,TypeError):
                pass
            hits=state.get('hits',0)+1 if value>=threshold else 0
            healthy=state.get('healthy',0)+1 if value<=threshold*.8 else 0
            active=hits>=rules['consecutive_samples'] or (state.get('active',False) and healthy<rules['consecutive_samples'])
            if hits or active or state.get('active'):
                result.append({'key':key,'category':'database','node_id':node,'slot_name':label,
                               'active':active,'hits':hits,'healthy':healthy,'baseline':observation.get('baseline'),
                               'status':'active' if active else 'recovered' if state.get('active') else 'pending',
                               'severity':'warning','reason':f"{label} {value:.2f}{unit}，阈值 {threshold}{unit}",'observed_at':snapshot['observed_at']})
    for key,state in old.items():
        if key not in seen and state.get('active'):
            result.append({**state,'status':'unknown','hits':0,'healthy':0})
    return result
