"""Shared SSH host observations, cached once per transport identity."""
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from .deployment.probes import probe
from .resources import canonical_host


def derive(current, previous):
    metrics={'cpu_percent':None,'memory_percent':None,'iowait_percent':None,'devices':{}}
    total,available=current.get('memory_total'),current.get('memory_available')
    if total and available is not None and 0<=available<=total:
        metrics['memory_percent']=100*(total-available)/total
    uptime=current.get('uptime_seconds');old_uptime=(previous or {}).get('uptime_seconds')
    elapsed=uptime-old_uptime if uptime is not None and old_uptime is not None else 0
    stable=bool(previous and current.get('boot_id') and current.get('boot_id')==previous.get('boot_id') and 15<=elapsed<=180)
    if stable:
        busy=current['cpu_total']-previous['cpu_total'];idle=current['cpu_idle']-previous['cpu_idle']
        if busy>0 and 0<=idle<=busy:
            metrics['cpu_percent']=100*(busy-idle)/busy
            if current.get('cpu_iowait') is not None and previous.get('cpu_iowait') is not None:
                wait=current['cpu_iowait']-previous['cpu_iowait']
                metrics['iowait_percent']=100*wait/busy if 0<=wait<=busy else None
        for name,device in current.get('devices',{}).items():
            old=previous.get('devices',{}).get(name)
            if not old:
                continue
            rates={}
            for key,label in [('read_sectors','read_mib_per_second'),('write_sectors','write_mib_per_second')]:
                delta=device[key]-old[key]
                rates[label]=delta*512/elapsed/1048576 if delta>=0 else None
            metrics['devices'][name]=rates
    return metrics


def scoped(value, address, paths):
    public={**value, 'host':address}
    public.pop('requested_paths',None)
    if value.get('raw'):
        filesystems=[]
        for item in value['raw']['filesystems']:
            selected=[path for path in item['paths'] if path in paths]
            if selected:
                filesystems.append({**item,'paths':selected})
        public['raw']={**value['raw'],'filesystems':filesystems}
    return public


def collect(service, environment, nodes):
    raw=yaml.safe_load(Path(environment['deployment_config']).read_text()) if environment.get('deployment_config') else {}
    if not isinstance(raw,dict) or not isinstance(raw.get('hosts',{}),dict):
        raise ValueError('主机配置格式不支持')
    declared=[h for h in (raw.get('hosts') or {}).values() if isinstance(h,dict) and isinstance(h.get('address'),str)]
    groups={}
    for node in nodes:
        host=canonical_host(node['host'])
        group=groups.setdefault(host,{'address':node['host'],'paths':set()})
        if node.get('data_dir'):
            group['paths'].update([node['data_dir'],str(Path(node['data_dir'])/'pg_wal')])
    observations=[]
    for host,group in groups.items():
        matches=[h for h in declared if canonical_host(h.get('address',''))==host]
        if host!='local' and len(matches)!=1:
            observations.append({'host':group['address'],'valid':False,'error':'部署配置中没有唯一 SSH 主机映射','new_sample':False})
            continue
        ssh=matches[0].get('ssh',{}) if matches else {}
        if not isinstance(ssh,dict):
            raise ValueError('SSH 配置格式不支持')
        identity=hashlib.sha256(json.dumps([host,ssh],sort_keys=True).encode()).hexdigest()
        with service.connection() as db:
            db.execute('DELETE FROM host_paths WHERE environment=? AND identity=?',(environment['id'],identity))
            db.executemany('INSERT INTO host_paths VALUES (?,?,?,?)',[(environment['id'],identity,path,time.time()) for path in group['paths']])
            requested={r[0] for r in db.execute('SELECT path FROM host_paths WHERE identity=? AND stamp>?',(identity,time.time()-120))}
            cached=db.execute('SELECT stamp,payload FROM host_samples WHERE identity=?',(identity,)).fetchone()
        previous=json.loads(cached[1]) if cached else None
        if cached and time.time()-cached[0]<60 and requested.issubset(set(previous.get('requested_paths',[]))):
            observations.append(scoped({**previous,'new_sample':False},group['address'],group['paths']))
            continue
        value={'host':group['address'],'observed_at':datetime.now(UTC).isoformat(),'new_sample':True,'identity':identity,'requested_paths':sorted(requested)}
        try:
            data=probe(group['address'],{'paths':sorted(requested)},ssh,agent='monitoring_agent.py')
            value.update(valid=True,raw=data,metrics=derive(data,(previous or {}).get('raw')))
        except ValueError:
            value.update(valid=False,error='SSH 主机采集失败，请核对连接、权限和 Linux 统计接口')
        with service.connection() as db:
            db.execute('INSERT OR REPLACE INTO host_samples VALUES (?,?,?)',(identity,time.time(),json.dumps(value)))
        observations.append(scoped(value,group['address'],group['paths']))
    return observations
