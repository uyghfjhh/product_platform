import datetime
import json
import os
import re
import shutil
import socket
import uuid
from pathlib import Path

from framework.errors import ConfigError, OperationError, SafetyError
from framework.health import EnvironmentHealthMixin
from framework.process import CommandRunner
from framework.postgres import PostgresClient
from framework.replication import ReplicationSetupMixin
from framework.state import StateStore, require_marker, write_marker
from framework.topology import Topology


LOCAL_HOSTS = {"127.0.0.1", "localhost", "local", "::1"}
GUC_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _sql_literal(value):
    return "'%s'" % str(value).replace("'", "''")


def _guc_value(value):
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        value = ",".join(str(item) for item in value)
    return _sql_literal(value)


class EnvironmentManager(ReplicationSetupMixin, EnvironmentHealthMixin):
    def __init__(self, config, cluster_name):
        self.config = config
        self.root = config.root
        self.cluster_name = cluster_name
        self.cluster = config.cluster(cluster_name)
        self.nodes = self.cluster["nodes"]
        self.groups = self.cluster.get("groups") or {}
        self.plugins = self.cluster.get("plugins") or {}
        self.store = StateStore(self.root, cluster_name)
        self.pg_home = config.postgres_home
        self.bin_dir = self.pg_home / "bin"
        self.log_file = self.store.directory / "environment.log"
        self.runner = CommandRunner(self.log_file)

    def binary(self, name):
        return self.bin_dir / name

    def node(self, name):
        try:
            return self.nodes[name]
        except KeyError:
            raise ConfigError("cluster %s 不存在节点: %s" %
                              (self.cluster_name, name))

    def is_local(self, host):
        return self._is_local(str(host))

    def managed_state(self):
        return self._load_managed_state()

    def lock_identity(self):
        """Return a stable identity for inter-checkout access to this cluster."""
        nodes = []
        for node in self.nodes.values():
            nodes.append({
                "host": str(node["host"]),
                "port": int(node["port"]),
                "data_dir": str(Path(node["data_dir"]).resolve()),
            })
        return json.dumps(sorted(
            nodes, key=lambda node: (node["host"], node["port"], node["data_dir"])),
            ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    def query_value(self, node_name, database, sql):
        return self._query_value(node_name, database, sql)

    def resolve_node(self, selector="primary"):
        return self.topology().resolve(selector)

    def topology(self):
        return Topology(self.cluster_name, self.nodes, self.groups)

    def roles(self):
        return self.topology().roles()

    def physical_relations(self):
        return self.topology().physical_relations()

    def physical_standbys(self):
        return self.topology().physical_standbys()

    def effective_settings(self, node_name):
        return self._effective_settings(node_name)

    def evaluate_requirements(self, requirements, postgres_client=None):
        requirements = requirements or {}
        setting_details = []
        allowed_clusters = requirements.get("clusters") or []
        if allowed_clusters and self.cluster_name not in allowed_clusters:
            return ("用例仅支持 cluster=%s；当前为 %s" %
                    (",".join(allowed_clusters), self.cluster_name), setting_details)
        for command in requirements.get("commands", []):
            if not isinstance(command, str) or not command:
                raise ConfigError("requirements.commands 必须包含非空命令名: %s" % command)
            if not shutil.which(command):
                return ("缺少命令: %s；请安装 util-linux（提供 %s）" %
                        (command, command), setting_details)
        missing_plugins = sorted(
            set(requirements.get("plugins", [])) - set(self.plugins))
        if missing_plugins:
            return ("cluster %s 未启用插件: %s" %
                    (self.cluster_name, ",".join(missing_plugins)), setting_details)
        for group in requirements.get("groups", []):
            if group not in self.groups:
                return ("cluster %s 缺少关系组: %s" %
                        (self.cluster_name, group), setting_details)
        for selector in requirements.get("nodes", []):
            try:
                self.resolve_node(selector)
            except ConfigError as exc:
                return str(exc), setting_details
        node_name = self.resolve_node(requirements.get("node", "primary"))
        if requirements.get("writable_node"):
            _, rows = self.status_rows()
            matched = [row for row in rows if row[0] == node_name]
            if not matched or matched[0][5] != "false" or matched[0][6] != "healthy":
                return ("cluster %s 没有健康的可写节点" % self.cluster_name,
                        setting_details)
        if requirements.get("system_time_control"):
            process = self.runner.run(["sudo", "-n", "true"], check=False)
            if process.returncode != 0:
                return (
                    "密码周期用例需要免交互 sudo 调整并恢复系统时间；"
                    "请安装 sudo 并为当前测试用户配置 sudo -n true",
                    setting_details)
        for role in requirements.get("roles", []):
            try:
                exists = self.query_value(
                    node_name, "postgres",
                    "SELECT count(*) FROM pg_roles WHERE rolname = '%s'" %
                    role.replace("'", "''"),
                )
            except Exception as exc:
                return "无法检查角色 %s: %s" % (role, exc), setting_details
            if exists != "1":
                return "缺少数据库角色: %s" % role, setting_details
        for extension in requirements.get("extensions", []):
            try:
                available = self.query_value(
                    node_name, "postgres",
                    "SELECT count(*) FROM pg_available_extensions WHERE name = %s" %
                    _sql_literal(extension))
            except Exception as exc:
                return "无法检查扩展 %s: %s" % (extension, exc), setting_details
            if available != "1":
                return "缺少可用 PostgreSQL 扩展: %s" % extension, setting_details
        setting_error = ""
        postgres_client = postgres_client or PostgresClient(self, self.runner)
        for spec in requirements.get("settings", []):
            selector = spec.get("node", requirements.get("node", "primary"))
            setting_node = self.resolve_node(selector)
            name = spec["name"]
            if not GUC_NAME.match(name):
                return "非法 PostgreSQL 参数名: %s" % name, setting_details
            try:
                detail = postgres_client.check_setting(setting_node, spec)
            except Exception as exc:
                detail = {
                    "source": "requirement", "node": setting_node,
                    "name": name, "sql": "SHOW %s" % name,
                    "config_sql": "SELECT ... FROM pg_file_settings WHERE name = '%s'" % name,
                    "config_output": "<查询失败>", "config_error": str(exc),
                    "output": "<查询失败>", "actual": "<查询失败>",
                    "requirement": ("等于 %s" % spec["equals"] if "equals" in spec
                                    else "包含 %s" % spec["contains"]),
                    "purpose": spec["purpose"], "matched": False,
                    "error": str(exc),
                }
            setting_details.append(detail)
            if not detail["matched"] and not setting_error:
                if detail.get("error"):
                    setting_error = "无法读取 PostgreSQL 参数 %s: %s" % (
                        name, detail["error"])
                else:
                    setting_error = "PostgreSQL 参数 %s 不满足要求（实际=%s，要求=%s）" % (
                        name, detail["actual"], detail["requirement"])
        return setting_error, setting_details

    def check_requirements(self, requirements):
        return self.evaluate_requirements(requirements)[0]

    def _pg_config_path(self, option):
        process = self.runner.run([self.binary("pg_config"), option], check=False)
        if process.returncode != 0:
            return None
        return Path(process.stdout.strip())

    def doctor(self, target=None):
        checks = []
        checks.append(self._file_check("postgres.home", self.pg_home, directory=True))
        for name in ("pg_config", "initdb", "pg_ctl", "psql", "pg_basebackup", "pg_isready"):
            checks.append(self._file_check("postgres.%s" % name, self.binary(name), executable=True))

        sharedir = None
        pkglibdir = None
        if self.binary("pg_config").is_file():
            sharedir = self._pg_config_path("--sharedir")
            pkglibdir = self._pg_config_path("--pkglibdir")
        for extension in sorted(self.plugins):
            control = sharedir / "extension" / (extension + ".control") if sharedir else Path("-")
            library = pkglibdir / (extension + ".so") if pkglibdir else Path("-")
            checks.append(self._file_check("extension.%s.control" % extension, control))
            checks.append(self._file_check("extension.%s.library" % extension, library))
        if "fdd_mmr" in self.plugins:
            checks.append(self._file_check(
                "fdd_mmr.fdd_mmr_join", self.binary("fdd_mmr_join"), executable=True))
            checks.append(self._file_check(
                "fdd_mmr.fddmmr_init_load", self.binary("fddmmr_init_load"), executable=True))

        checks.append(self._file_check("postgres.license_file", self.config.license_file))
        postgresql = self.cluster.get("postgresql") or {}
        checks.append(self._file_check(
            "template.postgresql",
            self.config.template_path(postgresql.get("config_template", "templates/postgresql.conf")),
        ))
        checks.append(self._file_check(
            "template.hba",
            self.config.template_path(postgresql.get("hba_template", "templates/pg_hba.conf")),
        ))

        for node_name, node in self.nodes.items():
            host = str(node["host"])
            if not self._is_local(host):
                checks.append(("BLOCKED", "node.%s.transport" % node_name,
                               "%s: 第一阶段暂未实现远程节点 setup" % host))
            else:
                checks.append(("PASS", "node.%s.transport" % node_name, "local"))
            checks.append(self._directory_parent_check(node_name, Path(node["data_dir"])))

        mac = self.plugins.get("fbase_mac") or {}
        target = target or ""
        if ".tde" in target or target.endswith("tde"):
            command = mac.get("tde_key_command", "")
            if command and os.access(command, os.X_OK):
                checks.append(("PASS", "fbase_mac.tde_key_command", command))
            else:
                checks.append(("BLOCKED", "fbase_mac.tde_key_command",
                               command or "未配置"))
        if ".tlcp" in target or target.endswith("tlcp"):
            cert_dir = mac.get("tlcp_cert_dir", "")
            if cert_dir and Path(cert_dir).is_dir():
                checks.append(("PASS", "fbase_mac.tlcp_cert_dir", cert_dir))
            else:
                checks.append(("BLOCKED", "fbase_mac.tlcp_cert_dir", cert_dir or "未配置"))
        return checks

    @staticmethod
    def _file_check(name, path, directory=False, executable=False):
        path = Path(path)
        valid = path.is_dir() if directory else path.is_file()
        if valid and executable:
            valid = os.access(str(path), os.X_OK)
        return ("PASS" if valid else "BLOCKED", name, str(path))

    @staticmethod
    def _directory_parent_check(node_name, data_dir):
        parent = data_dir.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        valid = parent.is_dir() and os.access(str(parent), os.W_OK | os.X_OK)
        return ("PASS" if valid else "BLOCKED",
                "node.%s.data_parent" % node_name, str(parent))

    @staticmethod
    def _is_local(host):
        if host in LOCAL_HOSTS:
            return True
        try:
            local_addresses = {item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None)}
            target_addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
            return bool(local_addresses & target_addresses)
        except socket.gaierror:
            return False

    def _require_setup_preconditions(self):
        blocked = [item for item in self.doctor() if item[0] != "PASS"]
        if blocked:
            lines = ["%s | %s | %s" % item for item in blocked]
            raise OperationError("环境前置检查未通过:\n" + "\n".join(lines))
        state = self.store.load()
        if state.get("state") != "-":
            raise SafetyError("cluster %s 已创建（state=%s），请先 env clean" %
                              (self.cluster_name, state.get("state")))
        for node_name, node in self.nodes.items():
            data_dir = Path(node["data_dir"])
            if data_dir.exists():
                raise SafetyError("拒绝覆盖已有数据库目录: %s (%s)" % (data_dir, node_name))
            if self._port_open(str(node["host"]), int(node["port"])):
                raise OperationError("端口已被占用: %s:%s" % (node["host"], node["port"]))

    @staticmethod
    def _port_open(host, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        try:
            return sock.connect_ex((host, port)) == 0
        finally:
            sock.close()

    def setup(self):
        self._require_setup_preconditions()
        env_id = "env_%s_%s" % (
            datetime.datetime.now().strftime("%Y%m%d_%H%M%S"),
            uuid.uuid4().hex[:6],
        )
        state = {
            "cluster": self.cluster_name,
            "env_id": env_id,
            "state": "partial",
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "nodes": self._state_nodes(),
        }
        self.store.save(state)
        try:
            standby_names = self.physical_standbys()
            seed_names = [name for name in self.nodes if name not in standby_names]
            for name in seed_names:
                print("[setup] initdb %s" % name, flush=True)
                self._init_seed(name, env_id)
            for name in seed_names:
                print("[setup] start %s" % name, flush=True)
                self._pg_ctl(name, "start")
                self._wait_ready(name)
                self._create_extensions(name)
            self._setup_physical_replication(env_id)
            self._setup_mmr()
            self._setup_logical()
            state["state"] = "running"
            self.store.save(state)
            print("env setup complete: %s (%s)" % (self.cluster_name, env_id))
        except Exception:
            state["state"] = "partial"
            self.store.save(state)
            raise

    def _state_nodes(self):
        roles = self.roles()
        result = {}
        for name, node in self.nodes.items():
            result[name] = {
                "host": str(node["host"]),
                "port": int(node["port"]),
                "data_dir": str(node["data_dir"]),
                "role": roles.get(name, "standalone"),
            }
        return result

    def _init_seed(self, node_name, env_id):
        node = self.nodes[node_name]
        data_dir = Path(node["data_dir"])
        data_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.runner.run([
                self.binary("initdb"), "-D", data_dir, "-U", "postgres",
                "--auth-local=trust", "--auth-host=trust",
            ])
        except Exception:
            # setup 前已确认路径不存在，因此可将 initdb 留下的目录纳入平台管理。
            if data_dir.exists():
                write_marker(data_dir, self.cluster_name, node_name, env_id)
            raise
        write_marker(data_dir, self.cluster_name, node_name, env_id)
        self._install_node_files(node_name)

    def _install_node_files(self, node_name):
        node = self.nodes[node_name]
        data_dir = Path(node["data_dir"])
        shutil.copy2(str(self.config.license_file), str(data_dir / "license.dat"))
        postgresql = self.cluster.get("postgresql") or {}
        hba_template = self.config.template_path(
            postgresql.get("hba_template", "templates/pg_hba.conf"))
        shutil.copy2(str(hba_template), str(data_dir / "pg_hba.conf"))
        postgresql_conf = data_dir / "postgresql.conf"
        include_line = "include = 'fbase_regress.conf'"
        content = postgresql_conf.read_text(encoding="utf-8")
        if include_line not in content:
            with postgresql_conf.open("a", encoding="utf-8") as stream:
                stream.write("\n# fbase_regress_v2\n%s\n" % include_line)
        self._write_generated_config(node_name)

    def _write_generated_config(self, node_name):
        node = self.nodes[node_name]
        data_dir = Path(node["data_dir"])
        postgresql = self.cluster.get("postgresql") or {}
        template = self.config.template_path(
            postgresql.get("config_template", "templates/postgresql.conf"))
        settings = self._effective_settings(node_name)
        lines = [template.read_text(encoding="utf-8").rstrip(), "", "# Generated settings"]
        for key in sorted(settings):
            lines.append("%s = %s" % (key, _guc_value(settings[key])))
        (data_dir / "fbase_regress.conf").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _effective_settings(self, node_name):
        node = self.nodes[node_name]
        settings = {}
        settings.update((self.cluster.get("postgresql") or {}).get("settings") or {})
        settings.update((node.get("postgresql") or {}).get("settings") or {})
        settings["port"] = int(node["port"])
        settings["listen_addresses"] = str(node["host"])
        settings["shared_preload_libraries"] = [
            plugin for plugin in sorted(self.plugins)
            if (self.plugins.get(plugin) or {}).get("preload", True)
        ]
        if "streaming" in self.groups or "logical" in self.groups or "mmr" in self.groups:
            settings["max_wal_senders"] = max(int(settings.get("max_wal_senders", 0)), 10)
            settings["max_replication_slots"] = max(int(settings.get("max_replication_slots", 0)), 10)
            settings["hot_standby"] = True
        if "logical" in self.groups or "mmr" in self.groups:
            settings["wal_level"] = "logical"
            settings["max_logical_replication_workers"] = max(
                int(settings.get("max_logical_replication_workers", 0)), 4)
            settings["max_worker_processes"] = max(int(settings.get("max_worker_processes", 0)), 8)
        elif "streaming" in self.groups:
            settings["wal_level"] = "replica"
        if "fdd_mmr" in self.plugins:
            settings["track_commit_timestamp"] = True
            settings["fdd.running_databases"] = self.plugins["fdd_mmr"].get(
                "enabled_databases", ["postgres"])
        return settings

    def _pg_ctl(self, node_name, action, check=True):
        node = self.nodes[node_name]
        data_dir = Path(node["data_dir"])
        argv = [self.binary("pg_ctl"), "-D", data_dir]
        if action == "start":
            argv += ["-l", data_dir / "startup.log", "-w", "start"]
        elif action == "stop":
            argv += ["-w", "-m", "fast", "stop"]
        elif action == "stop_immediate":
            argv += ["-w", "-m", "immediate", "stop"]
        elif action == "restart":
            argv += ["-l", data_dir / "startup.log", "-w", "restart"]
        elif action == "reload":
            argv += ["reload"]
        elif action == "status":
            argv += ["status"]
        else:
            raise ValueError("unknown pg_ctl action: %s" % action)
        return self.runner.run(argv, check=check)

    def _wait_ready(self, node_name):
        node = self.nodes[node_name]
        self.runner.run([
            self.binary("pg_isready"), "-h", node["host"], "-p", node["port"],
            "-d", "postgres", "-U", "postgres", "-t", "30",
        ])

    def _psql(self, node_name, database, sql):
        node = self.nodes[node_name]
        return self.runner.run([
            self.binary("psql"), "-X", "-v", "ON_ERROR_STOP=1", "-h", node["host"],
            "-p", node["port"], "-U", "postgres", "-d", database, "-c", sql,
        ])

    def _create_extensions(self, node_name):
        databases = {"postgres"}
        mmr = self.plugins.get("fdd_mmr") or {}
        databases.update(mmr.get("enabled_databases") or [])
        for database in sorted(databases):
            for extension in sorted(self.plugins):
                self._psql(node_name, database,
                           "CREATE EXTENSION IF NOT EXISTS %s" % extension)

    def _query_value(self, node_name, database, sql, timeout=5):
        """Read one value without allowing a health probe to block a run forever."""
        node = self.nodes[node_name]
        process = self.runner.run([
            self.binary("psql"), "-X", "-v", "ON_ERROR_STOP=1", "-A", "-t",
            "-h", node["host"], "-p", node["port"], "-U", "postgres",
            "-d", database, "-c", sql,
        ], timeout=timeout)
        lines = process.stdout.strip().splitlines()
        return lines[-1].strip() if lines else ""

    def _load_managed_state(self):
        state = self.store.load()
        if state.get("state") == "-":
            raise OperationError("cluster %s 尚未 setup" % self.cluster_name)
        env_id = state.get("env_id")
        for node_name, node in state.get("nodes", {}).items():
            data_dir = Path(node["data_dir"])
            if data_dir.exists():
                require_marker(data_dir, self.cluster_name, node_name, env_id)
        return state

    def start(self):
        state = self._load_managed_state()
        for name in self.nodes:
            result = self._pg_ctl(name, "status", check=False)
            if result.returncode != 0:
                print("[start] %s" % name, flush=True)
                self._pg_ctl(name, "start")
        state["state"] = "running"
        self.store.save(state)
        print("env start complete: %s" % self.cluster_name)

    def stop(self):
        state = self._load_managed_state()
        for name in reversed(list(self.nodes)):
            result = self._pg_ctl(name, "status", check=False)
            if result.returncode == 0:
                print("[stop] %s" % name, flush=True)
                self._pg_ctl(name, "stop")
        state["state"] = "stopped"
        self.store.save(state)
        print("env stop complete: %s" % self.cluster_name)

    def restart(self, quiet=False):
        state = self._load_managed_state()
        for name in self.nodes:
            if not quiet:
                print("[restart] %s" % name, flush=True)
            result = self._pg_ctl(name, "status", check=False)
            self._pg_ctl(name, "restart" if result.returncode == 0 else "start")
        state["state"] = "running"
        self.store.save(state)
        if not quiet:
            print("env restart complete: %s" % self.cluster_name)

    def reload(self, quiet=False):
        state = self._load_managed_state()
        if state.get("state") != "running":
            raise OperationError("cluster %s 未运行，不能 reload" % self.cluster_name)
        for name in self.nodes:
            self._write_generated_config(name)
            if not quiet:
                print("[reload] %s" % name, flush=True)
            self._pg_ctl(name, "reload")
        if not quiet:
            print("env reload complete: %s" % self.cluster_name)

    def clean(self):
        state = self._load_managed_state()
        env_id = state["env_id"]
        existing = []
        for name, node in self.nodes.items():
            data_dir = Path(node["data_dir"])
            if data_dir.exists():
                require_marker(data_dir, self.cluster_name, name, env_id)
                existing.append((name, data_dir))
        for name, data_dir in reversed(existing):
            result = self._pg_ctl(name, "status", check=False)
            if result.returncode == 0:
                self._pg_ctl(name, "stop")
            print("[clean] %s" % data_dir, flush=True)
            shutil.rmtree(str(data_dir))
        self.store.remove()
        print("env clean complete: %s" % self.cluster_name)


def format_doctor(checks):
    return "\n".join("%-7s | %-36s | %s" % item for item in checks)
