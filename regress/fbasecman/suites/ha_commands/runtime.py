"""Runtime and report support for HA console command cases."""

import json
import difflib
import shlex
import os
import shutil
import socket
import time
import re
from datetime import datetime
from pathlib import Path

import yaml

from framework.clients.psql import build_psql_command
from framework.configuration import load_regression_config
from framework.evidence import EvidenceStep, StepJournal
from framework.execution.command import run_logged_command
from framework.execution.locking import ExclusiveFileLock
from framework.reporting import ReportCheck, ReportDocument, ReportStep, render_report
from products.fbasecman.process import FbasecmanProcess, FbasecmanProcessError


class HaCommandFailure(RuntimeError):
    pass


class BackupCheckpoint(object):
    def __init__(self, files, config_content, backup_dir=None):
        self.files = frozenset(files)
        self.config_content = config_content
        self.backup_dir = backup_dir


def _port_free(port):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except PermissionError:
        # The hosted runner may prohibit even probe socket creation.  The
        # product startup remains authoritative for actual bind conflicts.
        return True
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", int(port)))
        return True
    except PermissionError:
        # The hosted runner may forbid a probe bind while still allowing the
        # product process to bind its configured listener.
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _non_ephemeral_port_range():
    low, high = 32768, 60999
    try:
        values = Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()
        low, high = int(values[0]), int(values[1])
    except (OSError, ValueError, IndexError):
        pass
    ranges = [(1024, low - 1), (high + 1, 65532)]
    usable = [(start, end) for start, end in ranges if end - start >= 2]
    if not usable:
        raise HaCommandFailure("no non-ephemeral TCP port range available")
    return max(usable, key=lambda item: item[1] - item[0])


def _port_pair(seed):
    range_start, range_end = _non_ephemeral_port_range()
    pair_count = (range_end - range_start - 1) // 2
    start_index = (os.getpid() * 17 + seed) % pair_count
    for offset in range(pair_count):
        listen = range_start + 2 * ((start_index + offset) % pair_count)
        # fbasecman binds the console listener, read listener and
        # prometheus endpoint (listen + 2); reserve all three as a unit.
        if _port_free(listen) and _port_free(listen + 1) and _port_free(listen + 2):
            return listen, listen + 1
    raise HaCommandFailure("no free port pair for HA command case")


def _json_write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class HaCommandRuntime(object):
    def __init__(self, root, case):
        self.root = Path(root)
        self.case = case
        self.env = load_regression_config(self.root)
        if not self.env.test_context_file.exists():
            raise HaCommandFailure("missing %s, run ./run.sh env setup first" % self.env.test_context_file)
        self.context = yaml.safe_load(self.env.test_context_file.read_text(encoding="utf-8")) or {}
        suite_name = getattr(case, "suite_name", "ha_commands")
        self.run_root = self.env.output_dir / "runs" / suite_name / case.name
        if self.run_root.exists():
            shutil.rmtree(str(self.run_root))
        self.workdir = self.run_root / "workdir"
        self.logs_dir = self.run_root / "logs"
        self.workdir.mkdir(parents=True)
        self.logs_dir.mkdir(parents=True)
        self.started_at = datetime.now()
        self.finished_at = None
        self._step_order = 0
        self.steps = []
        self.step_journal = StepJournal(self.run_root / "steps.json", case.target)
        self.proxy_log = self.run_root / "fbasecman.log"
        self._port_seed = 1
        self.listen_port, self.read_port = _port_pair(1)
        self.pid_file = self.workdir / "fbasecman.pid"
        self.active_conf = None
        # Every HA console mutation gets a before/after console snapshot.
        self.process = FbasecmanProcess(
            self.env.config["fbasecman"]["fbasecman_bin"],
            self.env.config["local"]["postgres_dir"], self.listen_port,
            self.listen_port + 2, self.pid_file, self.workdir / "locks",
            self.proxy_log, self.logs_dir, self.run_command, self.trace,
            _port_free,
        )

    def trace(self, message):
        with (self.run_root / "events.log").open("a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (datetime.now().strftime("%H:%M:%S"), message))

    def _next_order(self):
        self._step_order += 1
        return self._step_order

    def run_command(self, command, logfile, cwd=None, env=None, echo=False,
                    check=True, step_title=None, record=True):
        result = run_logged_command(command, logfile, cwd=cwd or self.root,
                                    env=env, echo=echo)
        if record:
            self.record_step(step_title or "执行命令", result.command,
                             "命令执行完成", result.output,
                             "PASS" if result.returncode == 0 else "FAIL")
        if check and result.returncode != 0:
            raise HaCommandFailure("command failed rc=%s: %s" % (result.returncode, result.command))
        return result.returncode, result.output

    def postgres_node_action(self, pgdata, action, title):
        """Stop/start one remote PostgreSQL fixture node for monitor probes."""
        if action not in ("start", "stop"):
            raise ValueError("unsupported postgres node action: %s" % action)
        db = self.env.config["database"]
        pgctl = "%s/bin/pg_ctl" % db["mmr_postgres_dir"]
        data = shlex.quote(str(pgdata))
        logfile = shlex.quote(str(pgdata) + "/logfile")
        if action == "stop":
            script = ("%s -D %s status >/dev/null 2>&1 && "
                      "%s -D %s stop -m immediate -w && "
                      "! %s -D %s status >/dev/null 2>&1" %
                      (pgctl, data, pgctl, data, pgctl, data))
        else:
            script = ("%s -D %s status >/dev/null 2>&1 || "
                      "%s -D %s start -l %s -w; "
                      "%s -D %s status >/dev/null 2>&1" %
                      (pgctl, data, pgctl, data, logfile, pgctl, data))
        command = ["ssh", "-F", "/dev/null",
                   "%s@%s" % (db["mmr_pg_user"], db["mmr_host"]), script]
        return self.run_command(command, self.logs_dir / ("node_%s.log" % action),
                                cwd=self.workdir, step_title=title)

    def record_step(self, title, command=None, expected=None, actual=None,
                    result=None, details=None):
        self.steps.append({
            "title": title, "command": command, "expected": expected,
            "actual": actual, "result": result, "details": details or [],
            "order": self._next_order(),
        })
        self.write_report("RUNNING")

    def evidence_step(self, title, expected=None):
        order = self._next_order()
        return EvidenceStep(
            title, self.step_journal, expected=expected,
            metadata={"order": order, "console": True},
            on_change=lambda: self.write_report("RUNNING"),
        )

    def check(self, title, expected, actual, passed):
        self.record_step(title, "", expected, actual,
                         "PASS" if passed else "FAIL")
        if not passed:
            raise HaCommandFailure("%s: expected %s, actual %s" % (title, expected, actual))

    def _context_value(self, key, default=""):
        value = self.context.get(key, default)
        return value if isinstance(value, str) else default

    def _query_scalar(self, port, sql, log_name):
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"],
            self.env.config["database"]["mmr_host"], port,
            self.env.config["database"]["mmr_pg_user"], "postgres", sql,
            footer=False, output_format="unaligned", tuples_only=True,
        )
        result = run_logged_command(command, self.logs_dir / log_name, cwd=self.workdir)
        value = result.output.strip()
        if result.returncode != 0 or not value:
            raise HaCommandFailure("metadata query failed: %s\n%s" % (result.command, result.output))
        return value.splitlines()[-1].strip()

    def _live_metadata(self):
        """Read topology identities from PostgreSQL instead of stale context data."""
        ports = self.env.config["database"]["ports"]
        system_identifiers = {}
        for name, port in (("pg_1", ports["mmr1"]), ("pg_2", ports["mmr2"]),
                           ("pg_3", ports["mmr1_standby1"]),
                           ("pg_4", ports["mmr2_standby1"])):
            system_identifiers[name] = self._query_scalar(
                port, "SELECT system_identifier FROM pg_control_system();",
                "metadata_%s_system_identifier.log" % name,
            )
        group = self._query_scalar(
            ports["mmr1"],
            "SELECT group_name || '|' || group_uuid FROM fdd.mmr_group ORDER BY group_name LIMIT 1;",
            "metadata_mmr_group.log",
        )
        fields = group.split("|", 1)
        if len(fields) != 2 or not all(fields):
            raise HaCommandFailure("invalid MMR metadata response: %s" % group)
        return system_identifiers, fields[0], fields[1]

    def render_conf(self, transform=None):
        db = self.env.config["database"]
        ports = db["ports"]
        sysids, real_group, group_uuid = self._live_metadata()
        lines = [
            'pid_file "%s"' % self.pid_file,
            "daemonize yes", 'unix_socket_dir "/tmp"', 'unix_socket_mode "0644"',
            'locks_dir "%s"' % (self.workdir / "locks"),
            'license_dir "%s"' % self.env.config["fbasecman"]["license_dir"],
            'priority 0', 'log_to_stdout no',
            'log_syslog no', 'log_format "%p %t %l [%i %s] (%c) %m\\n"',
            'log_syslog_ident "fbasecman"', 'log_syslog_facility "daemon"',
            'enable_guc_sync yes',
            'log_debug yes', 'log_config yes', 'log_session yes', 'log_query no',
            'log_stats yes', 'stats_interval 60', 'log_file "%s"' % self.proxy_log,
            'log_min_messages "info"', 'promhttp_server_port %s' % (self.listen_port + 2),
            # Match new-config/fbasecman-all-new.conf.  Keep this value at the
            # template setting so the regression suite exercises the shipped
            # configuration rather than an ASAN-specific variant.
            'server_login_retry 5', 'cache_msg_gc_size 0',
            'cache_coroutine 108', 'coroutine_stack_size 16',
            'workers 8', 'resolvers 1',
            'readahead 8192', 'nodelay yes',
            'log_general_stats_prom no', 'log_route_stats_prom no',
            'graceful_die_on_errors yes', 'enable_online_restart no',
            'bindwith_reuseport yes', 'keepalive 15',
            'keepalive_keep_interval 75', 'keepalive_probes 9',
            'keepalive_usr_timeout 0',
            'host "*"', 'ports "%s"' % self.listen_port, 'backlog 128',
            'compression yes', 'tls "disable"',
            'heartbeat_request "select 1"', 'admin_database "console"',
            # HA 用例显式固定 monitor 状态机参数，避免依赖产品默认值变化。
            'monitor_enabled yes', 'monitor_period 10',
            'monitor_recovery_period 10', 'monitor_retry_period_ms 1000',
            'monitor_max_retries 3', 'monitor_recovery_max_retries 3',
            'monitor_timeout 5',
            '', 'group "mmr_group" {', '    group_mode "mmr"',
            '    storage_db "postgres"', '    backend_clusters "pg_cluster_1,pg_cluster_2"',
            '    write_cluster "pg_cluster_2"', '    promoted_cluster "pg_cluster_1"',
            '    real_group_name "%s"' % real_group,
            '    group_uuid "%s"' % group_uuid, '    check "auto"', '}', '',
            'group "rep_group" {', '    group_mode "replication"',
            '    storage_db "postgres"', '    backend_clusters "pg_cluster_1"',
            '    check "auto"', '}', '',
            'group "balance_group" {', '    group_mode "balance"',
            '    storage_db "postgres"', '    access_mode "read_write"',
            '    backend_clusters "pg_cluster_1,pg_cluster_2"',
            '    check "auto"', '}', '',
            'group "single_group" {', '    group_mode "single"',
            '    storage_db "postgres"', '    access_mode "read_write"',
            '    backend_clusters "pg_cluster_1"', '    check "auto"', '}', '',
        ]
        datasource = [
            ("pg_1", ports["mmr1"], "pg_cluster_1", ""),
            ("pg_2", ports["mmr2"], "pg_cluster_2", ""),
            # These are the application_name values written into the
            # regression fixture's standby primary_conninfo by env/mmr.py.
            # They are distinct from the fbasecman datasource aliases.
            ("pg_3", ports["mmr1_standby1"], "pg_cluster_1", "pg_240"),
            ("pg_4", ports["mmr2_standby1"], "pg_cluster_2", "pg_250"),
        ]
        self.datasource_metadata = [
            {
                "name": name,
                "host": db["mmr_host"],
                "port": port,
                "cluster": cluster,
                "application_name": application_name or "(default)",
                "system_identifier": sysids[name],
                "status": "active",
            }
            for name, port, cluster, application_name in datasource
        ]
        for name, port, cluster, application_name in datasource:
            lines.extend([
                'datasources "%s" {' % name,
                '    host "%s"' % db["mmr_host"], '    port %s' % port,
                '    cluster_name "%s"' % cluster, '    weight 10',
                '    status "active"',
                ('    application_name "%s"' % application_name) if application_name else "",
                '    system_identifier "%s"' % sysids[name],
                '    tls "disable"', '}', '',
            ])
        lines.extend([
            'user "postgres" {',
            '    group_names "mmr_group,rep_group,balance_group,single_group"',
            '    authentication "none"', '    storage_user "postgres"',
            '    pool "transaction"', '    pool_size 20', '    pool_discard no',
            '    rw_split_method "none"', '}', '',
            'user "admin" {', '    authentication "none"', '    pool "session"',
            '    role "admin"', '}', '',
        ])
        path = self.workdir / (self.case.name + ".conf")
        content = "\n".join(line for line in lines if line != "") + "\n"
        if transform is not None:
            content = transform(content)
        path.write_text(content, encoding="utf-8")
        return path

    def start(self, transform=None, env=None):
        for attempt in range(3):
            conf = self.render_conf(transform=transform)
            ports = (self.listen_port, self.read_port, self.listen_port + 2)
            availability = tuple(_port_free(port) for port in ports)
            if all(availability):
                break
            self.trace("[retry] rendered listener ports became busy: %s" %
                       ",".join(str(port) for port, free in zip(ports, availability) if not free))
            self._port_seed += 1
            self.listen_port, self.read_port = _port_pair(self._port_seed)
            self.process.listen_port = self.listen_port
            self.process.prom_port = self.listen_port + 2
        else:
            raise HaCommandFailure("listener ports became busy while rendering HA configuration")
        self.process.start(conf, ready_timeout=90.0, record=False, env=env)
        self.active_conf = conf
        self.record_step(
            "启动 fbasecman", "%s %s" % (self.process.binary, conf),
            "console 可连接且目标 group (%s) 配置加载成功" %
            ", ".join(self.case.report_groups),
            "console ready",
            "PASS", [("监听端口", str(self.listen_port)), ("配置文件", str(conf))],
        )
        return conf

    def start_rejected(self, transform, title, expected, predicate):
        conf = self.render_conf(transform=transform)
        try:
            self.process.start(conf, ready_timeout=3.0, record=False)
        except FbasecmanProcessError as exc:
            actual = str(exc)
            passed = predicate(actual)
            self.record_step(title, "%s %s" % (self.process.binary, conf),
                             expected, actual, "PASS" if passed else "FAIL",
                             [("配置文件", str(conf))])
            if not passed:
                raise HaCommandFailure("%s: unexpected startup error: %s" %
                                       (title, actual))
            return conf
        self.record_step(title, "%s %s" % (self.process.binary, conf),
                         expected, "fbasecman unexpectedly started", "FAIL",
                         [("配置文件", str(conf))])
        raise HaCommandFailure("%s: fbasecman unexpectedly started" % title)

    def _ha_state_sql(self, sql):
        upper = sql.upper()
        if upper.startswith("SET CLUSTER") or " PARTED" in upper or " ACTIVE" in upper:
            return "SHOW NODE_STATUS;"
        if "WEIGHT" in upper:
            return "SHOW NODES;"
        if "WRITE" in upper or "PROMOTED" in upper or "GROUP" in upper:
            match = re.search(r'(?i)\bIN\s+GROUPS?\s*\(?\s*([A-Za-z_][A-Za-z0-9_]*)', sql)
            return "SHOW GROUP_ROUTING %s;" % (match.group(1) if match else "mmr_group")
        return "SHOW NODES;"

    def _record_ha_state(self, sql, phase):
        state_sql = self._ha_state_sql(sql)
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1",
            self.listen_port, "admin", "console", state_sql,
        )
        result = run_logged_command(
            command, self.logs_dir / ("ha_state_%02d.log" % self._next_order()),
            cwd=self.workdir,
        )
        output = result.output.rstrip() or "<empty>"
        self.record_step(
            "%s：%s" % (phase, state_sql),
            "$ %s" % result.command, "console 状态可读取", output,
            "PASS" if result.returncode == 0 else "FAIL",
        )
        if result.returncode != 0:
            raise HaCommandFailure("HA state query failed: %s" % state_sql)
        return output

    @staticmethod
    def _strip_inline_comment(value):
        quoted = False
        escaped = False
        for index, char in enumerate(value):
            if escaped:
                escaped = False
                continue
            if char == "\\" and quoted:
                escaped = True
                continue
            if char == '"':
                quoted = not quoted
            elif char == "#" and not quoted:
                return value[:index].rstrip()
        return value.strip()

    @staticmethod
    def _semantic_objects(text):
        objects = {}
        order = []
        pattern = re.compile(
            r'(?ms)^\s*(group|datasources)\s+"([^"]+)"\s*\{(.*?)\}')
        for match in pattern.finditer(text):
            kind, name, body = match.groups()
            identity = (kind, name)
            if identity in objects:
                raise ValueError("duplicate object %s %s" % identity)
            fields = {}
            token_text = " ".join(
                HaCommandRuntime._strip_inline_comment(raw).strip()
                for raw in body.splitlines()
                if HaCommandRuntime._strip_inline_comment(raw).strip()
            )
            field_pattern = re.compile(
                r'([A-Za-z_][A-Za-z0-9_]*)\s+("(?:\\.|[^"\\])*"|[^\s]+)')
            for field_match in field_pattern.finditer(token_text):
                key, value = field_match.groups()
                if key in fields:
                    raise ValueError("duplicate field %s.%s" % (name, key))
                fields[key] = value
            objects[identity] = fields
            order.append(identity)
        return objects, order

    @staticmethod
    def _command_scope(sql, before_objects):
        upper = sql.upper()
        allowed = {}
        if " WRITE " in upper or " PROMOTED " in upper:
            names = []
            groups = re.search(r'(?i)\bIN\s+GROUPS\s*\(([^)]*)\)', sql)
            group = re.search(r'(?i)\bIN\s+GROUP\s+([^\s;]+)', sql)
            if groups:
                names = [item.strip() for item in groups.group(1).split(",")]
            elif group:
                names = [group.group(1)]
            else:
                names = [name for kind, name in before_objects if kind == "group"]
            fields = {"promoted_cluster"}
            if " WRITE " in upper:
                fields.add("write_cluster")
            target = re.search(
                r'(?i)^SET\s+(?:NODE|CLUSTER)\s+(?:WRITE|PROMOTED)\s+([^\s]+)',
                sql)
            datasource = target.group(1).strip(";,()") if target else ""
            cluster = datasource
            if re.match(r'(?i)^SET\s+NODE\s+', sql):
                cluster = next(
                    (v.get("cluster_name", "").strip('"')
                     for (k, n), v in before_objects.items()
                     if k == "datasources" and n == datasource),
                    datasource)
            for name in names:
                for field in fields:
                    if field == "promoted_cluster" and " WRITE " in upper:
                        prior = before_objects.get(("group", name), {}).get("write_cluster")
                        allowed[("group", name, field)] = prior
                    else:
                        allowed[("group", name, field)] = '"%s"' % cluster
        elif upper.startswith("SET CLUSTER "):
            match = re.search(r'(?i)^SET\s+CLUSTER\s+(?:ACTIVE|PARTED)\s+([^\s;]+)', sql)
            cluster = match.group(1) if match else ""
            value = '"active"' if " ACTIVE " in upper else '"parted"'
            allowed = {
                (kind, name, "status"): value for (kind, name), fields in before_objects.items()
                if kind == "datasources" and fields.get("cluster_name", "").strip('"') == cluster
            }
        else:
            field = "weight" if " WEIGHT " in upper else "status"
            targets = set(re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b', sql))
            assignments = dict(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\d+)', sql))
            allowed = {
                (kind, name, field): assignments.get(name) for kind, name in before_objects
                if kind == "datasources" and name in targets
            }
            # Resolve host:port targets against the configuration snapshot.
            for (kind, name), fields in before_objects.items():
                endpoint = "%s:%s" % (fields.get("host", "").strip('"'), fields.get("port", ""))
                if kind == "datasources" and endpoint in sql:
                    value = assignments.get(name)
                    allowed[(kind, name, field)] = value
        return allowed

    def _semantic_config_diff(self, before, after, sql):
        try:
            old, old_order = self._semantic_objects(before)
            new, new_order = self._semantic_objects(after)
        except ValueError as exc:
            return False, "配置结构错误: %s" % exc
        lines = []
        valid = old_order == new_order and set(old) == set(new)
        if old_order != new_order:
            lines.append("对象顺序发生变化")
        allowed = self._command_scope(sql, old)
        for identity in sorted(set(old) | set(new)):
            old_fields, new_fields = old.get(identity, {}), new.get(identity, {})
            for field in sorted(set(old_fields) | set(new_fields)):
                old_value, new_value = old_fields.get(field), new_fields.get(field)
                if old_value == new_value:
                    continue
                change = (identity[0], identity[1], field)
                expected = change in allowed and (allowed[change] is None or allowed[change] == new_value)
                lines.append("%s %s.%s: %s -> %s%s" % (
                    identity[0], identity[1], field,
                    old_value, new_value, "" if expected else " [非预期]"))
                valid = valid and expected
        return valid, "\n".join(lines) or "<no semantic changes>"

    def psql(self, sql, title, expected, predicate):
        self.last_ha_sql = sql.strip()
        config_before = self.active_conf.read_text(encoding="utf-8") if self.active_conf else None
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1",
            self.listen_port, "admin", "console", sql,
        )
        with self.evidence_step(title, expected=expected) as step:
            retry_enabled = sql.lstrip().upper().startswith("SHOW ")
            deadline = time.time() + (30 if retry_enabled else 0)
            started = time.time()
            order = self._next_order()
            attempt = 0
            while True:
                attempt += 1
                result = run_logged_command(
                    command,
                    self.logs_dir / ("psql_%02d_%02d.log" % (order, attempt)),
                    cwd=self.workdir,
                )
                output = result.output.rstrip() or "<empty>"
                passed = result.returncode == 0 and predicate(output)
                if passed or time.time() >= deadline:
                    break
                time.sleep(0.2)
            step.actual_execution("$ %s" % result.command, output)
            actual = "returncode=%s attempts=%s elapsed=%.2fs" % (
                result.returncode, attempt, time.time() - started)
            step.assess(expected, actual, passed)
        if not passed:
            raise HaCommandFailure("%s: %s" % (title, actual))
        if config_before is not None:
            config_after = self.active_conf.read_text(encoding="utf-8")
            if config_before != config_after:
                diff_text = "".join(difflib.unified_diff(
                    config_before.splitlines(True), config_after.splitlines(True),
                    fromfile="config.before", tofile="config.after"))
                valid_diff, semantic_diff = self._semantic_config_diff(
                    config_before, config_after, self.last_ha_sql)
                self.record_step(
                    "%s：配置文件实际 diff（对应命令：%s）" %
                    (title, self.last_ha_sql),
                    "高可用命令: %s\ndiff -u config.before config.after" %
                    self.last_ha_sql,
                    "只包含本次高可用命令预期修改",
                    "语义变化:\n%s\n\n原始文本 diff (--minimal):\n%s" %
                    (semantic_diff, diff_text.rstrip() or "<no differences>"),
                    "PASS" if valid_diff else "FAIL",
                )
                if not valid_diff:
                    raise HaCommandFailure(
                        "configuration persistence changed an unexpected object or field")
        return output

    def psql_monitor(self, sql, title, expected, predicate, retry_timeout=5):
        """Run monitor SHOW in expanded form so field assertions are exact."""
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1", self.listen_port,
            "admin", "console", sql, expanded=True,
        )
        with self.evidence_step(title, expected=expected) as step:
            deadline = time.time() + max(0, retry_timeout)
            started = time.time()
            attempt = 0
            while True:
                attempt += 1
                result = run_logged_command(
                    command,
                    self.logs_dir / ("psql_monitor_%02d_%02d.log" %
                                     (self._step_order, attempt)),
                    cwd=self.workdir)
                output = result.output.rstrip() or "<empty>"
                passed = result.returncode == 0 and predicate(output)
                if passed or time.time() >= deadline:
                    break
                time.sleep(0.2)
            step.actual_execution("$ %s" % result.command, output)
            step.assess(expected,
                        "returncode=%s attempts=%s elapsed=%.2fs" %
                        (result.returncode, attempt, time.time() - started),
                        passed)
        if not passed:
            raise HaCommandFailure("%s: returncode=%s" % (title, result.returncode))
        return output

    @staticmethod
    def _expanded_rows(output, key):
        """Parse psql expanded output into rows indexed by one text column."""
        rows = {}
        current = {}
        for line in output.splitlines():
            if re.match(r"^-\[ RECORD", line):
                if current.get(key):
                    rows[current[key]] = current
                current = {}
                continue
            match = re.match(r"^([a-z_]+)\s*\|\s*(.*?)\s*$", line)
            if match:
                current[match.group(1)] = match.group(2)
        if current.get(key):
            rows[current[key]] = current
        return rows

    def wait_node_monitor(self, title, expected_rows, retry_timeout=30):
        """Wait until named node projections expose the requested trusted fields."""
        expected = "; ".join(
            "%s %s" % (node, ", ".join(
                "%s=%s" % (field, value)
                for field, value in sorted(fields.items())))
            for node, fields in sorted(expected_rows.items()))

        def matches(output):
            rows = self._expanded_rows(output, "node_name")
            for node, fields in expected_rows.items():
                row = rows.get(node)
                if row is None or any(row.get(field) != value
                                      for field, value in fields.items()):
                    return False
            return True

        return self.psql_monitor(
            "SHOW NODE_MONITOR;", title, expected, matches,
            retry_timeout=retry_timeout)

    def assert_table(self, sql, title, expected_rows, key="node_name", retry_timeout=15):
        """Execute a console query and assert table rows with structured diffs."""
        from framework.clients.psql import assert_table_rows

        expected_desc = "; ".join(
            "%s [%s]" % (k, ", ".join("%s=%s" % (col, val) for col, val in v.items()))
            for k, v in expected_rows.items()
        )
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1", self.listen_port,
            "admin", "console", sql,
        )
        with self.evidence_step(title, expected=expected_desc) as step:
            deadline = time.time() + max(0, retry_timeout)
            started = time.time()
            attempt = 0
            last_summary = ""
            while True:
                attempt += 1
                result = run_logged_command(
                    command,
                    self.logs_dir / ("assert_table_%02d_%02d.log" % (self._step_order, attempt)),
                    cwd=self.workdir,
                )
                output = result.output.rstrip() or "<empty>"
                passed, summary, _ = assert_table_rows(output, expected_rows, key=key)
                last_summary = summary
                if (result.returncode == 0 and passed) or time.time() >= deadline:
                    break
                time.sleep(0.3)

            step.actual_execution("$ %s" % result.command, output)
            step.assess(expected_desc, last_summary, result.returncode == 0 and passed)

        if result.returncode != 0 or not passed:
            raise HaCommandFailure("%s 失败:\n%s" % (title, last_summary))
        return output

    def psql_business(self, sql, title, expected, predicate, group="mmr_group",
                      port=None, user="postgres", retry_timeout=0):
        """Run a query through the configured MMR business route."""
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1",
            port or self.listen_port, user, group, sql,
        )
        deadline = time.time() + retry_timeout
        attempt = 0
        order = self._next_order()
        with self.evidence_step(title, expected=expected) as step:
            while True:
                attempt += 1
                result = run_logged_command(
                    command, self.logs_dir / ("psql_business_%02d_%02d.log" % (order, attempt)),
                    cwd=self.workdir,
                )
                output = result.output.rstrip() or "<empty>"
                passed = result.returncode == 0 and predicate(output)
                if passed or time.time() >= deadline:
                    break
                time.sleep(0.2)
            step.actual_execution("$ %s" % result.command, output)
            actual = "returncode=%s attempts=%s" % (result.returncode, attempt)
            step.assess(expected, actual, passed)
        if not passed:
            raise HaCommandFailure("%s: %s" % (title, actual))
        return output

    def psql_business_error(self, sql, title, expected, predicate,
                            group="mmr_group", port=None, user="postgres"):
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1",
            port or self.listen_port, user, group, sql,
        )
        with self.evidence_step(title, expected=expected) as step:
            result = run_logged_command(
                command, self.logs_dir / ("psql_business_error_%02d.log" % self._next_order()),
                cwd=self.workdir,
            )
            output = result.output.rstrip() or "<empty>"
            step.actual_execution("$ %s" % result.command, output)
            passed = result.returncode != 0 and predicate(output)
            actual = "returncode=%s" % result.returncode
            step.assess(expected, actual, passed)
        if not passed:
            raise HaCommandFailure("%s: %s" % (title, actual))
        return output

    def diff(self, before, after):
        command = ["diff", "-u", str(before), str(after)]
        result = run_logged_command(command, self.logs_dir / "config_diff.log", cwd=self.workdir)
        output = result.output.rstrip() or "<no differences>"
        self.record_step("检查配置文件 diff（与测试开始时初始配置比较）", result.command,
                         "与测试开始时保存的初始配置无差异", output,
                         "PASS" if result.returncode == 0 else "FAIL")
        if result.returncode != 0:
            raise HaCommandFailure("configuration diff is not empty: %s" % output)

    def diff_contains(self, before, after, expected, title):
        command = ["diff", "-u", str(before), str(after)]
        result = run_logged_command(
            command, self.logs_dir / ("config_diff_%02d.log" % (self._step_order + 1)),
            cwd=self.workdir,
        )
        output = result.output.rstrip() or "<no differences>"
        passed = result.returncode == 1 and all(item in output for item in expected)
        self.record_step(
            title, result.command, "diff 包含: %s" % ", ".join(expected), output,
            "PASS" if passed else "FAIL",
        )
        if not passed:
            raise HaCommandFailure("configuration diff did not contain expected change: %s" % output)

    @staticmethod
    def _backup_dir_path(config_path, backup_dir):
        if backup_dir is not None:
            return Path(backup_dir)
        return Path(config_path).parent / "conf-backup"

    def backup_checkpoint(self, config_path, backup_dir=None):
        backup_dir = self._backup_dir_path(config_path, backup_dir)
        files = tuple(path.name for path in backup_dir.iterdir()
                      if ".bak." in path.name) if backup_dir.is_dir() else ()
        return BackupCheckpoint(files, Path(config_path).read_bytes(), backup_dir)

    def assert_backup_created(self, checkpoint, config_path, title="验证配置备份文件"):
        backup_dir = checkpoint.backup_dir or self._backup_dir_path(config_path, None)
        current = set(path.name for path in backup_dir.iterdir()
                      if ".bak." in path.name) if backup_dir.is_dir() else set()
        created = sorted(current - set(checkpoint.files))
        content_matches = False
        if len(created) == 1:
            content_matches = (backup_dir / created[0]).read_bytes() == checkpoint.config_content
        actual = "新增备份=%s；备份数量 %d -> %d；内容与命令前配置%s" % (
            created[0] if len(created) == 1 else created,
            len(checkpoint.files), len(current),
            "一致" if content_matches else "不一致",
        )
        passed = len(created) == 1 and content_matches
        self.record_step(title, "检查备份目录 %s" % backup_dir,
                         "恰好新增一个备份，且内容等于命令执行前配置",
                         actual, "PASS" if passed else "FAIL")
        if not passed:
            raise HaCommandFailure("backup verification failed: %s" % actual)
        return created[0]

    def assert_no_backup_created(self, checkpoint, config_path,
                                 title="验证未创建配置备份"):
        backup_dir = checkpoint.backup_dir or self._backup_dir_path(config_path, None)
        current = set(path.name for path in backup_dir.iterdir()
                      if ".bak." in path.name) if backup_dir.is_dir() else set()
        created = sorted(current - set(checkpoint.files))
        actual = "新增备份=%s；备份数量 %d -> %d" % (
            created, len(checkpoint.files), len(current),
        )
        passed = current == set(checkpoint.files)
        self.record_step(title, "检查备份目录 %s" % backup_dir,
                         "备份文件集合保持不变", actual,
                         "PASS" if passed else "FAIL")
        if not passed:
            raise HaCommandFailure("unexpected backup created: %s" % actual)

    def psql_error(self, sql, title, expected, predicate, compare_config=True):
        config_before = self.active_conf.read_bytes() if self.active_conf else None
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1",
            self.listen_port, "admin", "console", sql,
        )
        with self.evidence_step(title, expected=expected) as step:
            result = run_logged_command(
                command, self.logs_dir / ("psql_error_%02d.log" % self._next_order()),
                cwd=self.workdir,
            )
            output = result.output.rstrip() or "<empty>"
            step.actual_execution("$ %s" % result.command, output)
            passed = result.returncode != 0 and predicate(output)
            actual = "returncode=%s" % result.returncode
            step.assess(expected, actual, passed)
        if not passed:
            raise HaCommandFailure("%s: %s" % (title, actual))
        if config_before is not None and compare_config:
            config_after = self.active_conf.read_bytes()
            unchanged = config_before == config_after
            self.record_step(
                "%s：验证错误命令未修改活动配置" % title,
                "逐字节比较命令执行前后的活动配置文件",
                "配置文件内容保持不变",
                "config unchanged=%s" % unchanged,
                "PASS" if unchanged else "FAIL",
            )
            if not unchanged:
                raise HaCommandFailure("rejected HA command changed active configuration")
        return output

    def write_report(self, status, reason=None):
        timeline = []
        for item in self.steps:
            checks = []
            for check in item.get("checks", []):
                if isinstance(check, ReportCheck):
                    checks.append(check)
                else:
                    checks.append(ReportCheck(*check))
            timeline.append((item["order"], ReportStep(
                item["title"], details=item["details"],
                execution=([{"label": "实际执行", "text": item["command"]}] if item["command"] else []),
                key_expected=item["expected"], actual=item["actual"], result=item["result"],
                checks=checks,
            )))
        for item in self.step_journal.steps:
            timeline.append((item.get("order", 0), ReportStep(
                item["title"], execution=item.get("execution", []),
                intermediate=item.get("intermediate", []), evidence=item.get("evidence", []),
                key_expected=item.get("expected"), actual=item.get("actual"),
                result=item.get("result"),
            )))
        items = [item for _, item in sorted(timeline, key=lambda value: value[0])]
        group_clusters = {
            "mmr_group": ("pg_cluster_1", "pg_cluster_2"),
            "rep_group": ("pg_cluster_1",),
            "balance_group": ("pg_cluster_1", "pg_cluster_2"),
            "single_group": ("pg_cluster_1",),
        }
        relevant_clusters = set()
        for group in self.case.report_groups:
            relevant_clusters.update(group_clusters.get(group, ()))
        report_datasources = [
            item for item in getattr(self, "datasource_metadata", [])
            if self.case.report_all_datasources or item["cluster"] in relevant_clusters
        ]
        datasource_lines = [
            "datasource %s: host=%s port=%s cluster=%s application_name=%s "
            "system_identifier=%s status=%s" % (
                item["name"], item["host"], item["port"], item["cluster"],
                item["application_name"], item["system_identifier"],
                item["status"],
            )
            for item in report_datasources
        ]
        group_descriptions = {
            "mmr_group": "group mmr_group: mode=mmr backend_clusters=pg_cluster_1,pg_cluster_2 "
                         "write_cluster=pg_cluster_2 promoted_cluster=pg_cluster_1 check=auto",
            "rep_group": "group rep_group: mode=replication backend_clusters=pg_cluster_1 "
                         "access_mode=default check=auto",
            "balance_group": "group balance_group: mode=balance "
                             "backend_clusters=pg_cluster_1,pg_cluster_2 "
                             "access_mode=read_write check=auto",
            "single_group": "group single_group: mode=single backend_clusters=pg_cluster_1 "
                            "access_mode=read_write check=auto",
        }
        if self.case.name == "reload_failure_rollback":
            group_descriptions["single_group"] = (
                "group single_group: mode=single backend_clusters=pg_cluster_1 "
                "access_mode=read_only check=auto (唯一 active replica=pg_3)"
            )
        configured_group_lines = getattr(self, "comprehensive_group_lines", None)
        if configured_group_lines is not None:
            group_lines = list(configured_group_lines)
        else:
            group_lines = [group_descriptions[group] for group in self.case.report_groups
                           if group in group_descriptions]
        group_lines.append("routing ports: proxy=%s; %s" % (
            self.listen_port,
            "; ".join("%s=%s" % (item["name"], item["port"])
                      for item in report_datasources),
        ))
        if self.case.route_mode:
            group_lines.append("routing mode: %s" % self.case.route_mode)
        crash_info = None
        if status == "FAIL":
            try:
                start_ts = self.started_at.timestamp() if hasattr(self.started_at, "timestamp") else time.time() - 3600
                crash_info = self.process.diagnose_crash(
                    search_dirs=[self.workdir, self.run_root, self.logs_dir],
                    since_time=start_ts,
                )
                if crash_info and (crash_info.get("is_crash") or crash_info.get("backtrace")):
                    bt = crash_info.get("backtrace")
                    if bt:
                        try:
                            (self.run_root / "backtrace.txt").write_text(bt, encoding="utf-8")
                        except Exception:
                            pass
                    items.append(ReportStep(
                        title="🚨 进程崩溃诊断 (Crash Forensics)",
                        command="gdb --batch -ex 'bt full' [binary] [core]",
                        intermediate="崩溃信号: %s\nCore 文件: %s\n\n%s" % (
                            crash_info.get("signal") or "异常终止",
                            crash_info.get("core_file") or "未定位到 core 文件",
                            bt or "无调用栈信息",
                        ),
                        expected="fbasecman 正常执行，无 SIGSEGV/SIGABRT/SIGBUS 崩溃",
                        actual="检测到异常崩溃退出并抓取到现场",
                        result="FAIL",
                    ))
            except Exception as crash_err:
                self.trace("[crash] failed to diagnose crash: %s" % crash_err)

        document = ReportDocument(
            self.case.target, status,
            self.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            (self.finished_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
            self.case.summary,
            config_lines=[
                "测试拓扑: %s" % self.case.topology,
                "控制台命令端口: %s" % self.listen_port,
                "手动启动命令: %s %s --console --log_to_stdout" % (
                    shlex.quote(str(self.process.binary)),
                    shlex.quote(str(self.active_conf or (self.workdir / (self.case.name + ".conf")))),
                ),
            ] + datasource_lines + group_lines + [
            ],
            coverage_mapping=[],
            steps=items,
            pass_reason=reason if status == "PASS" else None,
            failure_reason=reason if status == "FAIL" else None,
        )
        from framework.persistence.atomic import atomic_write_text
        atomic_write_text(self.run_root / "report.txt", render_report(document))
        summary_payload = {
            "target": self.case.target, "status": status, "reason": reason,
            "source_sections": self.case.source_sections,
        }
        if crash_info and crash_info.get("is_crash"):
            summary_payload["crash_info"] = {
                "signal": crash_info.get("signal"),
                "core_file": crash_info.get("core_file"),
            }
        _json_write(self.run_root / "summary.json", summary_payload)

    def stop(self):
        self.process.stop(best_effort=True, record=False)

    def finish(self, status, reason=None):
        self.finished_at = datetime.now()
        self.write_report(status, reason)
        self.stop()

    def __enter__(self):
        self.lock = ExclusiveFileLock(self.root / "output" / "ha_commands.lock", "HA command suite")
        self.lock.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        if hasattr(self, "lock"):
            self.lock.__exit__(exc_type, exc_value, traceback)
        return False
