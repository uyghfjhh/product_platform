"""Logical link lifecycle without removing subscriber tables or directories."""

import json
import time

from psycopg.conninfo import conninfo_to_dict

from ..resources import canonical_host
from .change_execution import _quote_literal
from .member_changes import quote_ident


def logical_editable(current, desired, name):
    if len(name) > 59:
        return False  # generated publication/subscription names must fit NAMEDATALEN
    before = current.get('logical_replications', {}).get(name)
    after = desired.get('logical_replications', {}).get(name)
    if not before and not after:
        return False
    for link in (before, after):
        if not link:
            continue
        if (link['pub']['streaming_cluster'] == link['sub']['streaming_cluster']
                and link['pub'].get('database', 'postgres') == link['sub'].get('database', link['pub'].get('database', 'postgres'))):
            return False  # CREATE SUBSCRIPTION to its own database can deadlock
        if len((link['sub'].get('slot') or {}).get('name') or name + '_slot') > 63:
            return False
        for role in ('pub', 'sub'):
            stream = link[role]['streaming_cluster']
            if (stream not in current.get('streaming_clusters', {})
                    or current['streaming_clusters'][stream] != desired.get('streaming_clusters', {}).get(stream)):
                return False
    return True


class LogicalChanges:
    def __init__(self, executor, name):
        self.e, self.name = executor, name
        self.before = executor.current.get('logical_replications', {}).get(name)
        self.after = executor.runtime.config.raw.get('logical_replications', {}).get(name)
        self.prefix = 'logical:' + name + ':'
        self.publication, self.subscription = name + '_pub', name + '_sub'
        self.state = executor.state.setdefault('logical_links', {}).setdefault(name, {})

    def node(self, link, role):
        return self.e.runtime.config.raw['streaming_clusters'][link[role]['streaming_cluster']]['primary']

    def db(self, link, role):
        return link[role].get('database', link['pub'].get('database', 'postgres'))

    def slot(self, link):
        return (link['sub'].get('slot') or {}).get('name') or self.name + '_slot'

    def sql(self, link, role, statement):
        return self.e.runtime._psql(self.node(link, role), statement, self.db(link, role), tuples=True).strip()

    def subscription_row(self, link):
        value = self.sql(link, 'sub', 'SELECT row_to_json(t) FROM (SELECT subslotname,subpublications,subconninfo FROM pg_subscription WHERE subname='
                         + _quote_literal(self.subscription) + " AND subdbid=(SELECT oid FROM pg_database WHERE datname=current_database())) t")
        return json.loads(value) if value else None

    def check_subscription(self, link, row):
        instance = self.e.runtime.config.instance(self.node(link, 'pub'))
        conn = conninfo_to_dict(row['subconninfo'])
        if (row['subpublications'] != [self.publication]
                or row['subslotname'] not in (self.slot(link), None)
                or canonical_host(conn.get('host', '')) != canonical_host(instance['host_config']['address'])
                or int(conn.get('port', '5432')) != instance['port']
                or conn.get('dbname') != self.db(link, 'pub')):
            raise ValueError('实际订阅与审阅基线不一致')

    def preflight(self):
        for link in (self.before, self.after):
            if not link:
                continue
            for role in ('pub', 'sub'):
                instance = self.e.runtime.config.instance(self.node(link, role))
                actual = self.sql(link, role, "SELECT current_setting('data_directory') || '|' || inet_server_port() || '|' || pg_is_in_recovery()")
                if actual != instance['data_dir'] + '|' + str(instance['port']) + '|false':
                    raise ValueError('逻辑复制连接身份或角色不符')
        if self.before:
            row = self.subscription_row(self.before)
            if not row:
                raise ValueError('基线订阅不存在，不能按已有链路变更')
            self.check_subscription(self.before, row)
            plugin = self.sql(self.before, 'pub', 'SELECT plugin FROM pg_replication_slots WHERE slot_name=' + _quote_literal(self.slot(self.before)))
            if plugin != 'pgoutput':
                raise ValueError('基线逻辑槽不存在或不是 pgoutput')
        if self.after:
            if (self.after['sub'].get('slot') or {}).get('failover'):
                for role in ('pub', 'sub'):
                    capability = self.sql(self.after, role, "SELECT count(*) FROM pg_proc WHERE pronamespace='pg_catalog'::regnamespace AND proname='pg_create_logical_replication_slot' AND 'failover'=ANY(proargnames)")
                    if capability != '1':
                        raise ValueError('发布端和订阅端必须实际支持 failover 逻辑槽，不能只依赖版本声明')
            # Retiring the old link precedes re-creating the new link, but a
            # differently located destination must not contain a namesake.
            if not self.before or self.after['sub'] != self.before['sub']:
                if self.subscription_row(self.after):
                    raise ValueError('目标数据库已有同名订阅')
            if not self.before or self.after['pub'] != self.before['pub']:
                if self.sql(self.after, 'pub', 'SELECT count(*) FROM pg_publication WHERE pubname=' + _quote_literal(self.publication)) != '0':
                    raise ValueError('目标数据库已有同名发布')
            level = self.sql(self.after, 'pub', 'SHOW wal_level')
            if level != 'logical':
                raise ValueError('发布端 wal_level 必须为 logical')
            # CREATE SUBSCRIPTION copies rows, not schema. Reject mismatched
            # tables before opening a slot or starting an apply worker.
            statement = "SELECT coalesce(json_agg(t ORDER BY schema,name,column_number)::text,'[]') FROM (SELECT n.nspname AS schema,c.relname AS name,a.attnum AS column_number,a.attname AS column_name,format_type(a.atttypid,a.atttypmod) AS column_type FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid WHERE c.relkind IN ('r','p') AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND a.attnum>0 AND NOT a.attisdropped) t"
            pub = json.loads(self.sql(self.after, 'pub', statement))
            sub = json.loads(self.sql(self.after, 'sub', statement))
            if any(row not in sub for row in pub):
                raise ValueError('订阅端缺少发布端表或列定义不匹配，请先部署一致结构')
            if self.after['sub'].get('copy_data', True):
                for schema, name in {(row['schema'], row['name']) for row in pub}:
                    count = self.sql(self.after, 'sub', 'SELECT EXISTS (SELECT 1 FROM ' + quote_ident(schema) + '.' + quote_ident(name) + ' LIMIT 1)')
                    if count != 'f':
                        raise ValueError('初始复制目标表非空，请审阅 copy_data=false 或使用空数据库')

    def retire(self):
        link = self.before
        def disable():
            row = self.subscription_row(link)
            if row:
                self.check_subscription(link, row)
                self.sql(link, 'sub', 'ALTER SUBSCRIPTION ' + quote_ident(self.subscription) + ' DISABLE')
        self.e.step(self.prefix + 'disable', disable)
        def detach():
            row = self.subscription_row(link)
            if row:
                self.check_subscription(link, row)
                self.sql(link, 'sub', 'ALTER SUBSCRIPTION ' + quote_ident(self.subscription) + ' SET (slot_name=NONE)')
        self.e.step(self.prefix + 'detach-slot', detach)
        def drop_subscription():
            if self.subscription_row(link):
                self.sql(link, 'sub', 'DROP SUBSCRIPTION ' + quote_ident(self.subscription))
        self.e.step(self.prefix + 'drop-subscription', drop_subscription)
        def drop_slot():
            deadline = time.monotonic() + 30
            while self.sql(link, 'pub', 'SELECT active FROM pg_replication_slots WHERE slot_name=' + _quote_literal(self.slot(link))) == 't':
                if time.monotonic() >= deadline:
                    raise ValueError('旧逻辑槽仍活跃，停止释放')
                time.sleep(.5)
            plugin = self.sql(link, 'pub', 'SELECT plugin FROM pg_replication_slots WHERE slot_name=' + _quote_literal(self.slot(link)))
            if plugin and plugin != 'pgoutput':
                raise ValueError('逻辑槽身份发生变化')
            if plugin:
                self.sql(link, 'pub', 'SELECT pg_drop_replication_slot(' + _quote_literal(self.slot(link)) + ')')
        self.e.step(self.prefix + 'drop-slot', drop_slot)
        self.e.step(self.prefix + 'drop-publication', lambda: self.sql(link, 'pub', 'DROP PUBLICATION IF EXISTS ' + quote_ident(self.publication)))

    def create(self):
        link = self.after
        def publication():
            if self.sql(link, 'pub', 'SELECT count(*) FROM pg_publication WHERE pubname=' + _quote_literal(self.publication)) == '0':
                self.sql(link, 'pub', 'CREATE PUBLICATION ' + quote_ident(self.publication) + ' FOR ALL TABLES')
        self.e.step(self.prefix + 'create-publication', publication)
        def subscription():
            row = self.subscription_row(link)
            if row:
                self.check_subscription(link, row)
                return
            instance = self.e.runtime.config.instance(self.node(link, 'pub'))
            def conn_quote(value):
                return "'" + str(value).replace('\\', '\\\\').replace("'", "\\'") + "'"
            conn = ' '.join(key + '=' + conn_quote(value) for key, value in {
                'host': instance['host_config']['address'], 'port': instance['port'],
                'user': 'postgres', 'dbname': self.db(link, 'pub'), 'application_name': self.subscription,
            }.items())
            failover = ',failover=true' if (link['sub'].get('slot') or {}).get('failover') else ''
            self.sql(link, 'sub', 'CREATE SUBSCRIPTION %s CONNECTION %s PUBLICATION %s WITH (slot_name=%s,copy_data=%s%s)' % (
                quote_ident(self.subscription), _quote_literal(conn), quote_ident(self.publication),
                _quote_literal(self.slot(link)), 'true' if link['sub'].get('copy_data', True) else 'false', failover))
        self.e.step(self.prefix + 'create-subscription', subscription)
        def verify():
            deadline = time.monotonic() + 60
            while True:
                if (link['sub'].get('slot') or {}).get('failover'):
                    flag = self.sql(link, 'pub', "SELECT to_jsonb(s)->>'failover' FROM pg_replication_slots s WHERE slot_name=" + _quote_literal(self.slot(link)))
                    if flag != 'true':
                        raise ValueError('实际发布端逻辑槽未启用 failover，不能按声明验收')
                workers = self.sql(link, 'sub', 'SELECT count(*) FROM pg_stat_subscription WHERE subname=' + _quote_literal(self.subscription) + ' AND pid IS NOT NULL AND relid IS NULL')
                pending = self.sql(link, 'sub', "SELECT count(*) FROM pg_subscription_rel r JOIN pg_subscription s ON s.oid=r.srsubid WHERE s.subname=" + _quote_literal(self.subscription) + " AND r.srsubstate<>'r'")
                if workers == '1' and pending == '0':
                    return
                if time.monotonic() >= deadline:
                    raise ValueError('订阅尚未启动或初始复制未完成')
                time.sleep(.5)
        self.e.step(self.prefix + 'verify', verify)

    def run(self):
        self.e.step(self.prefix + 'preflight', self.preflight)
        if self.before:
            self.retire()
        if self.after:
            self.create()
        self.e.emit('逻辑复制链路变更完成，业务表和数据库保留: ' + self.name)
