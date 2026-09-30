"""PostgreSQL 兼容管理连接；单个 SQL 请求只执行一次。"""

import time

try:
    import psycopg
    from psycopg.rows import tuple_row
except ImportError:
    psycopg = None
    tuple_row = None


def execute_query(environment: dict, sql: str, max_rows: int = 200) -> dict:
    if psycopg is None:
        raise RuntimeError("未检测到 psycopg 依赖库，请执行 ./web.sh setup 或 pip install 'psycopg[binary]' 后重试")
    started = time.monotonic()
    with psycopg.connect(
        host=environment["host"],
        port=environment["port"],
        dbname=environment["database_name"],
        user=environment["database_user"],
        connect_timeout=5,
        options="-c statement_timeout=15000",
        autocommit=True,
        row_factory=tuple_row,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = (
                [column.name for column in cursor.description]
                if cursor.description
                else []
            )
            if columns:
                fetched = cursor.fetchmany(max_rows + 1)
                truncated = len(fetched) > max_rows
                rows = [
                    [str(value) if value is not None else None for value in row]
                    for row in fetched[:max_rows]
                ]
            else:
                rows, truncated = [], False
            command_tag = cursor.statusmessage
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "command_tag": command_tag,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def list_objects(environment: dict) -> list[dict]:
    """只读系统目录，返回界面对象树需要的稳定字段。"""
    if psycopg is None:
        return []
    with psycopg.connect(
        host=environment["host"], port=environment["port"],
        dbname=environment["database_name"], user=environment["database_user"],
        connect_timeout=5, options="-c statement_timeout=10000",
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT n.nspname, c.relname, c.relkind
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND n.nspname NOT LIKE 'pg_toast%'
                  AND c.relkind IN ('r', 'p', 'v', 'm')
                ORDER BY n.nspname, c.relname
                LIMIT 1000
            """)
            rows = cursor.fetchall()
    return [{"schema": schema, "name": name, "kind": kind} for schema, name, kind in rows]


def list_columns(environment: dict, schema: str, table: str) -> list[dict]:
    if psycopg is None:
        return []
    with psycopg.connect(
        host=environment["host"], port=environment["port"],
        dbname=environment["database_name"], user=environment["database_user"],
        connect_timeout=5, options="-c statement_timeout=10000",
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema=%s AND table_name=%s
                ORDER BY ordinal_position
            """, (schema, table))
            rows = cursor.fetchall()
    return [{"name": name, "type": data_type, "nullable": nullable == "YES"}
            for name, data_type, nullable in rows]


def _query_dicts(environment: dict, sql: str, params: tuple = ()) -> list[dict]:
    """实例管理视图通用查询；返回行字典，供前端动态列表。"""
    if psycopg is None:
        raise RuntimeError("未检测到 psycopg 依赖库")
    with psycopg.connect(
        host=environment["host"], port=environment["port"],
        dbname=environment["database_name"], user=environment["database_user"],
        connect_timeout=5, options="-c statement_timeout=10000",
        autocommit=True, row_factory=tuple_row,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            columns = [column.name for column in cursor.description or []]
            rows = cursor.fetchall()
    return [dict(zip(columns, row)) for row in rows]


def list_sessions(environment: dict) -> list[dict]:
    """pg_stat_activity 会话视图，含等待事件与查询快照。"""
    return _query_dicts(environment, """
        SELECT pid, usename, datname, application_name, client_addr::text AS client_addr,
               backend_start, query_start, state_change, state,
               wait_event_type, wait_event, backend_type, left(query, 200) AS query
        FROM pg_catalog.pg_stat_activity
        ORDER BY query_start NULLS LAST
        LIMIT 500
    """)


def list_locks(environment: dict) -> list[dict]:
    """pg_locks 锁视图，附阻塞链与被锁对象。"""
    return _query_dicts(environment, """
        SELECT l.pid, l.locktype, l.mode, l.granted,
               l.relation::regclass::text AS relation,
               l.page, l.tuple, l.virtualtransaction, l.transactionid,
               pg_blocking_pids(l.pid)::text AS blocked_by,
               a.usename, a.state, left(a.query, 150) AS query
        FROM pg_catalog.pg_locks l
        LEFT JOIN pg_catalog.pg_stat_activity a ON a.pid = l.pid
        ORDER BY l.granted, l.pid, l.locktype
        LIMIT 1000
    """)


def list_replication(environment: dict) -> dict:
    """复制拓扑：发送端连接、接收端、复制槽——主备节点各自视角。"""
    senders = _query_dicts(environment, """
        SELECT pid, usename, application_name, client_addr::text AS client_addr,
               state, sync_state, sync_priority,
               sent_lsn::text AS sent_lsn, write_lsn::text AS write_lsn,
               flush_lsn::text AS flush_lsn, replay_lsn::text AS replay_lsn,
               write_lag::text AS write_lag, flush_lag::text AS flush_lag,
               replay_lag::text AS replay_lag
        FROM pg_catalog.pg_stat_replication
        ORDER BY application_name
        LIMIT 200
    """)
    receivers = _query_dicts(environment, """
        SELECT pid, status, receive_start_lsn::text AS receive_start_lsn,
               written_lsn::text AS written_lsn, flushed_lsn::text AS flushed_lsn,
               received_tli, sender_host, sender_port, conninfo
        FROM pg_catalog.pg_stat_wal_receiver
        LIMIT 50
    """)
    slots = _query_dicts(environment, """
        SELECT slot_name, plugin, slot_type, datname, temporary, active,
               restart_lsn::text AS restart_lsn, confirmed_flush_lsn::text AS confirmed_flush_lsn,
               wal_status, safe_wal_size
        FROM pg_catalog.pg_replication_slots
        ORDER BY slot_name
        LIMIT 200
    """)
    return {"senders": senders, "receivers": receivers, "slots": slots}


def list_settings(environment: dict, search: str = "") -> list[dict]:
    """pg_settings 参数视图；search 为空返回非默认值加常用项。"""
    if search:
        return _query_dicts(environment, """
            SELECT name, setting, unit, category, vartype, source,
                   pending_restart, min_val, max_val, enumvals
            FROM pg_catalog.pg_settings
            WHERE name ILIKE %s
            ORDER BY name
            LIMIT 200
        """, ("%" + search.replace("%", "\\%").replace("_", "\\_") + "%",))
    return _query_dicts(environment, """
        SELECT name, setting, unit, category, vartype, source,
               pending_restart, min_val, max_val, enumvals
        FROM pg_catalog.pg_settings
        WHERE source <> 'default' OR name IN (
            'max_connections', 'shared_buffers', 'work_mem', 'wal_level',
            'max_wal_senders', 'max_replication_slots', 'hot_standby',
            'shared_preload_libraries', 'port', 'listen_addresses',
            'log_destination', 'logging_collector', 'statement_timeout')
        ORDER BY name
        LIMIT 200
    """)


def cancel_backend(environment: dict, pid: int, terminate: bool = False) -> bool:
    """pg_cancel_backend 取消查询；terminate=True 时用 pg_terminate_backend 断会话。"""
    function = "pg_terminate_backend" if terminate else "pg_cancel_backend"
    rows = _query_dicts(environment, f"SELECT {function}(%s) AS done", (pid,))
    return bool(rows and rows[0]["done"])
