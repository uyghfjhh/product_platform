"""Update replication endpoint catalogs after a fenced physical relocation."""

import hashlib
import ipaddress
import json
import time

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from ..resources import canonical_host
from .change_execution import _quote_literal


def endpoint_contexts(config, stream):
    rows = []
    for name, cluster in config.get('mmr_clusters', {}).items():
        for member in cluster.get('members', {}).values():
            if member.get('streaming_cluster') == stream:
                if 'node_name' not in member or 'group_name' not in cluster:
                    return None
                rows.append({'kind': 'mmr', 'name': name, 'database': cluster.get('database', 'postgres'),
                             'member': member, 'cluster': cluster})
    for name, cluster in config.get('citus_clusters', {}).items():
        if cluster.get('coordinator', {}).get('streaming_cluster') == stream:
            if not cluster.get('workers'):
                return None
            rows.append({'kind': 'citus', 'name': name, 'role': 'coordinator',
                         'database': cluster.get('database', 'postgres'), 'cluster': cluster})
        for member in cluster.get('workers', {}).values():
            if member.get('streaming_cluster') == stream:
                if not cluster.get('coordinator'):
                    return None
                rows.append({'kind': 'citus', 'name': name, 'role': 'worker',
                             'database': cluster.get('database', 'postgres'), 'cluster': cluster})
    for name, link in config.get('logical_replications', {}).items():
        for role in ('pub', 'sub'):
            if link.get(role, {}).get('streaming_cluster') == stream:
                if not link.get('pub') or not link.get('sub'):
                    return None
                rows.append({'kind': 'logical', 'name': name, 'role': role, 'cluster': link,
                             'database': link[role].get('database', link['pub'].get('database', 'postgres'))})
    return rows


def loopback_host(host):
    if host.lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class EndpointMetadata:
    def __init__(self, migration):
        self.m = migration
        self.e, self.old, self.new = migration.e, migration.old, migration.new
        self.contexts = endpoint_contexts(self.e.current, migration.cluster)
        if self.contexts is None:
            raise ValueError('关联复制元数据不完整')
        self.state = migration.state.setdefault('endpoints', {})

    def primary(self, stream):
        return self.new.config.raw['streaming_clusters'][stream]['primary']

    def sql(self, runtime, node, database, statement):
        # Feed statements on stdin. Endpoint changes preserve existing
        # credentials in the server catalog; they never enter argv/checkpoints.
        instance = runtime.config.instance(node)
        args = [instance['installation_config']['home'] + '/bin/psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                '-h', instance['host_config']['address'], '-p', str(instance['port']), '-U', 'postgres',
                '-d', database, '-f', '-']
        try:
            result = runtime.executor.run(args, host=instance['host_config']['address'],
                                          stdin="SET lock_timeout='5s'; SET statement_timeout='30s';\n"+statement+';\n')
        except Exception:  # noqa: BLE001 - never include conninfo in task errors
            raise ValueError('复制端点元数据操作失败，请检查目标数据库状态') from None
        return result.stdout.strip()

    def conn(self, value):
        try:
            return conninfo_to_dict(value)
        except Exception:  # noqa: BLE001 - libpq parser errors can contain credentials
            raise ValueError('复制连接信息格式无效') from None

    def receipt(self, value):
        conn = self.conn(value)
        public = {key: conn[key] for key in ('host', 'hostaddr', 'port', 'dbname', 'user', 'application_name', 'sslmode', 'service') if key in conn}
        return hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()

    def matches(self, conn, instance, observer=None, *, global_route=False):
        host = conn.get('hostaddr') or conn.get('host', '')
        target = instance['host_config']['address']
        if global_route and loopback_host(host) and not loopback_host(target):
            return False
        if observer is not None and loopback_host(host):
            host = observer['host_config']['address']
        return canonical_host(host) == canonical_host(target) and int(conn.get('port', '5432')) == instance['port']

    def suffix(self, instance=None):
        instance = instance or self.m.destination
        host = instance['host_config']['address']
        try:
            address = str(ipaddress.ip_address(host))
        except ValueError:
            address = ''  # remove an old numeric override when switching to DNS
        return ' '+make_conninfo(host=host, hostaddr=address, port=instance['port'])

    def sub_row(self, runtime, node, database, name):
        result = self.sql(runtime, node, database, 'SELECT row_to_json(t) FROM (SELECT subslotname,subpublications,subconninfo '
                          'FROM pg_subscription WHERE subname='+_quote_literal(name)+
                          ' AND subdbid=(SELECT oid FROM pg_database WHERE datname=current_database())) t')
        return json.loads(result) if result else None

    def preflight(self):
        for context in self.contexts:
            key = context['kind']+':'+context['name']+':'+context.get('role', '')
            cluster, database = context['cluster'], context['database']
            if context['kind'] == 'mmr':
                uuids = set()
                receipts = {}
                names = {member['node_name'] for member in cluster['members'].values()}
                for member in cluster['members'].values():
                    node = self.primary(member['streaming_cluster'])
                    uuid = self.sql(self.old, node, database, 'SELECT group_uuid::text FROM fdd.mmr_group WHERE group_name='+_quote_literal(cluster['group_name']))
                    uuids.add(uuid)
                    actual = self.sql(self.old, node, database, "SELECT node_name FROM fdd.mmr_node WHERE node_state='ACTIVE' ORDER BY node_name")
                    if set(actual.splitlines()) != names:
                        raise ValueError('实际 MMR 成员与迁移基线不一致')
                    receipts[node] = {}
                    for target in cluster['members'].values():
                        dsn = self.sql(self.old, node, database, 'SELECT node_dsn FROM fdd.mmr_node WHERE node_name='+_quote_literal(target['node_name']))
                        target_instance = self.old.config.instance(self.primary(target['streaming_cluster']))
                        if (not self.matches(self.conn(dsn), target_instance, self.old.config.instance(node))
                                or self.conn(dsn).get('dbname') != database):
                            raise ValueError('MMR 成员端点与迁移基线不一致')
                        receipts[node][target['node_name']] = self.receipt(dsn)
                if len(uuids) != 1 or not next(iter(uuids)):
                    raise ValueError('MMR 组标识不一致')
                self.state[key] = {'group_uuid': next(iter(uuids)), 'dsn_receipts': receipts}
            elif context['kind'] == 'citus':
                coordinator = self.primary(cluster['coordinator']['streaming_cluster'])
                result = self.sql(self.old, coordinator, database, 'SELECT row_to_json(t) FROM (SELECT nodeid,groupid FROM pg_dist_node '
                                  'WHERE noderole=\'primary\' AND nodename='+_quote_literal(self.m.source['host_config']['address'])+
                                  ' AND nodeport='+str(self.m.source['port'])+') t')
                if not result:
                    raise ValueError('Citus 端点不在 Coordinator 基线中')
                details = json.loads(result)
                if (details['groupid'] == 0) != (context['role'] == 'coordinator'):
                    raise ValueError('Citus 节点角色不一致')
                self.state[key] = details
            else:
                subscriber = self.primary(cluster['sub']['streaming_cluster'])
                subdb = cluster['sub'].get('database', cluster['pub'].get('database', 'postgres'))
                row = self.sub_row(self.old, subscriber, subdb, context['name']+'_sub')
                publisher = self.old.config.instance(self.primary(cluster['pub']['streaming_cluster']))
                slot = (cluster['sub'].get('slot') or {}).get('name') or context['name']+'_slot'
                if (not row or not self.matches(self.conn(row['subconninfo']), publisher, self.old.config.instance(subscriber))
                        or self.conn(row['subconninfo']).get('dbname') != cluster['pub'].get('database', 'postgres')
                        or row['subslotname'] != slot or row['subpublications'] != [context['name']+'_pub']):
                    raise ValueError('逻辑订阅与迁移基线不一致')
                self.state[key] = {'slot': slot}
        self.e.save()

    def update_subscription(self, context):
        link = context['cluster']
        node = self.primary(link['sub']['streaming_cluster'])
        database = link['sub'].get('database', link['pub'].get('database', 'postgres'))
        name = context['name']+'_sub'
        row = self.sub_row(self.new, node, database, name)
        if not row:
            raise ValueError('待更新订阅不存在')
        conn = self.conn(row['subconninfo'])
        slot = (link['sub'].get('slot') or {}).get('name') or context['name']+'_slot'
        if (row['subslotname'] != slot or row['subpublications'] != [context['name']+'_pub']
                or conn.get('dbname') != link['pub'].get('database', 'postgres')):
            raise ValueError('订阅身份发生未审阅变化')
        publisher = self.new.config.instance(self.primary(link['pub']['streaming_cluster']))
        original = self.old.config.instance(self.primary(link['pub']['streaming_cluster']))
        if self.matches(conn, publisher, self.new.config.instance(node), global_route=True):
            return
        if not self.matches(conn, original, self.old.config.instance(node)):
            raise ValueError('订阅端点发生未审阅变化')
        suffix = self.suffix(publisher)
        # Read secrets and assemble ALTER SUBSCRIPTION inside the server only.
        self.sql(self.new, node, database, 'DO $platform_endpoint$ DECLARE connection text; BEGIN '
                 'SELECT subconninfo INTO STRICT connection FROM pg_subscription WHERE subname='+_quote_literal(name)+
                 ' AND subdbid=(SELECT oid FROM pg_database WHERE datname=current_database()); '
                 "EXECUTE format('ALTER SUBSCRIPTION %I CONNECTION %L', "+_quote_literal(name)+', connection || '+_quote_literal(suffix)+
                 '); END $platform_endpoint$')

    def update_mmr(self, context):
        cluster = context['cluster']
        members = sorted(cluster['members'].values(), key=lambda row: row['streaming_cluster'] != self.m.cluster)
        # Normalize peers too: an inherited localhost DSN is no longer a
        # route to that peer after the receiver moves to another host.
        for target in members:
            for observer in members:
                self.update_mmr_member(context, target, observer)

    def update_mmr_member(self, context, target, observer):
        cluster, database = context['cluster'], context['database']
        member_name = target['node_name']
        destination = self.new.config.instance(self.primary(target['streaming_cluster']))
        original = self.old.config.instance(self.primary(target['streaming_cluster']))
        suffix = self.suffix(destination)
        node = self.primary(observer['streaming_cluster'])
        def update():
            n = node
            uuid = self.sql(self.new, n, database, 'SELECT group_uuid::text FROM fdd.mmr_group WHERE group_name='+_quote_literal(cluster['group_name']))
            if uuid != self.state['mmr:'+context['name']+':']['group_uuid']:
                raise ValueError('MMR 组标识发生未审阅变化')
            dsn = self.sql(self.new, n, database, 'SELECT node_dsn FROM fdd.mmr_node WHERE node_name='+_quote_literal(member_name))
            conn = self.conn(dsn)
            if self.matches(conn, destination, self.new.config.instance(n), global_route=True):
                return
            receipt = self.state['mmr:'+context['name']+':']['dsn_receipts'][n][member_name]
            if not self.matches(conn, original, self.old.config.instance(n)) or self.receipt(dsn) != receipt:
                raise ValueError('MMR 端点发生未审阅变化')
            self.sql(self.new, n, database, 'DO $platform_endpoint$ DECLARE connection text; BEGIN '
                     'SELECT node_dsn INTO STRICT connection FROM fdd.mmr_node WHERE node_name='+_quote_literal(member_name)+'; '
                     "EXECUTE format('SELECT fdd.alter_node_interface(%L,%L,false)', "+_quote_literal(member_name)+
                     ', connection || '+_quote_literal(suffix)+'); END $platform_endpoint$')
        self.e.step('endpoint:mmr:'+context['name']+':'+member_name+':'+node, update)

    def update_citus(self, context):
        coordinator = self.primary(context['cluster']['coordinator']['streaming_cluster'])
        key = 'citus:'+context['name']+':'+context['role']
        nodeid = self.state[key]['nodeid']
        actual = self.sql(self.new, coordinator, context['database'], 'SELECT row_to_json(t) FROM (SELECT nodename,nodeport,groupid,noderole FROM pg_dist_node WHERE nodeid='+str(nodeid)+') t')
        row = json.loads(actual) if actual else None
        if (not row or row['groupid'] != self.state[key]['groupid'] or row['noderole'] != 'primary'
                or not any(self.matches({'host': row['nodename'], 'port': row['nodeport']}, instance)
                           for instance in (self.m.source, self.m.destination))):
            raise ValueError('Citus 节点身份发生未审阅变化')
        self.sql(self.new, coordinator, context['database'], 'SELECT citus_update_node(%s,%s,%s,false)' % (
            nodeid, _quote_literal(self.m.destination['host_config']['address']), self.m.destination['port']))

    def update(self):
        for context in self.contexts:
            key = 'endpoint:'+context['kind']+':'+context['name']+':'+context.get('role', '')
            handler = {'mmr': self.update_mmr, 'citus': self.update_citus, 'logical': self.update_subscription}[context['kind']]
            self.e.step(key, lambda c=context, function=handler: function(c))

    def verify(self):
        deadline = time.monotonic()+60
        for context in self.contexts:
            cluster, database = context['cluster'], context['database']
            while True:
                if context['kind'] == 'mmr':
                    ok = True
                    for member in cluster['members'].values():
                        node = self.primary(member['streaming_cluster'])
                        bad = self.sql(self.new, node, database, "SELECT count(*) FROM fdd.show_node_info(true,false) WHERE nodestate<>'ACTIVE' OR real_nodestate<>'ACTIVE' OR is_abnormal<>'OK'")
                        uuid = self.sql(self.new, node, database, 'SELECT group_uuid::text FROM fdd.mmr_group WHERE group_name='+_quote_literal(cluster['group_name']))
                        actual = self.sql(self.new, node, database, "SELECT node_name FROM fdd.mmr_node WHERE node_state='ACTIVE' ORDER BY node_name")
                        routes = True
                        for target in cluster['members'].values():
                            dsn = self.sql(self.new, node, database, 'SELECT node_dsn FROM fdd.mmr_node WHERE node_name='+_quote_literal(target['node_name']))
                            routes = routes and self.matches(self.conn(dsn), self.new.config.instance(self.primary(target['streaming_cluster'])), self.new.config.instance(node), global_route=True)
                        ok = (ok and routes and bad == '0'
                              and uuid == self.state['mmr:'+context['name']+':']['group_uuid']
                              and set(actual.splitlines()) == {row['node_name'] for row in cluster['members'].values()})
                elif context['kind'] == 'citus':
                    coordinator = self.primary(cluster['coordinator']['streaming_cluster'])
                    nodeid = self.state['citus:'+context['name']+':'+context['role']]['nodeid']
                    ok = self.sql(self.new, coordinator, database, 'SELECT count(*) FROM pg_dist_node WHERE nodeid=%s AND nodename=%s AND nodeport=%s' % (
                        nodeid, _quote_literal(self.m.destination['host_config']['address']), self.m.destination['port'])) == '1'
                else:
                    subscriber = self.primary(cluster['sub']['streaming_cluster'])
                    subdb = cluster['sub'].get('database', cluster['pub'].get('database', 'postgres'))
                    row = self.sub_row(self.new, subscriber, subdb, context['name']+'_sub')
                    publisher = self.new.config.instance(self.primary(cluster['pub']['streaming_cluster']))
                    slot = (cluster['sub'].get('slot') or {}).get('name') or context['name']+'_slot'
                    identity = (row and row['subslotname'] == slot and row['subpublications'] == [context['name']+'_pub']
                                and self.conn(row['subconninfo']).get('dbname') == cluster['pub'].get('database', 'postgres')
                                and self.matches(self.conn(row['subconninfo']), publisher, self.new.config.instance(subscriber), global_route=True))
                    pubdb = cluster['pub'].get('database', 'postgres')
                    active = self.sql(self.new, self.primary(cluster['pub']['streaming_cluster']), pubdb,
                                      'SELECT active FROM pg_replication_slots WHERE slot_name='+_quote_literal(slot))
                    ok = (identity and active == 't' and self.sql(self.new, subscriber, subdb,
                          'SELECT count(*) FROM pg_stat_subscription WHERE subname='+_quote_literal(context['name']+'_sub')+
                          ' AND pid IS NOT NULL AND relid IS NULL AND received_lsn IS NOT NULL') == '1')
                if ok:
                    break
                if time.monotonic() >= deadline:
                    raise ValueError('迁移后的复制端点尚未恢复健康')
                time.sleep(.5)
