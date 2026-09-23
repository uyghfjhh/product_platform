"""PostgreSQL 兼容管理连接；单个 SQL 请求只执行一次。"""

import time

import psycopg
from psycopg.rows import tuple_row


def execute_query(environment: dict, sql: str, max_rows: int = 200) -> dict:
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
