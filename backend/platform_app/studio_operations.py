"""Database operations inside the single Studio workspace."""

QUERIES = {
    "sessions": "SELECT pid,backend_start,usename,application_name,client_addr::text,state,wait_event_type,wait_event,query_start,left(query,2000) AS query FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() ORDER BY query_start NULLS LAST LIMIT 200",
    "locks": "SELECT a.pid,a.usename,a.state,l.locktype,l.mode,l.granted,l.relation::regclass::text AS relation,pg_blocking_pids(a.pid) AS blocking_pids FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid WHERE a.datname=current_database() AND a.pid<>pg_backend_pid() ORDER BY l.granted,a.pid LIMIT 500",
    "replication": "SELECT pid,application_name,client_addr::text,state,sync_state,sent_lsn::text,write_lsn::text,flush_lsn::text,replay_lsn::text FROM pg_stat_replication ORDER BY application_name",
    "receiver": "SELECT status,sender_host,sender_port,slot_name,written_lsn::text,flushed_lsn::text FROM pg_stat_wal_receiver",
    "settings": "SELECT name,setting,unit,context,vartype,pending_restart,source,short_desc FROM pg_settings ORDER BY name",
}
