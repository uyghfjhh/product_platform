"""Multi-master replication (MMR) and Citus distributed cluster lifecycle."""

import json
import time
from pathlib import Path

from .errors import OperationError
from .sql import quote_ident, quote_literal


class MmrCitusRuntimeMixin:
    def _drop_citus(self, name):
        cluster = self.config.citus_clusters[name]
        coordinator = self._primary(cluster["coordinator"]["streaming_cluster"])
        database = cluster.get("database", "postgres")
        # A Worker with shard placements cannot be removed first.  Dropping
        # the Coordinator extension with CASCADE removes distributed tables
        # and pg_dist_node metadata as one consistent operation.
        self._psql(coordinator, "DROP EXTENSION IF EXISTS citus CASCADE", database)
        streams = [cluster["coordinator"]["streaming_cluster"]]
        streams.extend(item["streaming_cluster"] for item in cluster["workers"].values())
        for streaming in streams:
            primary = self._primary(streaming)
            if primary != coordinator:
                self._psql(primary, "DROP EXTENSION IF EXISTS citus CASCADE", database)

    def _drop_mmr(self, name):
        cluster = self.config.mmr_clusters[name]
        database = cluster.get("database", "postgres")
        members = list(cluster["members"].values())
        control = self._primary(members[0]["streaming_cluster"])
        # FBase requires PARTED before DROP.  Part and drop non-control nodes
        # from the still-ACTIVE control member, then part/drop the control
        # member itself as the final group operation.
        for member in reversed(members[1:]):
            exists = self._psql(control, "SELECT count(*) FROM fdd.mmr_node WHERE node_name=%s" %
                                quote_literal(member["node_name"]), database, True)
            if exists == "1":
                self._psql(control, "SELECT fdd.part_node(%s,true,true)" % quote_literal(member["node_name"]), database)
                self._psql(control, "SELECT fdd.drop_node(%s,true)" % quote_literal(member["node_name"]), database)
        first = members[0]
        exists = self._psql(control, "SELECT count(*) FROM fdd.mmr_node WHERE node_name=%s" %
                            quote_literal(first["node_name"]), database, True)
        if exists == "1":
            self._psql(control, "SELECT fdd.part_node(%s,true,true)" % quote_literal(first["node_name"]), database)
            self._psql(control, "SELECT fdd.drop_node(%s,true)" % quote_literal(first["node_name"]), database)
        for member in members:
            node = self._primary(member["streaming_cluster"])
            # MMR creates subscriptions owned by the extension workflow.
            subscriptions = self._psql(node, "SELECT subname FROM pg_subscription WHERE subname LIKE 'fmmr_%'", database, True)
            for subscription in filter(None, subscriptions.splitlines()):
                self._psql(node, "ALTER SUBSCRIPTION %s DISABLE" % quote_ident(subscription), database)
                self._psql(node, "DROP SUBSCRIPTION %s" % quote_ident(subscription), database)
            for extension in ("fbase_mac", "fdd_mmr", "fb_license"):
                self._psql(node, "DROP EXTENSION IF EXISTS %s CASCADE" % quote_ident(extension), database)

    def create_citus(self, name):
        cluster = self.config.citus_clusters[name]
        names = [cluster["coordinator"]["streaming_cluster"]] + [v["streaming_cluster"] for v in cluster["workers"].values()]
        self._progress("创建 Citus 集群 citus.%s" % name)
        self._progress("检查 Citus 扩展前置条件")
        for streaming in names:
            primary = self._primary(streaming)
            instance = self.config.instance(primary)
            home = instance["installation_config"]["home"]
            controls = [home + "/share/extension/citus.control",
                        home + "/share/postgresql/extension/citus.control"]
            if not any(self.executor.exists(self._host(instance), item) for item in controls):
                raise OperationError("Citus 环境缺失: %s" % " 或 ".join(controls))
        extra = cluster.get("postgresql_config", {}).get("parameters", {})
        for streaming in names:
            self.create_streaming(streaming, extra)
        database = cluster.get("database", "postgres")
        coordinator = self._primary(names[0])
        self._progress("启用 Citus 扩展")
        for streaming in names:
            primary = self._primary(streaming)
            self._psql(primary, "CREATE EXTENSION IF NOT EXISTS citus", database)
        for worker in cluster["workers"].values():
            node = self.config.instance(self._primary(worker["streaming_cluster"]))
            exists = self._psql(coordinator, "SELECT count(*) FROM pg_dist_node WHERE nodename=%s AND nodeport=%s" %
                                (quote_literal(node["host_config"]["address"]), node["port"]), database, True)
            if exists == "0":
                self._progress("向 Coordinator 注册 Worker %s:%s" %
                               (node["host_config"]["address"], node["port"]))
                self._psql(coordinator, "SELECT citus_add_node(%s,%s)" %
                           (quote_literal(node["host_config"]["address"]), node["port"]), database)
        return "创建完成: citus.%s" % name

    def _sync_fbase_regress_state(self, kind, name, state="running"):
        import datetime, uuid, yaml
        if kind != "mmr":
            # Only the MMR topology maps onto the legacy multi-active cluster
            # shape; streaming/logical sync would write an incomplete state
            # file and mislead any consumer that still reads it.
            return
        env_id = f"env_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        nodes = {}
        cluster_key = "mmr"
        cluster = self.config.mmr_clusters[name]
        for member_name, member in cluster["members"].items():
            streaming_name = member["streaming_cluster"]
            streaming = self.config.streaming_clusters[streaming_name]
            pri_name = streaming["primary"]
            pri_inst = self.config.instance(pri_name)
            nodes[pri_name] = {
                "data_dir": str(Path(pri_inst["data_dir"]).resolve()),
                "host": pri_inst["host_config"]["address"],
                "port": int(pri_inst["port"]),
                "role": f"mmr_primary:{member_name}",
            }
            for s in (streaming.get("standbys") or []):
                std_name = s["instance"]
                std_inst = self.config.instance(std_name)
                nodes[std_name] = {
                    "data_dir": str(Path(std_inst["data_dir"]).resolve()),
                    "host": std_inst["host_config"]["address"],
                    "port": int(std_inst["port"]),
                    "role": f"mmr_standby:{member_name}",
                }

        for node_name, info in nodes.items():
            d = Path(info["data_dir"])
            if d.is_dir():
                marker_file = d / ".fbase_regress_v2.json"
                payload = {"cluster": cluster_key, "env_id": env_id, "node": node_name}
                marker_file.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
                pg_conf = d / "postgresql.conf"
                if pg_conf.is_file():
                    c = pg_conf.read_text(encoding="utf-8", errors="ignore")
                    if "include_if_exists = 'fbase_regress.conf'" not in c:
                        with pg_conf.open("a", encoding="utf-8") as pf:
                            pf.write("\ninclude_if_exists = 'fbase_regress.conf'\n")

        state_payload = {
            "cluster": cluster_key,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "env_id": env_id,
            "nodes": nodes,
            "state": state,
        }
        for root_path in [
            "/home/postgres/fly_dev/product_platform/regress/fbase",
            "/home/postgres/fly_dev/postgresql_for_fbase_dev/fbase_regress",
        ]:
            r = Path(root_path)
            if r.is_dir():
                out_dir = r / "output" / "envs" / cluster_key
                out_dir.mkdir(parents=True, exist_ok=True)
                with (out_dir / "state.yaml").open("w", encoding="utf-8") as stream:
                    yaml.safe_dump(state_payload, stream, default_flow_style=False)

    def create_mmr(self, name):
        cluster = self.config.mmr_clusters[name]
        extra = cluster.get("postgresql_config", {}).get("parameters", {})
        members = list(cluster["members"].items())
        self._progress("创建 MMR 集群 mmr.%s" % name)
        for _, member in members:
            self.create_streaming(member["streaming_cluster"], extra)
        database = cluster.get("database", "postgres")
        first_name, first = members[0]
        first_node = self._primary(first["streaming_cluster"])
        first_instance = self.config.instance(first_node)
        first_dsn = "host=%s port=%s dbname=%s user=postgres" % (first_instance["host_config"]["address"], first_instance["port"], database)
        # fbase_mac protects its own catalog tables from TRUNCATE.  Creating it
        # before join_group makes the initial table-copy worker fail while it
        # prepares those tables.  Build the MMR group first, then enable the
        # security extension on the converged members.
        bootstrap_extensions = [item for item in cluster["extensions"] if item != "fbase_mac"]
        deferred_extensions = [item for item in cluster["extensions"] if item == "fbase_mac"]
        for _, member in members:
            node = self._primary(member["streaming_cluster"])
            self._progress("配置 MMR 成员 %s" % member["node_name"])
            self._psql(node, "CREATE SCHEMA IF NOT EXISTS pgcluster", database)
            for extension in bootstrap_extensions:
                self._psql(node, "CREATE EXTENSION IF NOT EXISTS %s" % quote_ident(extension), database)
            options = member.get("mmr_node") or {}
            instance = self.config.instance(node)
            dsn = "host=%s port=%s dbname=%s user=postgres" % (instance["host_config"]["address"], instance["port"], database)
            exists = self._psql(node, "SELECT count(*) FROM fdd.mmr_node WHERE node_name=%s" % quote_literal(member["node_name"]), database, True)
            if exists == "0":
                self._psql(node, "SELECT fdd.create_node(%s,%s,%s,%s,%s)" % (quote_literal(member["node_name"]), quote_literal(dsn), "true" if options.get("failover_slot", True) else "false", quote_literal(options.get("streaming", "off")), "true" if options.get("two_phase", False) else "false"), database)
        if self._psql(first_node, "SELECT count(*) FROM fdd.mmr_group WHERE group_name=%s" % quote_literal(cluster["group_name"]), database, True) == "0":
            self._progress("创建 MMR 组 %s" % cluster["group_name"])
            self._psql(first_node, "SELECT fdd.create_group(%s)" % quote_literal(cluster["group_name"]), database)
        self._psql(first_node, "CREATE SCHEMA IF NOT EXISTS pgcluster; "
                   "CREATE TABLE IF NOT EXISTS pgcluster.mmr_probe "
                   "(node_name text PRIMARY KEY, token text NOT NULL)", database)
        self._psql(first_node,
                   "CREATE TABLE IF NOT EXISTS fbase_regress_mmr_health ("
                   "member text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT clock_timestamp())",
                   database)
        for _, member in members[1:]:
            node = self._primary(member["streaming_cluster"])
            join = member.get("join") or {}
            if join.get("synchronize_structure") == "data-only":
                # The engine's probe table must also exist on a data-only joiner.
                # Business tables remain the caller's responsibility.
                self._psql(node, "CREATE SCHEMA IF NOT EXISTS pgcluster; "
                           "CREATE TABLE IF NOT EXISTS pgcluster.mmr_probe "
                           "(node_name text PRIMARY KEY, token text NOT NULL)", database)
                self._psql(node,
                           "CREATE TABLE IF NOT EXISTS fbase_regress_mmr_health ("
                           "member text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT clock_timestamp())",
                           database)
            state = self._psql(
                node,
                "SELECT node_state FROM fdd.mmr_node WHERE node_name=%s"
                % quote_literal(member["node_name"]),
                database,
                True,
            )
            if state != "ACTIVE":
                self._progress("将成员 %s 加入 MMR 组" % member["node_name"])
                self._psql(node, "SELECT fdd.join_group(%s,%s,%s,%s,%s)" % (quote_literal(cluster["group_name"]), quote_literal(first_dsn), "true" if join.get("wait_for_completion", True) else "false", quote_literal(join.get("synchronize_structure", "all")), quote_literal(join.get("precheck", "table_exist_error"))), database)
        for _, member in members:
            node = self._primary(member["streaming_cluster"])
            for extension in deferred_extensions:
                self._psql(node, "CREATE EXTENSION IF NOT EXISTS %s" % quote_ident(extension), database)
        self._progress("校验 MMR 插件运行状态与成员活性")
        expected_count = len(members)
        for _, member in members:
            node = self._primary(member["streaming_cluster"])
            group_count = self._psql(node, "SELECT count(*) FROM fdd.mmr_group WHERE group_name=%s" % quote_literal(cluster["group_name"]), database, True)
            if group_count != "1":
                raise OperationError("节点 %s 未包含 MMR 组 %s" % (node, cluster["group_name"]))
            raw_udf = self._psql(node, "SELECT nodename||'|'||nodestate||'|'||real_nodestate||'|'||is_abnormal FROM fdd.show_node_info(true, false) ORDER BY nodename", database, True)
            lines = [l.strip() for l in raw_udf.splitlines() if l.strip()]
            if len(lines) != expected_count:
                raise OperationError("节点 %s 的 show_node_info 返回成员数量不符: %s != %s" % (node, len(lines), expected_count))
            for line in lines:
                parts = line.split("|")
                if len(parts) >= 4 and (parts[1] != "ACTIVE" or parts[2] != "ACTIVE" or parts[3] != "OK"):
                    raise OperationError("节点 %s 观察到异常成员: %s" % (node, line))

        self._progress("校验 MMR 双向数据同步收敛")
        for _, member in members:
            node = self._primary(member["streaming_cluster"])
            self._psql(node, "INSERT INTO fbase_regress_mmr_health(member) VALUES (%s) ON CONFLICT (member) DO NOTHING" % quote_literal(member["node_name"]), database)

        expected_members = sorted(m["node_name"] for _, m in members)
        expected_text = ",".join(expected_members)
        member_filter = ", ".join(quote_literal(m) for m in expected_members)
        deadline = time.monotonic() + 30
        converged = False
        while time.monotonic() < deadline:
            pending = []
            for _, member in members:
                node = self._primary(member["streaming_cluster"])
                actual = self._psql(node, "SELECT coalesce(string_agg(member, ',' ORDER BY member), '') FROM fbase_regress_mmr_health WHERE member IN (%s)" % member_filter, database, True)
                if actual != expected_text:
                    pending.append("%s=[%s]" % (member["node_name"], actual))
            if not pending:
                converged = True
                break
            time.sleep(1)
        if not converged:
            raise OperationError("MMR 数据未在 30 秒内完成双向收敛: %s" % ", ".join(pending))

        self._sync_fbase_regress_state("mmr", name, "running")
        return "创建完成: mmr.%s" % name
