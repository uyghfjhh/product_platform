"""Native Kingbase identities and SQL; no database configuration changes."""

import ipaddress

import psycopg
from platform_app.resources import canonical_host
from platform_app.studio_operations import QUERIES
from psycopg.rows import dict_row

RECEIVER = ('SELECT status,sender_host,sender_port,slot_name,received_lsn::text,'
            'latest_end_lsn::text,last_msg_receipt_time FROM sys_stat_wal_receiver')
IDENTITY = ("SELECT version() AS version,pg_is_in_recovery() AS recovery,"
            "current_setting('data_directory') AS data_dir,"
            "current_setting('database_mode') AS database_mode,"
            "current_setting('transaction_read_only') AS read_only,"
            "(SELECT system_identifier::text FROM pg_control_system()) AS system_identifier")


def stats_prefix(mode):
    return 'pg' if mode == 'pg' else 'sys'


def receiver_query(mode):
    return RECEIVER.replace('sys_stat_wal_receiver', stats_prefix(mode) + '_stat_wal_receiver')


def upstream_matches(source, target, receiver):
    upstream = receiver.get('sender_host') or ''
    local = upstream.startswith('/') or upstream.lower() == 'localhost'
    try:
        local = local or ipaddress.ip_address(upstream).is_loopback
    except ValueError:
        pass
    same_host = (canonical_host(source['host']) == canonical_host(target['host']) if local
                 else bool(upstream) and canonical_host(upstream) == canonical_host(source['host']))
    return receiver.get('sender_port') == source['port'] and same_host


def connect(environment, node=None):
    endpoint = node or environment
    return psycopg.connect(
        host=endpoint['host'], port=endpoint['port'],
        dbname=environment.get('database_name') or 'kingbase',
        user=environment.get('database_user') or 'kingbase',
        connect_timeout=3, autocommit=True, row_factory=dict_row,
        options='-c statement_timeout=3000 -c default_transaction_read_only=on -c client_encoding=UTF8',
    )


def identity(environment, node=None):
    with connect(environment, node) as connection:
        result = connection.execute(IDENTITY).fetchone()
        if not result['version'].startswith('KingbaseES'):
            raise ValueError('目标实例不是 KingbaseES')
        result['receivers'] = connection.execute(receiver_query(result['database_mode'])).fetchall()
        result['senders'] = connection.execute(
            'SELECT application_name,state,sync_state,client_addr::text,'
            f"sent_lsn::text,replay_lsn::text FROM {stats_prefix(result['database_mode'])}_stat_replication").fetchall()
        return result


def inspect_queries(environment=None):
    from products.kingbase.topology import database_mode
    return {**QUERIES, 'receiver': receiver_query(database_mode(environment or {}))}
