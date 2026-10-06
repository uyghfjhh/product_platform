"""PostgreSQL base metrics with reset-aware sampling and explicit scope."""
from datetime import datetime
import re

QUERIES = {
    'database': "SELECT datid,datname,xact_commit,xact_rollback,blks_read,blks_hit,deadlocks,temp_bytes,stats_reset FROM pg_stat_database WHERE datname=current_database()",
    'connections': "SELECT count(*) AS instance_clients,count(*) FILTER (WHERE datname=current_database()) AS database_clients,count(*) FILTER (WHERE state='active') AS active,count(*) FILTER (WHERE state='idle') AS idle,count(*) FILTER (WHERE state LIKE 'idle in transaction%') AS idle_in_transaction,current_setting('max_connections')::int AS max_connections FROM pg_stat_activity WHERE backend_type='client backend' AND pid<>pg_backend_pid() AND application_name<>'platform_monitor'",
    'wal': "SELECT wal_bytes,wal_records,stats_reset FROM pg_stat_wal",
    'sessions': "SELECT pid,backend_start,usename,datname,application_name,state,wait_event_type,wait_event,xact_start,query_start,extract(epoch FROM (clock_timestamp()-xact_start)) AS transaction_seconds,pg_blocking_pids(pid) AS blockers,query FROM pg_stat_activity WHERE backend_type='client backend' AND datname=current_database() AND pid<>pg_backend_pid() AND application_name<>'platform_monitor' ORDER BY xact_start NULLS LAST LIMIT 100",
    'blocking': "SELECT a.pid AS waiting_pid,a.backend_start AS waiting_start,p.pid AS blocking_pid,b.backend_start AS blocking_start,a.wait_event_type,a.wait_event,extract(epoch FROM (clock_timestamp()-a.xact_start)) AS transaction_seconds,a.query AS waiting_query,b.query AS blocking_query FROM pg_stat_activity a CROSS JOIN LATERAL unnest(pg_blocking_pids(a.pid)) p(pid) LEFT JOIN pg_stat_activity b ON b.pid=p.pid WHERE a.datname=current_database() AND a.pid<>pg_backend_pid() LIMIT 100",
    'activity_summary': "SELECT count(*) FILTER (WHERE cardinality(pg_blocking_pids(pid))>0) AS blocked,count(*) FILTER (WHERE xact_start IS NOT NULL AND clock_timestamp()-xact_start>interval '60 seconds') AS long_transactions,max(extract(epoch FROM (clock_timestamp()-xact_start))) AS longest_transaction_seconds,count(*) FILTER (WHERE state IS NULL) AS hidden_clients FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend' AND pid<>pg_backend_pid() AND application_name<>'platform_monitor'",
}


def section(node, name):
    value=node.get('sections',{}).get(name,{})
    return value, value.get('rows',[]) if value.get('valid') and not node.get('error') else []


def elapsed(current, previous):
    try:
        value=(datetime.fromisoformat(current['observed_at'])-datetime.fromisoformat(previous['observed_at'])).total_seconds()
        return value if 0<value<=45 else None
    except (KeyError,ValueError,TypeError):
        return None


def delta(current, previous, key):
    a,b=current.get(key),previous.get(key)
    return a-b if isinstance(a,(int,float)) and isinstance(b,(int,float)) and a>=b else None


def derive(snapshot, previous):
    old_nodes={n['node']['id']:n for n in (previous or {}).get('nodes',[])}
    values=[]
    for node in snapshot['nodes']:
        old=old_nodes.get(node['node']['id'],{})
        _,runtime=section(node,'runtime');_,old_runtime=section(old,'runtime')
        runtime_stable=bool(runtime and old_runtime and runtime[0].get('started_at') and runtime[0].get('started_at')==old_runtime[0].get('started_at') and runtime[0].get('recovery')==old_runtime[0].get('recovery'))
        db,db_rows=section(node,'database');old_db,old_db_rows=section(old,'database')
        interval=elapsed(db,old_db)
        stable=bool(runtime_stable and db_rows and old_db_rows and db_rows[0].get('datid')==old_db_rows[0].get('datid') and db_rows[0].get('stats_reset')==old_db_rows[0].get('stats_reset') and interval)
        metrics={'commit_per_second':None,'rollback_per_second':None,'buffer_hit_percent':None,'deadlocks_per_second':None,'temp_mib_per_second':None,'wal_mib_per_second':None}
        if stable:
            for key,label in [('xact_commit','commit_per_second'),('xact_rollback','rollback_per_second'),('deadlocks','deadlocks_per_second'),('temp_bytes','temp_mib_per_second')]:
                diff=delta(db_rows[0],old_db_rows[0],key)
                metrics[label]=diff/interval if diff is not None else None
            if metrics['temp_mib_per_second'] is not None:
                metrics['temp_mib_per_second']/=1048576
            hits=delta(db_rows[0],old_db_rows[0],'blks_hit');reads=delta(db_rows[0],old_db_rows[0],'blks_read')
            if hits is not None and reads is not None and hits+reads>0:
                metrics['buffer_hit_percent']=100*hits/(hits+reads)
        wal,wal_rows=section(node,'wal');old_wal,old_wal_rows=section(old,'wal');wal_interval=elapsed(wal,old_wal)
        if runtime_stable and runtime[0].get('recovery') is False and wal_rows and old_wal_rows and wal_interval and wal_rows[0].get('stats_reset')==old_wal_rows[0].get('stats_reset'):
            diff=delta(wal_rows[0],old_wal_rows[0],'wal_bytes')
            metrics['wal_mib_per_second']=diff/wal_interval/1048576 if diff is not None else None
        if runtime_stable and runtime[0].get('recovery') is True:
            current_section,_=section(node,'runtime');old_section,_=section(old,'runtime')
            receive_interval=elapsed(current_section,old_section)
            _,timeline=section(node,'timeline');_,old_timeline=section(old,'timeline')
            a,b=lsn(runtime[0].get('wal_position')),lsn(old_runtime[0].get('wal_position'))
            if receive_interval and timeline and timeline==old_timeline and a is not None and b is not None and a>=b:
                metrics['wal_mib_per_second']=(a-b)/receive_interval/1048576
        _,connections=section(node,'connections');_,activity=section(node,'activity_summary')
        if connections:
            metrics.update({key:connections[0].get(key) for key in ('instance_clients','database_clients','active','idle','idle_in_transaction','max_connections')})
        if activity:
            metrics.update({key:activity[0].get(key) for key in ('blocked','long_transactions','longest_transaction_seconds')})
            if activity[0].get('hidden_clients')!=0:
                metrics['long_transactions']=None
                metrics['longest_transaction_seconds']=None
            elif activity[0].get('longest_transaction_seconds') is None:
                metrics['longest_transaction_seconds']=0
        values.append({'node_id':node['node']['id'],'baseline':str((runtime[0].get('started_at'),runtime[0].get('recovery'),db_rows[0].get('datid') if db_rows else None,db_rows[0].get('stats_reset') if db_rows else None)) if runtime else None,'metrics':metrics})
    return values



def lsn(value):
    if not isinstance(value,str):
        return None
    try:
        high,low=value.split('/')
        if not re.fullmatch(r'[0-9a-fA-F]{1,8}',high) or not re.fullmatch(r'[0-9a-fA-F]{1,8}',low):
            return None
        return (int(high,16)<<32)+int(low,16)
    except ValueError:
        return None


def diagnostics(environment, view):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    if view not in ('queries','tables'):
        raise ValueError('未知诊断视图')
    result={'view':view,'observed_at':datetime.now().astimezone().isoformat(),'sections':{}}
    try:
        with psycopg.connect(host=environment['host'],port=environment['port'],dbname=environment['database_name'],user=environment['database_user'],connect_timeout=3,row_factory=dict_row,options='-c default_transaction_read_only=on -c statement_timeout=2000 -c application_name=platform_monitor -c client_encoding=UTF8') as conn:
            with conn.transaction():
                if view=='queries':
                    row=conn.execute("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pg_stat_statements'").fetchone()
                    if not row:
                        return {**result,'available':False,'reason':'未安装 pg_stat_statements；监控不会自动安装或重启数据库'}
                    namespace=sql.Identifier(row['nspname'])
                    queries={
                        'ranking':sql.SQL('SELECT queryid::text,calls,total_exec_time,mean_exec_time,rows,shared_blks_hit,shared_blks_read,temp_blks_written,query FROM {}.pg_stat_statements WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database()) ORDER BY total_exec_time DESC LIMIT 30').format(namespace),
                        'reset':sql.SQL('SELECT stats_reset,dealloc FROM {}.pg_stat_statements_info').format(namespace),
                    }
                else:
                    queries={
                        'tables':"SELECT schemaname,relname,n_live_tup,n_dead_tup,last_vacuum,last_autovacuum,last_analyze,last_autoanalyze,pg_total_relation_size(relid) AS total_bytes FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 30",
                        'vacuum':"SELECT pid,relid::regclass::text AS relation,phase,heap_blks_total,heap_blks_scanned,heap_blks_vacuumed FROM pg_stat_progress_vacuum WHERE datname=current_database() LIMIT 100",
                    }
                for name,query in queries.items():
                    try:
                        with conn.transaction():
                            result['sections'][name]={'valid':True,'rows':conn.execute(query).fetchall()}
                    except psycopg.Error as exc:
                        result['sections'][name]={'valid':False,'error':exc.diag.message_primary or str(exc)}
        result['available']=True
    except psycopg.Error:
        result.update(available=False,reason='连接或权限不可用，无法读取诊断')
    return result
