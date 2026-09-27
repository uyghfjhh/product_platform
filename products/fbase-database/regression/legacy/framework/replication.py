import json
import time
from pathlib import Path

from framework.errors import OperationError
from framework.state import write_marker


def _sql_literal(value):
    return "'%s'" % str(value).replace("'", "''")


class ReplicationSetupMixin(object):
    """Create physical, logical, and MMR relationships for a managed cluster."""

    def _setup_physical_replication(self, env_id):
        for primary_name, standby_name in self.physical_relations():
            primary = self.nodes[primary_name]
            standby = self.nodes[standby_name]
            data_dir = Path(standby["data_dir"])
            print("[setup] basebackup %s <- %s" % (standby_name, primary_name), flush=True)
            data_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.runner.run([
                    self.binary("pg_basebackup"), "-h", primary["host"], "-p", primary["port"],
                    "-U", "postgres", "-D", data_dir, "-R", "-X", "stream", "-c", "fast",
                ])
            except Exception:
                if data_dir.exists():
                    write_marker(data_dir, self.cluster_name, standby_name, env_id)
                raise
            write_marker(data_dir, self.cluster_name, standby_name, env_id)
            self._install_node_files(standby_name)
            print("[setup] start %s" % standby_name, flush=True)
            self._pg_ctl(standby_name, "start")
            self._wait_ready(standby_name)

    def _setup_mmr(self):
        mmr_group = self.groups.get("mmr")
        if not mmr_group:
            return
        plugin = self.plugins["fdd_mmr"]
        databases = plugin.get("enabled_databases") or ["postgres"]
        node_options = plugin.get("node_options") or {}
        join_options = plugin.get("join_options") or {}
        members = list((mmr_group.get("members") or {}).items())
        group_name = mmr_group["group_name"]
        for database in databases:
            print("[setup] create MMR group %s database=%s" % (group_name, database),
                  flush=True)
            for member_name, relation in members:
                primary_name = relation["primary"]
                node = self.nodes[primary_name]
                member_options = dict(node_options)
                member_options.update(relation.get("node_options") or {})
                dsn = "host=%s port=%s user=postgres dbname=%s" % (
                    node["host"], node["port"], database)
                sql = "SELECT fdd.create_node(%s, %s, %s, %s, %s)" % (
                    _sql_literal(member_name), _sql_literal(dsn),
                    "true" if member_options.get("failover", True) else "false",
                    _sql_literal(member_options.get("streaming", "off")),
                    "true" if member_options.get("two_phase", False) else "false")
                self._psql(primary_name, database, sql)

            unused_seed_name, seed_relation = members[0]
            seed_primary = seed_relation["primary"]
            seed_node = self.nodes[seed_primary]
            seed_dsn = "host=%s port=%s user=postgres dbname=%s" % (
                seed_node["host"], seed_node["port"], database)
            self._psql(
                seed_primary, database,
                "CREATE TABLE fbase_regress_mmr_health ("
                "member text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT clock_timestamp())")
            self._psql(seed_primary, database,
                       "SELECT fdd.create_group(%s)" % _sql_literal(group_name))
            for unused_member_name, relation in members[1:]:
                sql = "SELECT fdd.join_group(%s, %s, %s, %s, %s)" % (
                    _sql_literal(group_name), _sql_literal(seed_dsn),
                    "true" if join_options.get("wait_for_completion", True) else "false",
                    _sql_literal(join_options.get("synchronize_structure", "all")),
                    _sql_literal(join_options.get("precheck", "table_exist_error")))
                self._psql(relation["primary"], database, sql)
            self._validate_mmr(database, members, group_name)

    def _validate_mmr(self, database, members, group_name):
        expected = len(members)
        print("[setup] verify MMR plugin health with fdd.show_node_info", flush=True)
        for member_name, relation in members:
            primary = relation["primary"]
            group_count = self._query_value(
                primary, database,
                "SELECT count(*) FROM fdd.mmr_group WHERE group_name = %s" %
                _sql_literal(group_name))
            udf_rows = self._mmr_udf_check(primary, database)
            abnormal = [row for row in udf_rows if row.get("is_abnormal") != "OK"]
            inactive = [row for row in udf_rows
                        if row.get("nodestate") != "ACTIVE" or
                        row.get("real_nodestate") != "ACTIVE"]
            if len(udf_rows) != expected or abnormal or inactive or int(group_count) != 1:
                raise OperationError(
                    "MMR UDF 检查异常: member=%s database=%s nodes=%s/%s "
                    "abnormal=%s inactive=%s group=%s" %
                    (member_name, database, len(udf_rows), expected,
                     abnormal, inactive, group_count))
        print("[setup] MMR plugin health verified", flush=True)
        expected_members = sorted(member_name for member_name, unused in members)
        print("[setup] verify MMR bidirectional replication: %s -> all members" %
              ", ".join(expected_members), flush=True)
        for member_name, relation in members:
            print("[setup] write MMR probe on %s" % member_name, flush=True)
            self._psql(
                relation["primary"], database,
                "INSERT INTO fbase_regress_mmr_health(member) VALUES (%s) "
                "ON CONFLICT (member) DO NOTHING" % _sql_literal(member_name))
        deadline = time.time() + 30
        pending = []
        expected_text = ",".join(expected_members)
        member_filter = ", ".join(_sql_literal(name) for name in expected_members)
        while time.time() < deadline:
            pending = []
            for member_name, relation in members:
                actual = self._query_value(
                    relation["primary"], database,
                    "SELECT coalesce(string_agg(member, ',' ORDER BY member), '') "
                    "FROM fbase_regress_mmr_health WHERE member IN (%s)" % member_filter)
                if actual != expected_text:
                    pending.append("%s=[%s] expected=[%s]" %
                                   (member_name, actual, expected_text))
            if not pending:
                for member_name, unused in members:
                    print("[setup] MMR sync verified on %s: %s" %
                          (member_name, expected_text), flush=True)
                print("[setup] MMR bidirectional replication verified", flush=True)
                return
            time.sleep(1)
        raise OperationError("MMR 数据未在 30 秒内收敛: %s" % ", ".join(pending))

    def _mmr_udf_check(self, node_name, database, all_nodes=True):
        sql = (
            "SELECT coalesce(json_agg(row_to_json(status))::text, '[]') FROM ("
            "SELECT nodename,nodestate,real_nodestate,is_abnormal,detail "
            "FROM fdd.show_node_info(%s,false) ORDER BY nodename) status" %
            ("true" if all_nodes else "false"))
        raw = self._query_value(node_name, database, sql)
        try:
            return json.loads(raw)
        except ValueError:
            raise OperationError("无法解析 fdd.show_node_info 返回值: %s" % raw)

    def _setup_logical(self):
        logical = self.groups.get("logical")
        if not logical:
            return
        database = logical.get("database", "postgres")
        publisher_name = logical["publisher"]
        publication = logical["publication_name"]
        self._psql(publisher_name, database,
                   "CREATE PUBLICATION %s FOR ALL TABLES" % publication)
        publisher = self.nodes[publisher_name]
        for subscriber_name, options in (logical.get("subscribers") or {}).items():
            conninfo = "host=%s port=%s user=postgres dbname=%s" % (
                publisher["host"], publisher["port"], database)
            sql = (
                "CREATE SUBSCRIPTION %s CONNECTION %s PUBLICATION %s "
                "WITH (copy_data = false, slot_name = %s)"
            ) % (options["subscription_name"], _sql_literal(conninfo), publication,
                 _sql_literal(options["slot_name"]))
            self._psql(subscriber_name, database, sql)
