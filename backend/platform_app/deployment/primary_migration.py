"""Planned primary relocation with a shutdown WAL fence and retained source."""

import hashlib
import json
import re
import time
from pathlib import Path

from .change_execution import _quote_literal


def primary_cluster(current, desired, node):
    """Relocate an unchanged physical group and its declared endpoint catalogs."""
    from .endpoint_metadata import endpoint_contexts, loopback_host
    for name, cluster in current.get("streaming_clusters", {}).items():
        if cluster.get("primary") != node:
            continue
        if desired.get("streaming_clusters", {}).get(name) != cluster:
            return None
        old, new = current["instances"][node], desired["instances"][node]
        a = current["hosts"][old["host"]]["address"]
        b = desired["hosts"][new["host"]]["address"]
        same_dir = a == b and old["data_dir"] == new["data_dir"]
        same_home = (current["postgresql_installations"][old["installation"]]["home"]
                     == desired["postgresql_installations"][new["installation"]]["home"])
        contexts = endpoint_contexts(current, name)
        if contexts is None:
            return None
        for context in contexts:
            section = {'mmr': 'mmr_clusters', 'citus': 'citus_clusters', 'logical': 'logical_replications'}[context['kind']]
            if desired.get(section, {}).get(context['name']) != context['cluster']:
                return None
            if a != b:
                peer_streams = []
                if context['kind'] == 'mmr':
                    peer_streams = [row['streaming_cluster'] for row in context['cluster']['members'].values()]
                elif context['kind'] == 'logical':
                    peer_streams = [context['cluster'][role]['streaming_cluster'] for role in ('pub', 'sub')]
                for stream in peer_streams:
                    peer = desired['instances'][desired['streaming_clusters'][stream]['primary']]
                    if loopback_host(desired['hosts'][peer['host']]['address']):
                        return None  # published endpoints must be globally reachable
            if not same_dir:
                if context['kind'] == 'mmr' and not context['member'].get('mmr_node', {}).get('failover_slot', True):
                    return None
                if (context['kind'] == 'logical' and context['role'] == 'pub'
                        and not (context['cluster']['sub'].get('slot') or {}).get('failover')):
                    return None
        # A fresh copy must run alongside its source before the shutdown fence.
        if same_home and (same_dir or (a, old["port"]) != (b, new["port"])):
            return name
    return None


class PrimaryMigration:
    def __init__(self, executor, node, cluster):
        self.e, self.node, self.cluster = executor, node, cluster
        self.old, self.new = executor.old_runtime, executor.runtime
        self.source = self.old.config.instance(node)
        self.destination = self.new.config.instance(node)
        self.state = executor.state.setdefault("primary_migrations", {}).setdefault(node, {})

    def sql(self, runtime, node, statement):
        return self.e.sql(runtime, node, statement).strip()

    def control_sql(self, statement):
        # The cloned primary inherits synchronous rules. Reconnect its
        # standbys before requiring synchronous acknowledgements of changes.
        result = self.sql(self.new, self.node, "SET synchronous_commit=off; " + statement)
        return result.removeprefix("SET\n") if result != "SET" else ""

    def identity(self, runtime, node, recovery):
        instance = runtime.config.instance(node)
        raw = self.sql(runtime, node,
                       "SELECT row_to_json(t) FROM (SELECT current_setting('data_directory') AS directory,"
                       " inet_server_port() AS port, pg_is_in_recovery() AS recovery,"
                       " (SELECT system_identifier::text FROM pg_control_system()) AS system_id) t")
        row = json.loads(raw)
        if (row["directory"] != instance["data_dir"] or row["port"] != instance["port"]
                or row["recovery"] is not recovery):
            raise ValueError("主库迁移连接身份或角色不一致: " + node)
        return row["system_id"]

    def preflight(self):
        self.state["system_id"] = self.identity(self.old, self.node, False)
        for row in self.old.config.raw["streaming_clusters"][self.cluster].get("standbys", []):
            if self.identity(self.old, row["instance"], True) != self.state["system_id"]:
                raise ValueError("备库系统标识与迁移源不一致")
        same_dir = (self.source['host_config']['address'], self.source['data_dir']) == (
            self.destination['host_config']['address'], self.destination['data_dir'])
        if not same_dir:
            slots = self.logical_slots(self.old)
            if any(not row.get('failover') or row.get('temporary') or row.get('wal_status') == 'lost' for row in slots):
                raise ValueError('源端含不能延续的逻辑复制槽，禁止搬迁数据目录；可先执行保留原目录的端口变更')
            self.state['logical_slots'] = slots
        from .endpoint_metadata import EndpointMetadata

        EndpointMetadata(self).preflight()
        subscriptions = json.loads(self.sql(self.old, self.node,
            "SELECT coalesce(json_agg(json_build_object('name',s.subname,'database',d.datname))::text,'[]') FROM pg_subscription s JOIN pg_database d ON d.oid=s.subdbid"))
        if subscriptions and not same_dir:
            allowed = set()
            metadata = EndpointMetadata(self)
            for context in metadata.contexts:
                if context['kind'] == 'logical' and context['role'] == 'sub':
                    allowed.add((context['database'], context['name']+'_sub'))
                if context['kind'] == 'mmr':
                    names = metadata.sql(self.old, self.node, context['database'], 'SELECT sub_name FROM fdd.mmr_subscription')
                    allowed.update((context['database'], name) for name in names.splitlines())
            if any((row['database'], row['name']) not in allowed for row in subscriptions):
                raise ValueError('源端含未纳入方案的逻辑订阅，不能自动启动搬迁后的接收进程')
            self.state['receiver_worker_limit'] = int(self.sql(self.old, self.node, 'SHOW max_logical_replication_workers'))
        self.e.save()

    def logical_slots(self, runtime):
        return json.loads(self.sql(runtime, self.node,
                          "SELECT coalesce(json_agg(to_jsonb(s))::text,'[]') FROM pg_replication_slots s WHERE slot_type='logical'"))

    def check_logical_slots(self):
        expected = self.state.get('logical_slots', [])
        if not expected:
            return
        # A slot marked failover is not sufficient: verify that the physical
        # copy actually has the slot and its restart WAL before fencing.
        actual = {row['slot_name']: row for row in self.logical_slots(self.new)}
        files = set(self.sql(self.new, self.node, 'SELECT name FROM pg_ls_waldir()').splitlines())
        segment_size = int(self.sql(self.new, self.node, "SELECT pg_size_bytes(current_setting('wal_segment_size'))"))
        for row in expected:
            slot = actual.get(row['slot_name'])
            if (not slot or not slot.get('failover') or slot.get('active') or slot.get('wal_status') == 'lost'
                    or any(slot.get(key) != row.get(key) for key in ('plugin', 'database', 'slot_type'))
                    or not slot.get('restart_lsn') or not slot.get('confirmed_flush_lsn')):
                raise ValueError('迁移副本的逻辑槽未准备完成，原主库继续运行: '+row['slot_name'])
            high, low = [int(part, 16) for part in slot['restart_lsn'].split('/')]
            suffix = '%08X%08X' % (high, low//segment_size)
            if not any(len(name) == 24 and name.endswith(suffix) for name in files):
                raise ValueError('迁移副本缺少逻辑槽所需 WAL，禁止停止原主库: '+row['slot_name'])

    def wait_replay(self, lsn):
        if not re.fullmatch(r"[0-9A-F]+/[0-9A-F]+", lsn, re.I):
            raise ValueError("迁移 WAL 栅栏无效")
        deadline = time.monotonic() + 60
        while self.sql(self.new, self.node,
                       "SELECT pg_is_in_recovery() AND coalesce(pg_last_wal_replay_lsn()>="
                       + _quote_literal(lsn) + "::pg_lsn,false)") != "t":
            if time.monotonic() >= deadline:
                raise ValueError("新副本未回放到迁移栅栏，禁止提升；继续原计划恢复")
            time.sleep(.5)

    def clone(self):
        host = self.destination["host_config"]["address"]
        marker = self.new._marker(self.node)
        if self.new.executor.exists(host, marker):
            receipt = json.loads(self.new.executor.read_text(host, marker))
            if receipt.get("migration_plan") != self.e.plan["id"]:
                raise ValueError("目标目录不属于当前迁移计划")
            return
        self.new.executor.run(["mkdir", "-p", str(Path(self.destination["data_dir"]).parent)], host=host)
        self.new.executor.run([
            self.destination["installation_config"]["home"] + "/bin/pg_basebackup",
            "-h", self.source["host_config"]["address"], "-p", str(self.source["port"]),
            "-U", "postgres", "-D", self.destination["data_dir"], "-R",
            "--checkpoint=fast", "-X", "stream", "-S", self.state["slot"],
        ], host=host)
        self.new.executor.write_text(host, marker, json.dumps({
            "node": self.node, "migration_plan": self.e.plan["id"],
            "system_identifier": self.state["system_id"],
        }))

    def configure_clone(self):
        receiver = {'max_logical_replication_workers': 0} if 'receiver_worker_limit' in self.state else {}
        self.new._seed_auto_conf(self.destination["host_config"]["address"],
                                 self.destination["data_dir"], {
                                     **receiver,
                                     "port": self.destination["port"],
                                     "data_directory": self.destination["data_dir"],
                                     "listen_addresses": self.destination["host_config"]["address"],
                                     "primary_conninfo": "host=%s port=%s user=postgres dbname=postgres application_name=platform_migration" % (
                                         self.source["host_config"]["address"], self.source["port"]),
                                     "primary_slot_name": self.state["slot"],
                                 })

    def restore_receivers(self):
        if 'receiver_worker_limit' not in self.state:
            return
        prefix = 'primary-move:'+self.node+':'
        self.e.step(prefix+'receiver-limit', lambda: self.sql(self.new, self.node,
            'ALTER SYSTEM SET max_logical_replication_workers = '+str(self.state['receiver_worker_limit'])))
        self.e.step(prefix+'receiver-restart-stop', lambda: self.e.stop(self.new, self.node))
        self.e.step(prefix+'receiver-restart-start', lambda: self.new.start_instance(self.node)
                    if not self.new.status_instance(self.node)['running'] else None)

    def check_receiver_quarantine(self):
        if 'receiver_worker_limit' in self.state:
            if (self.sql(self.new, self.node, 'SHOW max_logical_replication_workers') != '0'
                    or self.sql(self.new, self.node, 'SELECT count(*) FROM pg_stat_subscription WHERE pid IS NOT NULL') != '0'):
                raise ValueError('目标订阅接收进程未暂停，不能切换未更新的连接')

    def fence(self):
        if self.old.status_instance(self.node)["running"]:
            # Refresh source slot facts immediately before stopping it. A slot
            # added/retired outside the reviewed workflow cannot be omitted.
            before = {row['slot_name']: row for row in self.state.get('logical_slots', [])}
            now = {row['slot_name']: row for row in self.logical_slots(self.old)}
            if (before.keys() != now.keys() or any(
                not row.get('failover') or row.get('temporary') or row.get('wal_status') == 'lost'
                or any(row.get(key) != before[name].get(key) for key in ('plugin', 'database', 'datoid'))
                for name, row in now.items()
            )):
                raise ValueError('源端逻辑槽发生未审阅变化，原主库继续运行')
            self.e.stop(self.old, self.node)
        # Unlike a pre-stop pg_current_wal_lsn(), a clean shutdown checkpoint
        # includes writes that committed while pg_ctl was stopping the source.
        result = self.old.executor.run([
            "env", "LC_ALL=C", self.source["installation_config"]["home"] + "/bin/pg_controldata",
            self.source["data_dir"],
        ], host=self.source["host_config"]["address"]).stdout
        def field(label):
            match = re.search(r"^" + re.escape(label) + r":\s*(.+)$", result, re.M)
            if not match:
                raise ValueError("无法确认旧主库关闭栅栏: " + label)
            return match[1].strip()
        if field("Database cluster state") != "shut down":
            raise ValueError("旧主库未正常关闭，禁止提升副本")
        if field("Database system identifier") != self.state["system_id"]:
            raise ValueError("旧主库系统标识发生变化")
        self.state["shutdown_lsn"] = field("Latest checkpoint location")
        self.e.save()

    def promote(self):
        if self.old.status_instance(self.node)["running"]:
            raise ValueError("旧主库重新启动，禁止提升新主库")
        if self.sql(self.new, self.node, "SELECT pg_is_in_recovery()") == "f":
            if self.identity(self.new, self.node, False) != self.state["system_id"]:
                raise ValueError("已提升实例系统标识不符")
            return  # interrupted after promotion; the durable fence preceded it
        if self.identity(self.new, self.node, True) != self.state["system_id"]:
            raise ValueError("迁移副本系统标识不符")
        self.wait_replay(self.state["shutdown_lsn"])
        self.check_logical_slots()
        self.new._promote(self.node)

    def prepare_copy(self):
        if self.identity(self.new, self.node, True) != self.state['system_id']:
            raise ValueError('迁移副本系统标识不符，原主库继续运行')
        # Retain WAL before fencing, rather than creating unreserved slots
        # only after clients can begin writing to the promoted node.
        for row in self.new.config.raw['streaming_clusters'][self.cluster].get('standbys', []):
            node = row['instance']
            slot = row.get('slot') or self.cluster + '_' + node + '_slot'
            kind = self.sql(self.new, self.node, 'SELECT slot_type FROM pg_replication_slots WHERE slot_name=' + _quote_literal(slot))
            if kind and kind != 'physical':
                raise ValueError('迁移副本复制槽被逻辑槽占用')
            if not kind:
                self.sql(self.new, self.node, 'SELECT pg_create_physical_replication_slot(' + _quote_literal(slot) + ',true)')

    def reconnect(self, row):
        node = row["instance"]
        if self.identity(self.new, node, True) != self.state["system_id"]:
            raise ValueError("待重连备库身份不符")
        slot = row.get("slot") or self.cluster + "_" + node + "_slot"
        kind = self.control_sql("SELECT slot_type FROM pg_replication_slots WHERE slot_name=" + _quote_literal(slot))
        if kind and kind != "physical":
            raise ValueError("新主库复制槽被逻辑槽占用")
        if not kind:
            self.control_sql("SELECT pg_create_physical_replication_slot(" + _quote_literal(slot) + ")")
        conn = "host=%s port=%s user=postgres application_name=%s" % (
            self.destination["host_config"]["address"], self.destination["port"], row.get("application_name", node))
        self.sql(self.new, node, "ALTER SYSTEM SET primary_conninfo = " + _quote_literal(conn))
        self.sql(self.new, node, "ALTER SYSTEM SET primary_slot_name = " + _quote_literal(slot))
        self.sql(self.new, node, "SELECT pg_reload_conf()")

    def run(self):
        prefix = "primary-move:" + self.node + ":"
        if (prefix + "shutdown-fence" in self.e.state['completed']
                and self.old.status_instance(self.node)["running"]):
            raise ValueError("旧主库在关闭栅栏后重新启动，停止恢复以避免双主")
        self.e.step(prefix + "preflight", self.preflight)
        same_dir = (self.source["host_config"]["address"], self.source["data_dir"]) == (
            self.destination["host_config"]["address"], self.destination["data_dir"])
        if same_dir:
            self.e.step(prefix + "port", lambda: self.sql(self.old, self.node,
                        "ALTER SYSTEM SET port = " + _quote_literal(self.destination["port"])))
            self.e.step(prefix + "stop", lambda: self.e.stop(self.old, self.node))
            self.e.step(prefix + "start", lambda: self.new.start_instance(self.node)
                        if not self.new.status_instance(self.node)["running"] else None)
        else:
            self.state.setdefault("slot", "platform_move_" + hashlib.sha256(
                self.e.plan["id"].encode()).hexdigest()[:20])
            self.e.save()
            def ensure_slot():
                kind = self.sql(self.old, self.node, "SELECT slot_type FROM pg_replication_slots WHERE slot_name=" + _quote_literal(self.state["slot"]))
                if kind and kind != "physical":
                    raise ValueError("迁移槽名被逻辑槽占用")
                if not kind:
                    self.sql(self.old, self.node, "SELECT pg_create_physical_replication_slot(" + _quote_literal(self.state["slot"]) + ")")
            self.e.step(prefix + "slot", ensure_slot)
            self.e.step(prefix + "clone", self.clone)
            self.e.step(prefix + "configure", self.configure_clone)
            self.e.step(prefix + "start-copy", lambda: self.new.start_instance(self.node)
                        if not self.new.status_instance(self.node)["running"] else None)
            self.e.step(prefix + 'copy-identity-and-slots', self.prepare_copy)
            self.e.step(prefix + "catch-up", lambda: self.wait_replay(self.sql(self.old, self.node, "SELECT pg_current_wal_lsn()")))
            self.e.step(prefix + 'logical-slot-readiness', self.check_logical_slots)
            self.e.step(prefix + "shutdown-fence", self.fence)
            self.e.step(prefix + "promote", self.promote)
            self.e.step(prefix + 'receiver-quarantine', self.check_receiver_quarantine)
        for row in self.new.config.raw["streaming_clusters"][self.cluster].get("standbys", []):
            self.e.step(prefix + "reconnect:" + row["instance"], lambda r=row: self.reconnect(r))
        from .endpoint_metadata import EndpointMetadata

        EndpointMetadata(self).update()
        self.restore_receivers()
        EndpointMetadata(self).verify()
        self.e.emit("主库迁移完成，原数据目录保留: " + self.source["data_dir"])
