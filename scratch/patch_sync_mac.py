from pathlib import Path
import py_compile

target_code_search = '''    def _sync_fbase_regress_state(self, kind, name, state="running"):
        import datetime, uuid, yaml
        if kind != "mmr":
            return
        cluster_key = "mmr"
        env_id = f"env_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        nodes = {}
        cluster = self.config.mmr_clusters[name]'''

replacement_code = '''    def _sync_fbase_regress_state(self, kind, name, state="running"):
        import datetime, uuid, yaml
        env_id = f"env_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        nodes = {}
        if kind == "mmr":
            cluster_key = "mmr"
            cluster = self.config.mmr_clusters[name]'''

for filepath in [
    '/home/postgres/fly_dev/pgcluster/pgclusterlib/runtime.py',
    '/home/postgres/fly_dev/postgresql_for_fbase_dev/pgcluster/pgclusterlib/runtime.py',
]:
    p = Path(filepath)
    if not p.is_file():
        continue
    content = p.read_text(encoding='utf-8')
    if target_code_search in content:
        content = content.replace(target_code_search, replacement_code)
        
        # Also add mac handling after mmr handling
        old_tail = '''                    nodes[std_name] = {
                        "data_dir": str(Path(std_inst["data_dir"]).resolve()),
                        "host": std_inst["host_config"]["address"],
                        "port": int(std_inst["port"]),
                        "role": f"mmr_standby:{member_name}",
                    }'''
        new_tail = '''                    nodes[std_name] = {
                        "data_dir": str(Path(std_inst["data_dir"]).resolve()),
                        "host": std_inst["host_config"]["address"],
                        "port": int(std_inst["port"]),
                        "role": f"mmr_standby:{member_name}",
                    }
        elif kind == "streaming" and name == "mac":
            cluster_key = "mac"
            for inst_name in ["mac_primary", "mac_standby", "logical_subscriber"]:
                if inst_name in self.config.instances:
                    inst = self.config.instance(inst_name)
                    role = "primary" if "primary" in inst_name else "standby" if "standby" in inst_name else "logical_subscriber"
                    nodes[inst_name] = {
                        "data_dir": str(Path(inst["data_dir"]).resolve()),
                        "host": inst["host_config"]["address"],
                        "port": int(inst["port"]),
                        "role": role,
                    }
        else:
            return'''
        content = content.replace(old_tail, new_tail)

        # Call in create_streaming:
        old_create_stream = 'self._progress("流复制集群 streaming.%s 已就绪" % cluster_name)\n        return "创建完成: streaming.%s" % cluster_name'
        new_create_stream = 'self._progress("流复制集群 streaming.%s 已就绪" % cluster_name)\n        self._sync_fbase_regress_state("streaming", cluster_name, "running")\n        return "创建完成: streaming.%s" % cluster_name'
        content = content.replace(old_create_stream, new_create_stream)

        p.write_text(content, encoding='utf-8')
        py_compile.compile(filepath, doraise=True)
        print("Patched and compiled:", filepath)
    else:
        print("Pattern not found in:", filepath)
