"""Physical streaming replication lifecycle, failover, rejoin, and restore."""

import json
import time
from pathlib import Path

from .errors import OperationError, SafetyError
from .sql import quote_ident, quote_literal


class StreamingRuntimeMixin:
    def _ensure_streaming_plugins(self, cluster_name):
        """Fail fast when a member host lacks a required plugin binary."""
        cluster = self.config.streaming_clusters[cluster_name]
        members = [cluster["primary"]] + [
            item["instance"] for item in cluster.get("standbys") or []
        ]
        checked = set()
        for member in members:
            instance = self.config.instance(member)
            install = instance["installation_config"]
            host, home = self._host(instance), install["home"]
            for plugin_name, plugin in (install.get("plugins") or {}).items():
                if not plugin.get("required"):
                    continue
                key = (host, home, plugin_name)
                if key in checked:
                    continue
                checked.add(key)
                if plugin.get("extension") and not self._plugin_installed(host, home, plugin):
                    raise OperationError(
                        "插件 %s 未安装到 %s (%s)；请先装入数据库安装目录" %
                        (plugin["extension"], home, host))
                preload = plugin.get("preload_library")
                if preload and not self.executor.exists(host, home + "/lib/" + preload + ".so"):
                    raise OperationError(
                        "插件 %s 缺少预加载库 %s：请确认 %s (%s) 已完整安装" %
                        (plugin_name, preload + ".so", home, host))

    def _drop_physical_slots(self, streaming_name):
        cluster = self.config.streaming_clusters[streaming_name]
        primary = self._primary(streaming_name)
        # A physical slot cannot be removed while its receiver is connected.
        for standby in self._streaming_standbys(streaming_name):
            if standby in self.config.instances and self.status_instance(standby)["running"]:
                self.stop_instance(standby)
        for item in cluster.get("standbys") or []:
            slot = item.get("slot") or "%s_%s_slot" % (streaming_name, item["instance"])
            self._psql(primary, "SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots "
                       "WHERE slot_name=%s AND NOT active" % quote_literal(slot), tuples=True)

    def failover(self, target, yes=False, force=False):
        if not yes:
            raise SafetyError("failover 会改变主备角色；请使用 --yes")
        if not target.startswith("streaming."):
            raise OperationError("failover 当前只支持 streaming.<cluster>")
        cluster_name = target.split(".", 1)[1]
        if cluster_name not in self.config.streaming_clusters:
            raise OperationError("未知流复制集群: %s" % target)
        cluster = self.config.streaming_clusters[cluster_name]
        old_primary = self._primary(cluster_name)
        members = [cluster["primary"]] + [item["instance"] for item in cluster.get("standbys") or []]
        candidates = [item for item in members if item != old_primary]
        if len(candidates) != 1:
            raise OperationError("failover 要求恰好一个可提升备库: %s" % target)
        new_primary = candidates[0]
        self._progress("校验切换目标 %s (%s -> %s)" % (target, old_primary, new_primary))
        if not self._managed(old_primary) or not self._managed(new_primary):
            raise SafetyError("主库或备库不是 pgcluster 管理的数据目录")
        if not self.status_instance(new_primary)["running"]:
            raise OperationError("备库未运行，不能提升: %s" % new_primary)
        if self._psql(new_primary, "SELECT pg_is_in_recovery()", tuples=True) != "t":
            raise OperationError("目标实例不是备库: %s" % new_primary)
        if self.status_instance(old_primary)["running"]:
            instance = self.config.instance(old_primary)
            mode = "immediate" if force else "fast"
            self._progress("停止旧主库 %s" % old_primary)
            self.executor.run([self._bin(old_primary, "pg_ctl"), "stop", "-D", instance["data_dir"], "-m", mode, "-w"], host=self._host(instance))
        self._progress("提升备库 %s 为主库" % new_primary)
        self._promote(new_primary)
        state = self._state()
        state.setdefault("primaries", {})[cluster_name] = new_primary
        state.setdefault("failovers", {})[cluster_name] = {
            "old_primary": old_primary, "new_primary": new_primary, "rejoined": False,
        }
        self._save_state(state)
        return "已切换: %s (%s -> %s)" % (target, old_primary, new_primary)

    def _promote(self, name):
        instance = self.config.instance(name)
        self.executor.run([self._bin(name, "pg_ctl"), "promote", "-D",
                           instance["data_dir"], "-w"], host=self._host(instance))
        until = time.monotonic() + 30
        while time.monotonic() < until:
            if self._psql(name, "SELECT pg_is_in_recovery()", tuples=True) == "f":
                return
            time.sleep(.5)
        raise OperationError("提升后的实例未退出 recovery: %s" % name)

    def rejoin(self, target, yes=False):
        if not yes:
            raise SafetyError("rejoin 会重建旧主库数据目录；请使用 --yes")
        if not target.startswith("streaming."):
            raise OperationError("rejoin 当前只支持 streaming.<cluster>")
        cluster_name = target.split(".", 1)[1]
        cluster = self.config.streaming_clusters.get(cluster_name)
        if not cluster:
            raise OperationError("未知流复制集群: %s" % target)
        state = self._state()
        new_primary = self._primary(cluster_name)
        old_primary = state.get("failovers", {}).get(cluster_name, {}).get("old_primary", cluster["primary"])
        if old_primary == new_primary:
            raise OperationError("没有待重新加入的旧主库: %s" % target)
        # 新主库原备库槽位在提升后仍保留它停止消费的位置，旧主库接着它续传。
        slot = self._standby_slot(cluster_name, new_primary, fallback=old_primary)
        self._progress("将旧主库 %s 重新加入 %s" % (old_primary, target))
        self._rebuild_standby(cluster_name, new_primary, old_primary, slot)
        state.setdefault("failovers", {}).setdefault(cluster_name, {})["rejoined"] = True
        self._save_state(state)
        return "已重新加入: %s (%s -> standby)" % (target, old_primary)

    def _standby_slot(self, cluster_name, name, fallback=None):
        cluster = self.config.streaming_clusters[cluster_name]
        spec = next((item for item in cluster.get("standbys") or []
                     if item["instance"] == name), None)
        return (spec or {}).get("slot") or "%s_%s_slot" % (cluster_name, fallback or name)

    def _replication_host(self, primary, standby):
        source = self.config.instance(primary)["host_config"]["address"]
        destination = self.config.instance(standby)["host_config"]["address"]
        return "127.0.0.1" if source == destination else source

    def _rebuild_standby(self, cluster_name, primary, name, slot=None):
        """Rebuild ``name`` as a streaming standby of ``primary`` via basebackup."""
        slot = slot or self._standby_slot(cluster_name, name)
        source = self.config.instance(primary)
        destination = self.config.instance(name)
        if self.status_instance(name)["running"]:
            self._progress("停止 %s" % name)
            self.stop_instance(name)
        if not self._managed(name):
            raise SafetyError("数据目录不是 pgcluster 管理目录: %s" % name)
        self._progress("删除 %s 的数据目录" % name)
        self.executor.remove_tree(self._host(destination), destination["data_dir"])
        if self._psql(primary, "SELECT count(*) FROM pg_replication_slots WHERE slot_name=%s"
                      % quote_literal(slot), tuples=True) == "0":
            self._psql(primary, "SELECT pg_create_physical_replication_slot(%s)" % quote_literal(slot))
        self._progress("从 %s 重建 %s" % (primary, name))
        self.executor.run(["mkdir", "-p", str(Path(destination["data_dir"]).parent)], host=self._host(destination))
        self.executor.run([self._bin(name, "pg_basebackup"), "-h", self._replication_host(primary, name),
                           "-p", str(source["port"]), "-U", "postgres", "-D", destination["data_dir"],
                           "-R", "-X", "stream", "-S", slot], host=self._host(destination))
        self.executor.write_text(self._host(destination), self._marker(name),
                                 json.dumps({"node": name}) + "\n")
        self._write_config(name, primary, slot)
        self._progress("启动备库 %s" % name)
        self.start_instance(name)
        self._progress("等待备库 %s 就绪" % name)
        self._wait(name)
        # pg_basebackup -R 写入的 primary_conninfo 不带声明的 application_name，归一化。
        self._write_upstream(cluster_name, name, primary)
        self._psql(name, "SELECT pg_reload_conf()")

    def restore(self, target, yes=False, adopt=False):
        """恢复配置声明的主备角色。

        角色漂移可能绕过 pgcluster 状态（回归用例或人工直接 ``pg_ctl
        promote``），因此以实例实际 ``pg_is_in_recovery()`` 为准：先确保
        配置主库运行为主库，再把角色或上游不符的节点恢复为它的备库。

        ``adopt`` 把配置中声明但缺少管理标记的 PostgreSQL 数据目录纳入
        管理（写入 .pgcluster-managed），用于夹具创建的环境；之后这些
        目录的漂移节点可以被重建。
        """
        if not yes:
            raise SafetyError("restore 会重建角色漂移节点的数据目录；请使用 --yes")
        cluster_names = self._restore_clusters(target)
        if adopt:
            for cluster_name in cluster_names:
                cluster = self.config.streaming_clusters[cluster_name]
                members = [cluster["primary"]] + [
                    item["instance"] for item in cluster.get("standbys") or []]
                for name in members:
                    self._adopt(name)
        changed, unrepairable = [], []
        for cluster_name in cluster_names:
            fixed, skipped = self._restore_streaming(cluster_name)
            changed.extend(fixed)
            unrepairable.extend(skipped)
        if unrepairable:
            raise OperationError(
                "以下节点角色已分叉且数据目录不受 pgcluster 管理，无法自动重建: %s。"
                "确认目录归属后用 --adopt 纳入管理，或由夹具/人工重建" % ", ".join(unrepairable))
        if not changed:
            return "角色与配置一致: %s" % target
        return "已恢复配置角色: %s (%s)" % (target, ", ".join(sorted(set(changed))))

    def _restore_clusters(self, target):
        kind, _, name = target.partition(".")
        if kind == "streaming":
            if name not in self.config.streaming_clusters:
                raise OperationError("未知流复制集群: %s" % target)
            return [name]
        if kind == "mmr":
            cluster = self.config.mmr_clusters.get(name)
            if not cluster:
                raise OperationError("未知 MMR 集群: %s" % target)
            return [item["streaming_cluster"] for item in cluster["members"].values()]
        raise OperationError("restore 需要 streaming.<集群> 或 mmr.<集群> 目标: %s" % target)

    def _live_role(self, name):
        """True=备库、False=主库、None=未运行。"""
        if not self.status_instance(name)["running"]:
            return None
        return self._psql(name, "SELECT pg_is_in_recovery()", tuples=True) == "t"

    def _upstream_matches(self, cluster_name, name, primary):
        """备库的 primary_conninfo 是否指向 ``primary`` 且用声明的 application_name。"""
        parent = self.config.instance(primary)
        try:
            conninfo = self._psql(name, "SHOW primary_conninfo", tuples=True)
        except OperationError:
            return False
        parts = dict(item.split("=", 1) for item in conninfo.split() if "=" in item)
        spec = next((item for item in
                     self.config.streaming_clusters[cluster_name].get("standbys") or []
                     if item["instance"] == name), {})
        hosts = {parent["host_config"]["address"]}
        if self.config.instance(name)["host_config"]["address"] == parent["host_config"]["address"]:
            hosts.update({"127.0.0.1", "localhost", "::1"})
        return (parts.get("host") in hosts
                and parts.get("port") == str(parent["port"])
                and parts.get("application_name") == (spec.get("application_name") or name))

    def _is_managed(self, name):
        try:
            return self._managed(name)
        except SafetyError:
            return False

    def _adopt(self, name):
        """Write the managed marker into a config-declared PostgreSQL data dir."""
        if self._is_managed(name):
            return
        instance = self.config.instance(name)
        host = self._host(instance)
        data_dir = instance["data_dir"]
        if not self.executor.is_nonempty_dir(host, data_dir):
            raise OperationError("无法认领空目录: %s" % data_dir)
        if not self.executor.exists(host, str(Path(data_dir) / "PG_VERSION")):
            raise OperationError("无法认领非 PostgreSQL 数据目录: %s" % data_dir)
        self.executor.write_text(host, self._marker(name), json.dumps({"node": name}) + "\n")
        self._progress("已将 %s 纳入 pgcluster 管理" % name)

    def _write_upstream(self, cluster_name, name, primary):
        """把备库的 primary_conninfo/primary_slot_name 写到声明的形态。"""
        slot = self._standby_slot(cluster_name, name)
        parent = self.config.instance(primary)
        if self._psql(primary, "SELECT count(*) FROM pg_replication_slots WHERE slot_name=%s"
                      % quote_literal(slot), tuples=True) == "0":
            self._psql(primary, "SELECT pg_create_physical_replication_slot(%s)" % quote_literal(slot))
        spec = next((item for item in
                     self.config.streaming_clusters[cluster_name].get("standbys") or []
                     if item["instance"] == name), {})
        app = spec.get("application_name") or name
        conninfo = "host=%s port=%s user=postgres application_name=%s" % (
            self._replication_host(primary, name), parent["port"], app)
        self._psql(name, "ALTER SYSTEM SET primary_conninfo = %s" % quote_literal(conninfo))
        self._psql(name, "ALTER SYSTEM SET primary_slot_name = %s" % quote_literal(slot))

    def _repoint_upstream(self, cluster_name, name, primary):
        """把仍在 recovery 的备库上游改指配置主库（非破坏性）。"""
        self._write_upstream(cluster_name, name, primary)
        self._progress("重启 %s 使新上游生效" % name)
        self.stop_instance(name)
        self.start_instance(name)
        self._wait(name)

    def _restore_streaming(self, cluster_name):
        cluster = self.config.streaming_clusters[cluster_name]
        configured = cluster["primary"]
        standbys = [item["instance"] for item in cluster.get("standbys") or []]
        roles = {name: self._live_role(name) for name in [configured] + standbys}
        changed, unrepairable = [], []
        if roles[configured] is None:
            self._progress("配置主库 %s 未运行，拉起" % configured)
            self.start_instance(configured)
            self._wait(configured)
            roles[configured] = self._live_role(configured)
            changed.append(configured)
        if roles[configured]:
            self._progress("配置主库 %s 处于备库角色，提升为主库" % configured)
            self._promote(configured)
            changed.append(configured)
        for name in standbys:
            if roles[name] is None:
                self._progress("备库 %s 未运行，拉起后探测角色" % name)
                try:
                    self.start_instance(name)
                    self._wait(name)
                    roles[name] = self._live_role(name)
                    changed.append(name)
                except OperationError:
                    self._progress("备库 %s 无法拉起" % name)
                    roles[name] = False
            if roles[name] is True:
                if self._upstream_matches(cluster_name, name, configured):
                    continue
                self._progress("备库 %s 上游未指向 %s，改指后重启" % (name, configured))
                self._repoint_upstream(cluster_name, name, configured)
                changed.append(name)
                continue
            if not self._is_managed(name):
                unrepairable.append(name)
                continue
            self._progress("%s 角色已分叉，重建为 %s 的备库" % (name, configured))
            self._rebuild_standby(cluster_name, configured, name)
            changed.append(name)
        state = self._state()
        state.setdefault("primaries", {})[cluster_name] = configured
        state.get("failovers", {}).pop(cluster_name, None)
        self._save_state(state)
        return changed, unrepairable

    def _write_config(self, name, primary=None, slot=None, extra=None,
                      application_name=None):
        instance = self.config.instance(name)
        host, data_dir = self._host(instance), instance["data_dir"]
        params = dict((self.config.raw.get("postgresql_config") or {}).get("parameters") or {})
        params.update(extra or {})
        params["port"] = instance["port"]
        params["listen_addresses"] = "*"
        if primary:
            parent = self.config.instance(primary)
            params["primary_conninfo"] = "host=%s port=%s user=postgres application_name=%s" % (self._replication_host(primary, name), parent["port"], application_name or name)
            params["primary_slot_name"] = slot
        lines = ["# Managed by pgcluster"]
        for key, value in sorted(params.items()):
            if isinstance(value, list):
                value = ",".join(value)
            lines.append("%s = %s" % (key, quote_literal(value) if isinstance(value, str) else value))
        self.executor.write_text(host, str(Path(data_dir) / "pgcluster.conf"), "\n".join(lines) + "\n")
        postgres_conf = str(Path(data_dir) / "postgresql.conf")
        existing_conf = self.executor.read_text(host, postgres_conf)
        if "include_if_exists = 'pgcluster.conf'" not in existing_conf:
            self.executor.append_text(host, postgres_conf, "\ninclude_if_exists = 'pgcluster.conf'\n")
        # Mirror the declared parameters into postgresql.auto.conf so they are
        # visible through pg_file_settings as the deployment baseline. FBase
        # security GUCs reject ALTER SYSTEM RESET entirely, so test fixtures
        # that restore settings can only return to the baseline through
        # ALTER SYSTEM SET — which requires an existing auto.conf entry.
        self._seed_auto_conf(host, data_dir, params)
        hba_rules = self.config.raw.get("postgresql_config", {}).get("hba")
        if hba_rules:
            lines = []
            for rule in hba_rules:
                fields = [rule["type"], rule["database"], rule["user"]]
                if rule["type"] != "local":
                    fields.append(rule["address"])
                fields.append(rule["auth_method"])
                lines.append(" ".join(fields))
            hba = "\n".join(lines) + "\n"
        else:
            hba = "local all all trust\nhost all all 0.0.0.0/0 trust\nhost replication all 0.0.0.0/0 trust\n"
        self.executor.write_text(host, str(Path(data_dir) / "pg_hba.conf"), hba)

    def _seed_auto_conf(self, host, data_dir, params):
        path = str(Path(data_dir) / "postgresql.auto.conf")
        existing = ""
        if self.executor.exists(host, path):
            existing = self.executor.read_text(host, path) or ""
        kept = []
        for line in existing.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                if stripped.split("=", 1)[0].strip() in params:
                    continue  # rewritten from the declared parameters below
            kept.append(line)
        while kept and not kept[-1].strip():
            kept.pop()
        kept.append("")
        kept.append("# pgcluster baseline: declared profile parameters")
        for key in sorted(params):
            value = params[key]
            if isinstance(value, list):
                value = ",".join(value)
            rendered = quote_literal(value) if isinstance(value, str) else str(value)
            kept.append("%s = %s" % (key, rendered))
        self.executor.write_text(host, path, "\n".join(kept) + "\n")

    def _init_primary(self, name, extra=None):
        instance = self.config.instance(name)
        host, data_dir = self._host(instance), instance["data_dir"]
        if self.executor.is_nonempty_dir(host, data_dir):
            self._managed(name)
            if not self.status_instance(name)["running"]:
                # 首次启动失败后的重试要重写受管配置，再启动原实例。
                self._write_config(name, extra=extra)
                self.start_instance(name)
            return
        self.executor.run(["mkdir", "-p", str(Path(data_dir).parent)], host=host)
        self.executor.run([self._bin(name, "initdb"), "-D", data_dir, "-U", "postgres", "--auth-local=trust", "--auth-host=trust"], host=host)
        license_config = instance["installation_config"].get("license")
        if license_config:
            source = license_config["source_file"]
            target = str(Path(data_dir) / license_config.get("data_file", "license.dat"))
            self.executor.run(["cp", source, target], host=host)
        self.executor.write_text(host, self._marker(name), json.dumps({"node": name}) + "\n")
        self._write_config(name, extra=extra)
        self.start_instance(name)

    def _wait(self, name, seconds=30):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            try:
                if self._psql(name, "SELECT 1", tuples=True) == "1":
                    return
            except OperationError:
                pass
            time.sleep(.5)
        raise OperationError("实例未在 %s 秒内就绪: %s" % (seconds, name))

    def create_streaming(self, cluster_name, extra=None):
        cluster = self.config.streaming_clusters[cluster_name]
        primary = self._primary(cluster_name)
        self._progress("创建流复制集群 streaming.%s" % cluster_name)
        self._ensure_streaming_plugins(cluster_name)
        self._progress("初始化主库 %s" % primary)
        self._init_primary(primary, extra=extra)
        self._progress("等待主库 %s 就绪" % primary)
        self._wait(primary)
        for standby in cluster.get("standbys") or []:
            name = standby["instance"]
            if name == primary:
                continue
            self._progress("配置备库 %s" % name)
            slot = standby.get("slot") or "%s_%s_slot" % (cluster_name, name)
            instance, host = self.config.instance(name), self._host(self.config.instance(name))
            exists = self._psql(primary, "SELECT slot_type FROM pg_replication_slots WHERE slot_name=%s" % quote_literal(slot), tuples=True)
            if not exists:
                self._psql(primary, "SELECT pg_create_physical_replication_slot(%s)" % quote_literal(slot))
            if not self._managed(name):
                self.executor.run(["mkdir", "-p", str(Path(instance["data_dir"]).parent)], host=host)
                p = self.config.instance(primary)
                self.executor.run([self._bin(name, "pg_basebackup"), "-h", self._replication_host(primary, name), "-p", str(p["port"]), "-U", "postgres", "-D", instance["data_dir"], "-R", "-X", "stream", "-S", slot], host=host)
                self.executor.write_text(host, self._marker(name), json.dumps({"node": name}) + "\n")
            self._write_config(name, primary, slot, extra,
                               standby.get("application_name"))
            if not self.status_instance(name)["running"]:
                self.start_instance(name)
            self._progress("等待备库 %s 就绪" % name)
            self._wait(name)
        self._progress("流复制集群 streaming.%s 已就绪" % cluster_name)
        for extension in cluster.get("extensions") or []:
            self._progress("启用扩展 %s" % extension)
            self._psql(primary, "CREATE EXTENSION IF NOT EXISTS %s" % quote_ident(extension),
                       cluster.get("database") or "postgres")
        for database, options in (cluster.get("databases") or {}).items():
            self._progress("创建数据库 %s" % database)
            if self._psql(primary, "SELECT count(*) FROM pg_database WHERE datname=%s"
                          % quote_literal(database), "postgres", True) != "1":
                self._psql(primary, "CREATE DATABASE %s" % quote_ident(database))
            db_extensions = (
                options if isinstance(options, list)
                else options.get("extensions") if isinstance(options, dict) else []
            ) or []
            for extension in db_extensions:
                self._progress("启用扩展 %s.%s" % (database, extension))
                self._psql(primary, "CREATE EXTENSION IF NOT EXISTS %s" % quote_ident(extension), database)
        self._sync_fbase_regress_state("streaming", cluster_name, "running")
        return "创建完成: streaming.%s" % cluster_name
