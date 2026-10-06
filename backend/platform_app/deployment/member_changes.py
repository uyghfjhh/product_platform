"""Reviewed MMR/Citus membership changes on already declared live instances."""

import time

from .change_execution import _quote_literal


def quote_ident(value):
    return '"' + value.replace('"', '""') + '"'


def new_member_streams(current, desired):
    """New physical groups owned exclusively by newly added MMR/Citus members."""
    groups, ambiguous = {}, set()
    for section, key in (('mmr_clusters', 'members'), ('citus_clusters', 'workers')):
        for name in current.get(section, {}).keys() & desired.get(section, {}).keys():
            before, after = current[section][name], desired[section][name]
            if {k: v for k, v in before.items() if k != key} != {k: v for k, v in after.items() if k != key}:
                continue
            for member in after.get(key, {}).keys() - before.get(key, {}).keys():
                stream = after[key][member]['streaming_cluster']
                if stream in current.get('streaming_clusters', {}):
                    continue
                cluster = desired.get('streaming_clusters', {}).get(stream)
                if not cluster:
                    continue
                nodes = [cluster['primary']] + [row['instance'] for row in cluster.get('standbys', [])]
                if all(node not in current['instances'] and node in desired['instances'] for node in nodes):
                    if stream in groups:
                        ambiguous.add(stream)
                    groups[stream] = {'section': section, 'cluster': name, 'nodes': nodes}
    return {name: group for name, group in groups.items() if name not in ambiguous}


def retired_member_streams(current, desired):
    groups = {}
    def references(config, stream):
        count = 0
        for section in ('mmr_clusters', 'citus_clusters', 'logical_replications'):
            for cluster in config.get(section, {}).values():
                rows = list(cluster.get('members', {}).values()) + list(cluster.get('workers', {}).values())
                rows += [cluster.get(key, {}) for key in ('coordinator', 'pub', 'sub')]
                count += sum(row.get('streaming_cluster') == stream for row in rows)
        return count
    for section, key in (('mmr_clusters', 'members'), ('citus_clusters', 'workers')):
        for name in current.get(section, {}).keys() & desired.get(section, {}).keys():
            before, after = current[section][name], desired[section][name]
            for member in before.get(key, {}).keys() - after.get(key, {}).keys():
                stream = before[key][member]['streaming_cluster']
                if stream in desired.get('streaming_clusters', {}):
                    continue
                cluster = current.get('streaming_clusters', {}).get(stream)
                if not cluster or references(current, stream) != 1 or references(desired, stream):
                    continue
                nodes = [cluster['primary']] + [row['instance'] for row in cluster.get('standbys', [])]
                if all(node not in desired['instances'] for node in nodes):
                    groups[stream] = {'section': section, 'cluster': name, 'nodes': nodes}
    return groups


def member_delta(current, desired, section, name):
    before = current.get(section, {}).get(name)
    after = desired.get(section, {}).get(name)
    if not before or not after:
        return None
    key = 'members' if section == 'mmr_clusters' else 'workers'
    if {k: v for k, v in before.items() if k != key} != {k: v for k, v in after.items() if k != key}:
        return None
    old, new = before[key], after[key]
    for members in (old, new):
        if len({row['streaming_cluster'] for row in members.values()}) != len(members):
            return None
    if section == 'citus_clusters' and after['coordinator']['streaming_cluster'] in {
        row['streaming_cluster'] for row in new.values()
    }:
        return None
    if any(old[n] != new[n] for n in old.keys() & new.keys()):
        return None  # a replacement is reviewed as separate add and retire steps
    if not new or (section == 'mmr_clusters' and not old.keys() & new.keys()):
        return None
    streams = current.get('streaming_clusters', {})
    new_streams = new_member_streams(current, desired)
    retired_streams = retired_member_streams(current, desired)
    for row in list(old.values()) + list(new.values()):
        stream = row['streaming_cluster']
        if stream not in streams:
            if stream not in new_streams:
                return None
        elif stream not in retired_streams and streams[stream] != desired.get('streaming_clusters', {}).get(stream):
            return None
    if section == 'mmr_clusters':
        if len({v['node_name'] for v in new.values()}) != len(new):
            return None
        # Existing members must retain their identity; don't re-use a retired
        # node name for another physical database in one plan.
        if {old[n]['node_name'] for n in old.keys()-new.keys()} & {new[n]['node_name'] for n in new.keys()-old.keys()}:
            return None
    return {'added': sorted(new.keys()-old.keys()), 'removed': sorted(old.keys()-new.keys())}


class MemberChanges:
    def __init__(self, executor, section, name):
        self.e, self.section, self.name = executor, section, name
        self.before = executor.current[section][name]
        self.after = executor.runtime.config.raw[section][name]
        self.key = 'members' if section == 'mmr_clusters' else 'workers'
        self.delta = member_delta(executor.current, executor.runtime.config.raw, section, name)
        if self.delta is None:
            raise ValueError('成员变更与审阅配置不一致')
        self.database = self.after.get('database', 'postgres')
        self.prefix = section + ':' + name + ':'
        self.state = executor.state.setdefault('memberships', {}).setdefault(self.prefix, {})

    def node(self, member):
        return self.member_runtime(member).config.raw['streaming_clusters'][member['streaming_cluster']]['primary']

    def member_runtime(self, member):
        return (self.e.runtime if member['streaming_cluster'] in self.e.runtime.config.raw['streaming_clusters']
                else self.e.old_runtime)

    def endpoint(self, member):
        instance = self.member_runtime(member).config.instance(self.node(member))
        return instance['host_config']['address'], instance['port']

    def sql(self, member, statement, *, value=True):
        return self.member_runtime(member)._psql(self.node(member), statement, self.database, tuples=value).strip()

    def identity(self, member):
        node = self.node(member)
        instance = self.member_runtime(member).config.instance(node)
        actual = self.sql(member, "SELECT current_setting('data_directory') || '|' || inet_server_port() || '|' || pg_is_in_recovery()")
        if actual != instance['data_dir'] + '|' + str(instance['port']) + '|false':
            raise ValueError('成员连接身份或主库角色不符: ' + node)

    def wait(self, predicate, message):
        deadline = time.monotonic() + 60
        while not predicate():
            if time.monotonic() >= deadline:
                raise ValueError(message)
            time.sleep(.5)

    def mmr_preflight(self):
        uuids = set()
        expected = {row['node_name'] for row in self.before['members'].values()}
        for member in self.before['members'].values():
            self.identity(member)
            if self.sql(member, 'SELECT count(*) FROM fdd.mmr_group') != '1':
                raise ValueError('MMR 成员包含其他组，不能自动退出全局节点')
            uuids.add(self.sql(member, 'SELECT group_uuid::text FROM fdd.mmr_group WHERE group_name=' + _quote_literal(self.after['group_name'])))
            actual = self.sql(member, "SELECT node_name FROM fdd.mmr_node WHERE node_state='ACTIVE' ORDER BY node_name")
            if set(actual.splitlines()) != expected:
                raise ValueError('实际 MMR 成员与基线不一致')
        if len(uuids) != 1 or not next(iter(uuids)):
            raise ValueError('MMR 组标识不一致')
        self.state['group_uuid'] = next(iter(uuids))
        for key in self.delta['added']:
            member = self.after['members'][key]
            self.identity(member)
            if (self.sql(member, 'SHOW track_commit_timestamp') != 'on'
                    or self.sql(member, 'SHOW wal_level') != 'logical'
                    or 'fdd_mmr' not in [item.strip() for item in self.sql(member, 'SHOW shared_preload_libraries').split(',')]):
                raise ValueError('新增成员需预先启用 fdd_mmr、logical WAL 和提交时间戳')
            # join_group can copy/truncate tables. Require a clean destination
            # and retain the extension's strict table_exist_error precheck.
            count = self.sql(member, "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p') AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e')")
            if count != '0':
                raise ValueError('新增 MMR 成员包含业务表，不能自动覆盖')
            if self.sql(member, "SELECT count(*) FROM pg_extension WHERE extname='fdd_mmr'") == '1':
                if self.sql(member, 'SELECT count(*) FROM fdd.mmr_node') != '0':
                    raise ValueError('新增成员已登记其他 MMR 节点')
            if (member.get('join') or {}).get('precheck', 'table_exist_error') != 'table_exist_error':
                raise ValueError('新增成员必须使用 table_exist_error 检查')
        self.e.save()

    def mmr_add(self, key, control):
        member = self.after['members'][key]
        for extension in self.after.get('extensions', ['fdd_mmr']):
            if extension != 'fbase_mac':
                self.e.step(self.prefix + 'extension:' + key + ':' + extension,
                            lambda ext=extension: self.sql(member, 'CREATE EXTENSION IF NOT EXISTS ' + quote_ident(ext)))
        host, port = self.endpoint(member)
        def create():
            if self.sql(member, 'SELECT count(*) FROM fdd.mmr_node WHERE node_name=' + _quote_literal(member['node_name'])) == '0':
                options = member.get('mmr_node') or {}
                self.sql(member, 'SELECT fdd.create_node(%s,%s,%s,%s,%s)' % (
                    _quote_literal(member['node_name']), _quote_literal('host=%s port=%s dbname=%s user=postgres' % (host, port, self.database)),
                    'true' if options.get('failover_slot', True) else 'false',
                    _quote_literal(options.get('streaming', 'off')), 'true' if options.get('two_phase', False) else 'false'))
        self.e.step(self.prefix + 'create:' + key, create)
        def join():
            if self.sql(member, 'SELECT node_state FROM fdd.mmr_node WHERE node_name=' + _quote_literal(member['node_name'])) == 'ACTIVE':
                uuid = self.sql(member, 'SELECT group_uuid::text FROM fdd.mmr_group WHERE group_name=' + _quote_literal(self.after['group_name']))
                if uuid != self.state['group_uuid']:
                    raise ValueError('已加入的成员组标识不符')
                return
            host, port = self.endpoint(control)
            self.sql(member, 'SELECT fdd.join_group(%s,%s,true,%s,%s)' % (
                _quote_literal(self.after['group_name']), _quote_literal('host=%s port=%s dbname=%s user=postgres' % (host, port, self.database)),
                _quote_literal((member.get('join') or {}).get('synchronize_structure', 'all')), _quote_literal('table_exist_error')))
        self.e.step(self.prefix + 'join:' + key, join)
        if 'fbase_mac' in self.after.get('extensions', []):
            self.e.step(self.prefix + 'security:' + key, lambda: self.sql(member, 'CREATE EXTENSION IF NOT EXISTS fbase_mac'))

    def mmr_remove(self, key, control):
        member = self.before['members'][key]
        def part():
            state = self.sql(control, 'SELECT node_state FROM fdd.mmr_node WHERE node_name=' + _quote_literal(member['node_name']))
            if state and state != 'PARTED':
                self.sql(control, 'SELECT fdd.part_node(%s,true,false)' % _quote_literal(member['node_name']))
        self.e.step(self.prefix + 'part:' + key, part)
        def drop():
            if self.sql(control, 'SELECT count(*) FROM fdd.mmr_node WHERE node_name=' + _quote_literal(member['node_name'])) != '0':
                self.sql(control, 'SELECT fdd.drop_node(%s,false)' % _quote_literal(member['node_name']))
        self.e.step(self.prefix + 'drop:' + key, drop)

    def mmr_verify(self):
        expected = {row['node_name'] for row in self.after['members'].values()}
        for member in self.after['members'].values():
            def healthy(m=member):
                actual = self.sql(m, "SELECT node_name FROM fdd.mmr_node WHERE node_state='ACTIVE' ORDER BY node_name")
                udf = self.sql(m, "SELECT count(*) FROM fdd.show_node_info(true,false) WHERE nodestate<>'ACTIVE' OR real_nodestate<>'ACTIVE' OR is_abnormal<>'OK'")
                return set(actual.splitlines()) == expected and udf == '0'
            self.wait(healthy, 'MMR 成员未收敛到审阅配置')

    def citus_preflight(self):
        coordinator = self.after['coordinator']
        self.identity(coordinator)
        version = self.sql(coordinator, "SELECT extversion FROM pg_extension WHERE extname='citus'")
        if not version:
            raise ValueError('Coordinator 未启用 Citus')
        expected = {'%s|%s' % self.endpoint(row) for row in self.before['workers'].values()}
        actual = set(self.sql(coordinator, "SELECT nodename || '|' || nodeport FROM pg_dist_node WHERE groupid>0 AND noderole='primary'").splitlines())
        if expected != actual:
            raise ValueError('实际 Citus Worker 与审阅基线不一致')
        for member in self.after['workers'].values():
            self.identity(member)
            if self.sql(member, "SELECT extversion FROM pg_extension WHERE extname='citus'") != version:
                raise ValueError('Worker 的 Citus 版本与 Coordinator 不一致')

    def citus_run(self):
        coordinator = self.after['coordinator']
        self.e.step(self.prefix + 'preflight', self.citus_preflight)
        for key in self.delta['added']:
            host, port = self.endpoint(self.after['workers'][key])
            def add(h=host, p=port):
                if self.sql(coordinator, 'SELECT count(*) FROM pg_dist_node WHERE nodename=%s AND nodeport=%s' % (_quote_literal(h), p)) == '0':
                    self.sql(coordinator, 'SELECT citus_add_node(%s,%s)' % (_quote_literal(h), p))
            self.e.step(self.prefix + 'add:' + key, add)
        for key in self.delta['removed']:
            host, port = self.endpoint(self.before['workers'][key])
            def drain(h=host, p=port):
                if self.sql(coordinator, 'SELECT count(*) FROM pg_dist_node WHERE nodename=%s AND nodeport=%s' % (_quote_literal(h), p)) != '0':
                    self.sql(coordinator, 'SELECT citus_drain_node(%s,%s)' % (_quote_literal(h), p))
            self.e.step(self.prefix + 'drain:' + key, drain)
            def remove(h=host, p=port):
                if self.sql(coordinator, 'SELECT count(*) FROM pg_dist_node WHERE nodename=%s AND nodeport=%s' % (_quote_literal(h), p)) != '0':
                    self.sql(coordinator, 'SELECT citus_remove_node(%s,%s)' % (_quote_literal(h), p))
            self.e.step(self.prefix + 'remove:' + key, remove)
        self.e.step(self.prefix + 'verify', lambda: self.e.runtime.verify_target('citus.' + self.name))

    def run(self):
        if self.section == 'citus_clusters':
            self.citus_run()
        else:
            control_key = sorted(self.before['members'].keys() & self.after['members'].keys())[0]
            control = self.after['members'][control_key]
            self.e.step(self.prefix + 'preflight', self.mmr_preflight)
            for key in self.delta['added']:
                self.mmr_add(key, control)
            for key in self.delta['removed']:
                self.mmr_remove(key, control)
            self.e.step(self.prefix + 'verify', self.mmr_verify)
        self.e.emit('复制成员变更完成，数据库与业务数据保留: ' + self.name)
