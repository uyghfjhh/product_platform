"""Minute aggregates of explicitly valid numeric metrics; preserve peaks."""
import math
from datetime import UTC, datetime


def metrics(snapshot):
    for node in snapshot.get('nodes', []):
        runtime = node.get('sections', {}).get('runtime', {})
        if node.get('error') or not runtime.get('valid') or not runtime.get('rows'):
            continue
        baseline = str(runtime['rows'][0].get('started_at')) + ':' + str(runtime['rows'][0].get('recovery'))
        slots = node.get('sections', {}).get('slots', {})
        for slot in slots.get('rows', []) if slots.get('valid') else []:
            value = slot.get('retained_bytes')
            if isinstance(value, (int,float)) and math.isfinite(value) and value >= 0:
                yield (f"slot:{snapshot.get('fingerprint','')}:{node['node']['id']}:{slot['slot_name']}:{baseline}", f"{node['node']['id']} / {slot['slot_name']} · 保留 WAL", 'MiB', value/1048576)
    for item in snapshot.get('replication_metrics', []):
        for stage,value in item.get('bytes_per_second', {}).items():
            if isinstance(value, (int,float)) and math.isfinite(value) and value >= 0:
                identity = f"{item['node_id']}:{item['pid']}:{item['backend_start']}:{item.get('timeline_id')}:{stage}"
                yield ('rate:'+snapshot.get('fingerprint','')+':'+identity, f"{item['node_id']} / {item['application_name']} / {stage} · WAL 推进", 'MiB/s', value/1048576)

    units={'commit_per_second':'tx/s','rollback_per_second':'tx/s','buffer_hit_percent':'%','deadlocks_per_second':'次/s','temp_mib_per_second':'MiB/s','wal_mib_per_second':'MiB/s','instance_clients':'连接','active':'连接','idle':'连接','idle_in_transaction':'连接','blocked':'会话','long_transactions':'事务','longest_transaction_seconds':'秒'}
    for item in snapshot.get('database_metrics',[]):
        for name,value in item.get('metrics',{}).items():
            if name in units and isinstance(value,(int,float)) and math.isfinite(value) and value>=0:
                yield (f"db:{snapshot.get('fingerprint','')}:{item['node_id']}:{item['baseline']}:{name}",f"{item['node_id']} / {name}",units[name],value)

    for host in snapshot.get('hosts',[]):
        if not host.get('valid'):
            continue
        for name,value in host.get('metrics',{}).items():
            if name in ('cpu_percent','memory_percent','iowait_percent') and isinstance(value,(int,float)) and math.isfinite(value):
                yield (f"host:{host['identity']}:{host['raw']['boot_id']}:{name}",f"{host['host']} / {name}",'%',value)
        for device,values in host.get('metrics',{}).get('devices',{}).items():
            for name,value in values.items():
                if isinstance(value,(int,float)) and math.isfinite(value):
                    yield (f"host:{host['identity']}:{host['raw']['boot_id']}:{device}:{name}",f"{host['host']} / {device} / {name}",'MiB/s',value)


def append(db, identity, snapshot):
    stamp=datetime.fromisoformat(snapshot['observed_at']).timestamp()
    bucket=int(stamp//60)*60
    host_epochs={h['identity']:datetime.fromisoformat(h['observed_at']).timestamp() for h in snapshot.get('hosts',[]) if h.get('valid')}
    allowed_hosts=set()
    for host_id,epoch in host_epochs.items():
        last=db.execute('SELECT stamp FROM host_metric_seen WHERE environment=? AND identity=?',(identity,host_id)).fetchone()
        if not last or epoch>last[0]:
            allowed_hosts.add(host_id)
    for key,label,unit,value in metrics(snapshot):
        metric_bucket=bucket
        if key.startswith('host:'):
            host_id=key.split(':')[1]
            if host_id not in allowed_hosts:
                continue
            metric_bucket=int(host_epochs[host_id]//60)*60
        db.execute('''INSERT INTO minute_metrics VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(environment,bucket,series) DO UPDATE SET total=total+excluded.total,
            count=count+1,peak=max(peak,excluded.peak)''', (identity,metric_bucket,key,label,unit,value,1,value))
    for host_id in allowed_hosts:
        db.execute('INSERT OR REPLACE INTO host_metric_seen VALUES (?,?,?)',(identity,host_id,host_epochs[host_id]))


def read(db, identity, start, window_minutes):
    resolution=max(60, math.ceil(window_minutes/120)*60)
    rows=db.execute('SELECT (bucket / ?)*?,series,label,unit,sum(total),sum(count),max(peak) FROM minute_metrics WHERE environment=? AND bucket>=? AND series IN (SELECT series FROM minute_metrics WHERE environment=? AND bucket>=? GROUP BY series ORDER BY max(bucket) DESC LIMIT 128) GROUP BY (bucket / ?),series,label,unit ORDER BY 1', (resolution,resolution,identity,start,identity,start,resolution)).fetchall()
    grouped={}
    for bucket,key,label,unit,total,count,peak in rows:
        series=grouped.setdefault(key,{'key':key,'label':label,'unit':unit,'points':[]})
        series['points'].append({'time':datetime.fromtimestamp(bucket, UTC).isoformat(),'value':total/count,'peak':peak,'valid_samples':count})
    return list(grouped.values())
