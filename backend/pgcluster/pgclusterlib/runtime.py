import json
import time
from pathlib import Path

from .errors import OperationError, SafetyError
from .executor import Executor
from .runtime_logical import LogicalRuntimeMixin
from .runtime_mmr import MmrCitusRuntimeMixin
from .runtime_streaming import StreamingRuntimeMixin
from .sql import quote_literal

MARKER = ".pgcluster-managed"


class Runtime(StreamingRuntimeMixin, LogicalRuntimeMixin, MmrCitusRuntimeMixin):
    """Lifecycle, topology verification, and replication primitives."""

    def __init__(self, config, executor=None, progress=None):
        self.config = config
        self.executor = executor or Executor(
            hosts={
                host["address"]: host.get("ssh")
                for host in config.hosts.values()
                if host.get("ssh")
            }
        )
        self.progress = progress

    def _progress(self, message):
        if self.progress:
            self.progress(message)

    def _state_path(self):
        return self.config.path.with_name(".%s.state.json" % self.config.path.name)

    def _state(self):
        path = self._state_path()
        if not path.exists():
            return {"primaries": {}}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OperationError("无法读取运行状态 %s: %s" % (path, exc))
        if not isinstance(value, dict) or not isinstance(value.get("primaries", {}), dict):
            raise OperationError("运行状态文件格式无效: %s" % path)
        return value

    def _save_state(self, state):
        path = self._state_path()
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _primary(self, cluster_name):
        cluster = self.config.streaming_clusters[cluster_name]
        return self._state().get("primaries", {}).get(cluster_name, cluster["primary"])

    def _streaming_standbys(self, cluster_name):
        cluster = self.config.streaming_clusters[cluster_name]
        primary = self._primary(cluster_name)
        configured = [item["instance"] for item in cluster.get("standbys") or []]
        if primary == cluster["primary"]:
            return configured
        return [cluster["primary"]] + [item for item in configured if item != primary]

    def _host(self, instance):
        address = instance["host_config"]["address"]
        return "local" if self.executor.is_local(address) else address

    @staticmethod
    def _extension_controls(home, extension):
        return [
            home + "/share/extension/" + extension + ".control",
            home + "/share/postgresql/extension/" + extension + ".control",
        ]

    def _plugin_installed(self, host, home, plugin):
        extension = plugin.get("extension")
        if not extension:
            return True
        return any(self.executor.exists(host, control) for control in self._extension_controls(home, extension))

    def _installation_hosts(self, installation_name):
        hosts = []
        for name, instance in self.config.instances.items():
            if instance["installation"] != installation_name:
                continue
            host = self._host(self.config.instance(name))
            if host not in hosts:
                hosts.append(host)
        if not hosts:
            raise OperationError("安装 %s 没有被任何实例使用" % installation_name)
        return hosts

    def doctor_instance(self, name):
        instance = self.config.instance(name)
        host = self._host(instance)
        install = instance["installation_config"]
        home = install["home"]
        checks = {
            "data_dir": self.executor.is_dir(host, instance["data_dir"]),
            "postgres": self.executor.exists(host, home + "/bin/postgres"),
            "pg_ctl": self.executor.exists(host, home + "/bin/pg_ctl"),
            "initdb": self.executor.exists(host, home + "/bin/initdb"),
            "pg_basebackup": self.executor.exists(host, home + "/bin/pg_basebackup"),
            "psql": self.executor.exists(host, home + "/bin/psql"),
        }
        for plugin_name, plugin in (install.get("plugins") or {}).items():
            if plugin.get("extension"):
                checks["extension.%s" % plugin_name] = self._plugin_installed(host, home, plugin)
        license_config = install.get("license")
        if license_config:
            checks["license.source"] = self.executor.exists(host, license_config["source_file"])
            if checks["data_dir"]:
                checks["license.data"] = self.executor.exists(
                    host, str(Path(instance["data_dir"]) / license_config.get("data_file", "license.dat"))
                )
        return checks

    def install_installation(self, installation_name, force=False):
        install = self.config.installation(installation_name)
        home = install["home"]
        hosts = self._installation_hosts(installation_name)
        self._progress("检查安装 %s 的插件前置条件" % installation_name)
        messages = []
        for plugin_name, plugin in (install.get("plugins") or {}).items():
            extension = plugin.get("extension")
            if not extension:
                messages.append("%s: 无扩展文件，跳过" % plugin_name)
                continue
            for host in hosts:
                if self._plugin_installed(host, home, plugin) and not force:
                    self._progress("插件 %s@%s 已安装，跳过" % (plugin_name, host))
                    messages.append("%s@%s: 已安装" % (plugin_name, host))
                    continue
                self._progress("编译并安装插件 %s@%s" % (plugin_name, host))
                source = plugin.get("source_dir")
                if not source:
                    raise OperationError(
                        "插件 %s@%s 未安装，且未配置 source_dir；请先安装到 %s" %
                        (plugin_name, host, home)
                    )
                if not self.executor.is_dir(host, source):
                    raise OperationError("插件源码目录不存在: %s (%s)" % (source, host))
                pg_config = home + "/bin/pg_config"
                if not self.executor.exists(host, pg_config):
                    raise OperationError("缺少 pg_config: %s" % pg_config)
                make_args = ["make", "-C", source, "PG_CONFIG=" + pg_config]
                self.executor.run(make_args, host=host)
                self.executor.run(["sudo", "-n"] + make_args + ["install"], host=host)
                if not self._plugin_installed(host, home, plugin):
                    raise OperationError("插件安装后仍未找到扩展控制文件: %s" % plugin_name)
                messages.append("%s@%s: 已安装" % (plugin_name, host))
        return "安装完成: %s\n%s" % (installation_name, "\n".join(messages) if messages else "没有配置插件")

    def status_instance(self, name):
        instance = self.config.instance(name)
        host = self._host(instance)
        install = instance["installation_config"]
        result = self.executor.run(
            [install["home"] + "/bin/pg_ctl", "status", "-D", instance["data_dir"]],
            host=host, check=False,
        )
        return {
            "running": result.returncode == 0,
            # pg_ctl uses exit code 3 for a reachable, stopped instance.
            "known": result.returncode in {0, 3},
            "message": (result.stdout or result.stderr).strip(),
        }

    def status_cluster(self, name):
        return {instance: self.status_instance(instance) for instance in self.config.cluster_instances(name)}

    def target_instances(self, target):
        """Resolve a CLI target to instance names from the current configuration model."""
        if target in self.config.instances:
            return [target]
        if "." not in target:
            raise OperationError("未知目标: %s" % target)
        kind, name = target.split(".", 1)
        streams = []
        if kind == "streaming":
            streams = [name]
        elif kind == "logical":
            link = self.config.logical_replications.get(name)
            if link:
                streams = [link["pub"]["streaming_cluster"], link["sub"]["streaming_cluster"]]
        elif kind == "citus":
            cluster = self.config.citus_clusters.get(name)
            if cluster:
                streams = [cluster["coordinator"]["streaming_cluster"]]
                streams.extend(item["streaming_cluster"] for item in cluster["workers"].values())
        elif kind == "mmr":
            cluster = self.config.mmr_clusters.get(name)
            if cluster:
                streams = [item["streaming_cluster"] for item in cluster["members"].values()]
        else:
            raise OperationError("未知目标类型: %s" % kind)
        if not streams or any(stream not in self.config.streaming_clusters for stream in streams):
            raise OperationError("未知目标: %s" % target)
        result = []
        for stream in streams:
            for instance in self.config.cluster_instances(stream):
                if instance not in result:
                    result.append(instance)
        if kind == "mmr":
            for instance in self.config.mmr_clusters[name].get("auxiliary_instances", []):
                if instance not in result:
                    result.append(instance)
        return result

    def status_target(self, target):
        return {name: self.status_instance(name) for name in self.target_instances(target)}

    def status_display(self, target):
        """Collect per-instance status while preserving individual probe errors."""
        states = {}
        for name in self.target_instances(target):
            try:
                state = self.status_instance(name)
                if not state["known"]:
                    state["running"] = None
                states[name] = state
            except OperationError as exc:
                states[name] = {"running": None, "message": str(exc)}
        return states

    def deployment_states(self, target=None):
        """Classify declared topology by the managed-data marker on each host."""
        targets = []
        collections = self.config._collections()
        if target:
            self._target_for_deployment(collections, target)
            targets = [target]
        else:
            for kind, values in collections.items():
                targets.extend("%s.%s" % (kind, name) for name in values)
        names = []
        for item in targets:
            for instance in self.target_instances(item):
                if instance not in names:
                    names.append(instance)
        instance_states = {}
        for name in names:
            instance = self.config.instance(name)
            result = self.executor.run(
                ["sh", "-c", 'test -d "$1" && test -e "$1/.pgcluster-managed"',
                 "sh", instance["data_dir"]],
                host=self._host(instance), check=False,
            )
            if result.returncode == 0:
                instance_states[name] = "已部署"
            elif result.stderr.strip():
                instance_states[name] = "未知"
            else:
                instance_states[name] = "未部署"

        states = {}
        for kind, values in collections.items():
            for name in values:
                target = "%s.%s" % (kind, name)
                if target not in targets:
                    continue
                members = [instance_states[item] for item in self.target_instances(target)]
                if all(item == "已部署" for item in members):
                    states[target] = "已部署"
                elif any(item == "未知" for item in members):
                    states[target] = "未知"
                elif any(item == "已部署" for item in members):
                    states[target] = "部分部署"
                else:
                    states[target] = "未部署"
        return states

    @staticmethod
    def _target_for_deployment(collections, target):
        if "." not in target:
            raise OperationError("未知目标: %s" % target)
        kind, name = target.split(".", 1)
        if kind not in collections or name not in collections[kind]:
            raise OperationError("未知目标: %s" % target)

    def start_instance(self, name):
        instance = self.config.instance(name)
        host = self._host(instance)
        install = instance["installation_config"]
        return self.executor.run(
            [install["home"] + "/bin/pg_ctl", "start", "-D", instance["data_dir"],
             "-l", str(Path(instance["data_dir"]) / "pg_ctl.log"), "-w"], host=host
        )

    def stop_instance(self, name, mode="fast", timeout=6):
        instance = self.config.instance(name)
        host = self._host(instance)
        install = instance["installation_config"]
        data_dir = instance["data_dir"]
        pg_ctl = install["home"] + "/bin/pg_ctl"
        try:
            return self.executor.run(
                [pg_ctl, "stop", "-D", data_dir, "-m", mode, "-w", "-t", str(timeout)],
                host=host,
            )
        except Exception:
            # 1. Fallback to immediate mode if fast mode hangs/times out
            try:
                return self.executor.run(
                    [pg_ctl, "stop", "-D", data_dir, "-m", "immediate", "-w", "-t", "4"],
                    host=host,
                )
            except Exception:
                pass

            # 2. Hard kill postmaster and lingering walsenders if still running
            pid_file = Path(data_dir) / "postmaster.pid"
            self.executor.run(
                ["pkill", "-9", "-f", f"postgres.*{data_dir}"],
                host=host,
                check=False,
            )
            time.sleep(0.5)
            if pid_file.is_file():
                try:
                    pid_file.unlink()
                except OSError:
                    pass
            return "已强制终止实例: %s" % name

    def start_target(self, target):
        names = self.target_instances(target)
        self._progress("启动目标 %s" % target)
        for name in names:
            if not self._managed(name):
                raise SafetyError("实例不是 pgcluster 创建的，拒绝启动: %s" % name)
            if not self.status_instance(name)["running"]:
                self._progress("启动实例 %s" % name)
                self.start_instance(name)
            else:
                self._progress("实例 %s 已运行，跳过" % name)
        return "已启动: %s" % target

    def stop_target(self, target):
        names = self.target_instances(target)
        self._progress("停止目标 %s" % target)
        for name in names:
            if not self._managed(name):
                raise SafetyError("实例不是 pgcluster 创建的，拒绝停止: %s" % name)
        for name in reversed(names):
            if self.status_instance(name)["running"]:
                self._progress("停止实例 %s" % name)
                self.stop_instance(name)
            else:
                self._progress("实例 %s 已停止，跳过" % name)
        return "已停止: %s" % target

    def restart_target(self, target):
        self._progress("重启目标 %s" % target)
        self.stop_target(target)
        time.sleep(1)
        self.start_target(target)
        return "已重启: %s" % target

    def clean_target(self, target, yes=False):
        if not yes:
            raise SafetyError("clean 会删除 PGDATA；请使用 --yes")
        names = self.target_instances(target)
        self._progress("校验目标 %s 的受管数据目录" % target)
        for name in names:
            if not self._managed(name):
                raise SafetyError("实例不是 pgcluster 创建的，拒绝清理: %s" % name)

        self._progress("删除目标 %s 的复制元数据" % target)
        self._teardown_metadata(target)
        for name in reversed(names):
            if self.status_instance(name)["running"]:
                self._progress("停止实例 %s" % name)
                self.stop_instance(name)
        for name in names:
            instance = self.config.instance(name)
            self._progress("删除实例 %s 的数据目录" % name)
            self.executor.remove_tree(self._host(instance), instance["data_dir"])
        self._progress("清理运行状态记录")
        self._clear_state(names)
        return "已清理: %s" % target

    def _clear_state(self, instances):
        state = self._state()
        changed = False
        for cluster_name, primary in list(state.get("primaries", {}).items()):
            if primary in instances or cluster_name not in self.config.streaming_clusters:
                state["primaries"].pop(cluster_name, None)
                state.get("failovers", {}).pop(cluster_name, None)
                changed = True
        if changed:
            self._save_state(state)

    def _teardown_metadata(self, target):
        if "." not in target:
            return
        kind, name = target.split(".", 1)
        streams = []
        if kind == "logical":
            self._drop_logical(name)
            link = self.config.logical_replications[name]
            streams = [link["pub"]["streaming_cluster"], link["sub"]["streaming_cluster"]]
        elif kind == "citus":
            self._drop_citus(name)
            cluster = self.config.citus_clusters[name]
            streams = [cluster["coordinator"]["streaming_cluster"]]
            streams.extend(item["streaming_cluster"] for item in cluster["workers"].values())
        elif kind == "mmr":
            self._drop_mmr(name)
            streams = [item["streaming_cluster"] for item in self.config.mmr_clusters[name]["members"].values()]
        elif kind == "streaming":
            streams = [name]
        for streaming in streams:
            self._drop_physical_slots(streaming)

    def verify_target(self, target):
        states = self.status_target(target)
        if target.startswith("streaming.") and target.split(".", 1)[1] in self._state().get("primaries", {}):
            effective = self._primary(target.split(".", 1)[1])
            if not states.get(effective, {}).get("running"):
                raise OperationError("切换后的主库未运行: %s" % effective)
        elif not all(item["running"] for item in states.values()):
            raise OperationError("存在未运行实例: %s" % target)
        if "." not in target:
            return "验证通过: %s" % target
        kind, name = target.split(".", 1)
        if kind == "streaming":
            primary = self._primary(name)
            expected = len(self._streaming_standbys(name))
            failover_info = self._state().get("failovers", {}).get(name, {})
            if failover_info and not failover_info.get("rejoined", False) and expected:
                # After a promotion the former primary is intentionally
                # stopped until `rejoin`; verify the promoted role itself.
                expected = 0
            actual = int(self._psql(primary, "SELECT count(*) FROM pg_stat_replication WHERE state='streaming'", tuples=True))
            if actual < expected:
                raise OperationError("流复制备库未全部 streaming: %s/%s" % (actual, expected))
        elif kind == "logical":
            link = self.config.logical_replications[name]
            sub = self._primary(link["sub"]["streaming_cluster"])
            state = self._psql(sub, "SELECT count(*) FROM pg_stat_subscription WHERE latest_end_lsn IS NOT NULL", link["sub"].get("database", "postgres"), True)
            if state != "1":
                raise OperationError("逻辑订阅尚未收到 WAL: %s" % name)
        elif kind == "citus":
            cluster = self.config.citus_clusters[name]
            coordinator = self._primary(cluster["coordinator"]["streaming_cluster"])
            expected = len(cluster["workers"])
            actual = int(self._psql(coordinator, "SELECT count(*) FROM pg_dist_node WHERE groupid > 0 AND isactive", cluster.get("database", "postgres"), True))
            if actual != expected:
                raise OperationError("Citus Worker 不完整: %s/%s" % (actual, expected))
        elif kind == "mmr":
            cluster = self.config.mmr_clusters[name]
            database = cluster.get("database", "postgres")
            expected_members = sorted(m["node_name"] for m in cluster["members"].values())
            for member in cluster["members"].values():
                node = self._primary(member["streaming_cluster"])
                state = self._psql(node, "SELECT count(*) FROM fdd.mmr_node WHERE node_state='ACTIVE'", database, True)
                if int(state) < len(cluster["members"]):
                    raise OperationError("MMR 节点尚未全部 ACTIVE: %s" % name)
                raw_udf = self._psql(node, "SELECT nodename||'|'||nodestate||'|'||real_nodestate||'|'||is_abnormal FROM fdd.show_node_info(true, false) ORDER BY nodename", database, True)
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
                    pass
        return "验证通过: %s" % target

    def health_target(self, target):
        """Return machine-readable health details for status/monitoring callers."""
        result = self.status_target(target)
        if "." in target:
            try:
                self.verify_target(target)
                result["health"] = "ok"
            except OperationError as exc:
                result["health"] = "failed"
                result["reason"] = str(exc)
        else:
            result["health"] = "ok" if all(item["running"] for item in result.values()) else "failed"
        return result

    def lag_target(self, target):
        """Return replication-specific details for an already-created target."""
        if "." not in target:
            raise OperationError("lag 需要集群目标，例如 streaming.basic_cluster")
        kind, name = target.split(".", 1)
        rows = []
        if kind == "streaming":
            cluster = self.config.streaming_clusters.get(name)
            if not cluster:
                raise OperationError("未知目标: %s" % target)
            primary = self._primary(name)
            rows.append({"cluster": target, "role": "primary", "instance": primary})
            sql = ("SELECT application_name||'|'||state||'|'||"
                   "COALESCE(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::text,'-1') "
                   "FROM pg_stat_replication ORDER BY application_name")
            for line in filter(None, self._psql(primary, sql, tuples=True).splitlines()):
                app, state, lag = line.split("|", 2)
                rows.append({"cluster": target, "role": "standby", "application": app, "state": state, "lag_bytes": int(float(lag)) if lag != "-1" else None})
        elif kind == "logical":
            link = self.config.logical_replications.get(name)
            if not link:
                raise OperationError("未知目标: %s" % target)
            sub = self._primary(link["sub"]["streaming_cluster"])
            sql = "SELECT subname||'|'||COALESCE(received_lsn::text,'')||'|'||COALESCE(latest_end_lsn::text,'') FROM pg_stat_subscription"
            for line in filter(None, self._psql(sub, sql, link["sub"].get("database", "postgres"), True).splitlines()):
                subscription, received, latest = line.split("|", 2)
                rows.append({"cluster": target, "subscription": subscription, "received_lsn": received, "latest_end_lsn": latest})
        elif kind == "citus":
            cluster = self.config.citus_clusters.get(name)
            if not cluster:
                raise OperationError("未知目标: %s" % target)
            coordinator = self._primary(cluster["coordinator"]["streaming_cluster"])
            sql = "SELECT nodename||'|'||nodeport||'|'||isactive FROM pg_dist_node WHERE groupid > 0 ORDER BY nodeport"
            for line in filter(None, self._psql(coordinator, sql, cluster.get("database", "postgres"), True).splitlines()):
                host, port, active = line.split("|")
                rows.append({"cluster": target, "worker": "%s:%s" % (host, port), "active": active.lower() in {"t", "true", "1"}})
        elif kind == "mmr":
            cluster = self.config.mmr_clusters.get(name)
            if not cluster:
                raise OperationError("未知目标: %s" % target)
            for member_name, member in cluster["members"].items():
                node = self._primary(member["streaming_cluster"])
                state = self._psql(node, "SELECT node_name||'|'||node_state FROM fdd.mmr_node ORDER BY node_id", cluster.get("database", "postgres"), True)
                rows.append({"cluster": target, "member": member_name, "nodes": state.splitlines()})
        else:
            raise OperationError("未知目标类型: %s" % kind)
        return rows

    def _bin(self, name, command):
        return self.config.instance(name)["installation_config"]["home"] + "/bin/" + command

    def _psql(self, name, sql, database="postgres", tuples=False):
        instance = self.config.instance(name)
        args = [self._bin(name, "psql"), "-X", "-v", "ON_ERROR_STOP=1", "-h",
                instance["host_config"]["address"], "-p", str(instance["port"]), "-U", "postgres", "-d", database]
        if tuples:
            args.append("-At")
        args += ["-c", sql]
        return self.executor.run(args, host=self._host(instance)).stdout.strip()

    def _marker(self, name):
        return str(Path(self.config.instance(name)["data_dir"]) / MARKER)

    def _managed(self, name):
        instance = self.config.instance(name)
        host = self._host(instance)
        if not self.executor.is_nonempty_dir(host, instance["data_dir"]):
            return False
        marker = self._marker(name)
        if not self.executor.exists(host, marker):
            raise SafetyError("拒绝操作非 pgcluster 创建的数据目录: %s" % instance["data_dir"])
        return True
