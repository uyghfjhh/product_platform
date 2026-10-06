"""Prometheus 0.0.4 cache export; never query databases or export business rows."""
import math
import time
from collections import defaultdict
from datetime import datetime


def escape(value):
    return str(value).replace('\\','\\\\').replace('\n','\\n').replace('"','\\"')


def exposition(environment_id, enabled, snapshot, now=None):
    now=time.time() if now is None else now
    families=defaultdict(dict)
    def emit(name,value,**labels):
        if value is None or isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            return
        tags=tuple(sorted({'environment_id':environment_id,**labels}.items()))
        families[name][tags]=value
    emit('platform_monitor_enabled',int(enabled))
    age=None
    if snapshot:
        try:
            age=max(0,now-datetime.fromisoformat(snapshot['observed_at']).timestamp())
        except (KeyError,TypeError,ValueError):
            pass
    fresh=age is not None and age<=60
    emit('platform_monitor_sample_available',int(fresh))
    emit('platform_monitor_sample_age_seconds',age)
    if fresh:
        healthy_nodes={n['node']['id'] for n in snapshot.get('nodes',[]) if not n.get('error')}
        for node in snapshot.get('nodes',[]):
            identity=node['node']['id']
            emit('platform_monitor_instance_observable',int(identity in healthy_nodes),node_id=identity)
            for name,section in node.get('sections',{}).items():
                emit('platform_monitor_section_valid',int(section.get('valid',False) and identity in healthy_nodes),node_id=identity,section=name)
            if identity not in healthy_nodes:
                continue
            for slot in node.get('sections',{}).get('slots',{}).get('rows',[]) if node.get('sections',{}).get('slots',{}).get('valid') else []:
                labels={'node_id':identity,'slot_name':slot['slot_name'],'slot_type':slot['slot_type']}
                emit('platform_replication_slot_active',int(slot['active']),**labels)
                emit('platform_replication_slot_retained_bytes',slot.get('retained_bytes'),**labels)
        metric_names={'commit_per_second':'platform_postgres_commits_per_second','rollback_per_second':'platform_postgres_rollbacks_per_second','buffer_hit_percent':'platform_postgres_buffer_hit_percent','wal_mib_per_second':'platform_postgres_wal_bytes_per_second','instance_clients':'platform_postgres_instance_clients','database_clients':'platform_postgres_database_clients','max_connections':'platform_postgres_max_connections','active':'platform_postgres_active_clients','idle':'platform_postgres_idle_clients','idle_in_transaction':'platform_postgres_idle_transaction_clients','blocked':'platform_postgres_blocked_clients','long_transactions':'platform_postgres_long_transactions','longest_transaction_seconds':'platform_postgres_longest_transaction_seconds','deadlocks_per_second':'platform_postgres_deadlocks_per_second','temp_mib_per_second':'platform_postgres_temp_bytes_per_second'}
        for item in snapshot.get('database_metrics',[]):
            if item['node_id'] not in healthy_nodes:
                continue
            for key,name in metric_names.items():
                value=item['metrics'].get(key)
                if value is not None and key.endswith('mib_per_second'):
                    value*=1048576
                emit(name,value,node_id=item['node_id'])
        for item in snapshot.get('replication_metrics',[]):
            if item['node_id'] not in healthy_nodes:
                continue
            labels={'node_id':item['node_id'],'sender_pid':str(item['pid'])}
            for stage,value in item.get('stage_bytes',{}).items():
                emit('platform_replication_stage_bytes',value,stage=stage,**labels)
            for stage,value in item.get('bytes_per_second',{}).items():
                emit('platform_replication_progress_bytes_per_second',value,stage=stage,**labels)
        for link in snapshot.get('resolved_topology',{}).get('links',[]):
            emit('platform_replication_link_state',1,link_id=link['id'],kind=link['kind'],state=link['status'])
        for alert in snapshot.get('alerts',[]):
            emit('platform_monitor_alert_state',1,alert_id=alert['key'],category=alert.get('category','slot'),state=alert['status'],severity=alert['severity'])
        for host in snapshot.get('hosts',[]):
            try:
                host_age=now-datetime.fromisoformat(host['observed_at']).timestamp()
            except (KeyError,TypeError,ValueError):
                continue
            if not host.get('valid') or host_age>180:
                continue
            labels={'host':host['host']}
            for key in ('cpu_percent','memory_percent','iowait_percent'):
                emit('platform_host_'+key,host.get('metrics',{}).get(key),**labels)
            for fs in host.get('raw',{}).get('filesystems',[]):
                if fs.get('device') is not None:
                    emit('platform_host_filesystem_available_bytes',fs.get('available_bytes'),device=str(fs['device']),**labels)
                    emit('platform_host_filesystem_total_bytes',fs.get('total_bytes'),device=str(fs['device']),**labels)
            for device,values in host.get('metrics',{}).get('devices',{}).items():
                for key,value in values.items():
                    emit('platform_host_device_bytes_per_second',value*1048576 if value is not None else None,device=device,direction='read' if key.startswith('read') else 'write',**labels)
    lines=[]
    for name,values in sorted(families.items()):
        lines.extend([f'# HELP {name} Latest valid cached observation; missing values are omitted.',f'# TYPE {name} gauge'])
        for labels,value in sorted(values.items()):
            tags=','.join(f'{key}="{escape(val)}"' for key,val in labels)
            lines.append(f'{name}{{{tags}}} {value}')
    return '\n'.join(lines)+'\n'
