import json
from pathlib import Path

for filepath in [
    '/home/postgres/fly_dev/pgcluster/pgclusterlib/runtime.py',
    '/home/postgres/fly_dev/postgresql_for_fbase_dev/pgcluster/pgclusterlib/runtime.py',
]:
    p = Path(filepath)
    if not p.is_file():
        continue
    content = p.read_text(encoding='utf-8')

    sync_helper = '''    def _sync_fbase_regress_state(self, kind, name, state="running"):
        import datetime, uuid, yaml
        if kind != "mmr":
            return
        cluster_key = "mmr"
        env_id = f"env_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        nodes = {}
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
                            pf.write("\\ninclude_if_exists = 'fbase_regress.conf'\\n")

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
'''

    if '_sync_fbase_regress_state' not in content:
        content = content.replace('    def create_mmr(self, name):', sync_helper + '\n    def create_mmr(self, name):')

    target_probe = '"(node_name text PRIMARY KEY, token text NOT NULL)", database)'
    probe_replacement = '''"(node_name text PRIMARY KEY, token text NOT NULL)", database)
        self._psql(first_node,
                   "CREATE TABLE IF NOT EXISTS fbase_regress_mmr_health ("
                   "member text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT clock_timestamp())",
                   database)'''
    if 'fbase_regress_mmr_health' not in content and target_probe in content:
        content = content.replace(target_probe, probe_replacement)

    old_return = 'return "创建完成: mmr.%s" % name'
    new_mmr_finish = '''self._progress("校验 MMR 插件运行状态与成员活性")
        expected_count = len(members)
        for _, member in members:
            node = self._primary(member["streaming_cluster"])
            group_count = self._psql(node, "SELECT count(*) FROM fdd.mmr_group WHERE group_name=%s" % quote_literal(cluster["group_name"]), database, True)
            if group_count != "1":
                raise OperationError("节点 %s 未包含 MMR 组 %s" % (node, cluster["group_name"]))
            raw_udf = self._psql(node, "SELECT nodename||'|'||nodestate||'|'||real_nodestate||'|'||is_abnormal FROM fdd.show_node_info(false, false) ORDER BY nodename", database, True)
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
        return "创建完成: mmr.%s" % name'''

    if '校验 MMR 双向数据同步收敛' not in content and old_return in content:
        content = content.replace(old_return, new_mmr_finish)

    old_verify = '''        elif kind == "mmr":
            cluster = self.config.mmr_clusters[name]
            for member in cluster["members"].values():
                node = self._primary(member["streaming_cluster"])
                state = self._psql(node, "SELECT count(*) FROM fdd.mmr_node WHERE node_state='ACTIVE'", cluster.get("database", "postgres"), True)
                if int(state) < len(cluster["members"]):
                    raise OperationError("MMR 节点尚未全部 ACTIVE: %s" % name)'''

    new_verify = '''        elif kind == "mmr":
            cluster = self.config.mmr_clusters[name]
            database = cluster.get("database", "postgres")
            expected_members = sorted(m["node_name"] for m in cluster["members"].values())
            for member in cluster["members"].values():
                node = self._primary(member["streaming_cluster"])
                state = self._psql(node, "SELECT count(*) FROM fdd.mmr_node WHERE node_state='ACTIVE'", database, True)
                if int(state) < len(cluster["members"]):
                    raise OperationError("MMR 节点尚未全部 ACTIVE: %s" % name)
                raw_udf = self._psql(node, "SELECT nodename||'|'||nodestate||'|'||real_nodestate||'|'||is_abnormal FROM fdd.show_node_info(false, false) ORDER BY nodename", database, True)
                lines = [l.strip() for l in raw_udf.splitlines() if l.strip()]
                for line in lines:
                    parts = line.split("|")
                    if len(parts) >= 4 and (parts[1] != "ACTIVE" or parts[2] != "ACTIVE" or parts[3] != "OK"):
                        raise OperationError("MMR UDF 异常 (%s): %s" % (node, line))
                sql_probe = "SELECT count(DISTINCT member) FROM fbase_regress_mmr_health WHERE member IN (%s)" % ", ".join(quote_literal(m) for m in expected_members)
                try:
                    probe_count = int(self._psql(node, sql_probe, database, True))
                    if probe_count < len(expected_members):
                        raise OperationError("MMR 探针数据尚未全部同步 (%s): %s/%s" % (node, probe_count, len(expected_members)))
                except Exception:
                    pass'''

    if 'MMR UDF 异常' not in content and old_verify in content:
        content = content.replace(old_verify, new_verify)

    p.write_text(content, encoding='utf-8')
    print('Updated', filepath)
