import hashlib
import json
from pathlib import Path

from pglast.parser import parse_sql_json


def validate_script(script):
    if len(script.encode('utf-8')) > 64000:
        raise ValueError('SQL 的 UTF-8 大小不能超过 64000 字节')
    if any(line.lstrip().startswith('\\') for line in script.splitlines()):
        raise ValueError('自定义 SQL 不允许 pgbench 元命令')
    try:
        parsed = json.loads(parse_sql_json(script))
    except Exception as exc:
        raise ValueError('SQL 语法无效；仅支持 PostgreSQL 查询，不支持 pgbench 元命令') from exc
    statements = parsed.get('stmts', [])
    if not statements or len(statements) > 50:
        raise ValueError('脚本需要包含 1–50 条查询')
    if any(set(item['stmt']) != {'SelectStmt'} for item in statements):
        raise ValueError('当前自定义负载仅支持 SELECT，不支持写入或事务控制')

    def inspect(value):
        if isinstance(value, dict):
            function = value.get('FuncCall')
            if function:
                name = function['funcname'][-1].get('String', {}).get('sval', '').lower()
                if name in {
                    'pg_promote', 'pg_reload_conf', 'pg_terminate_backend', 'pg_cancel_backend',
                    'pg_wal_replay_pause', 'pg_wal_replay_resume', 'pg_switch_wal',
                    'pg_create_restore_point', 'pg_backup_start', 'pg_backup_stop',
                    'pg_start_backup', 'pg_stop_backup', 'pg_drop_replication_slot',
                    'pg_create_physical_replication_slot', 'pg_create_logical_replication_slot',
                    'pg_replication_slot_advance', 'pg_logical_emit_message', 'set_config',
                    'lo_import', 'lo_export', 'lo_unlink', 'dblink_exec',
                }:
                    raise ValueError('只读负载不允许管理或外部变更函数：' + name)
            if set(value) & {'InsertStmt', 'UpdateStmt', 'DeleteStmt', 'MergeStmt', 'IntoClause', 'LockingClause', 'intoClause', 'lockingClause'}:
                raise ValueError('查询不能包含写入、SELECT INTO 或行锁')
            for item in value.values():
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)

    inspect(parsed)
    return len(statements)


def environment_fingerprint(environment):
    value = {key: environment.get(key) for key in (
        'id', 'product_id', 'host', 'port', 'database_name', 'database_user', 'deployment_target',
    )}
    for key in ('deployment_config', 'applied_deployment_config', 'resource_baseline_config'):
        path = environment.get(key)
        value[key] = [path, hashlib.sha256(Path(path).read_bytes()).hexdigest() if path and Path(path).is_file() else None]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
