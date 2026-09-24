"""Runtime shared by the independent handover transfer cases.

The suite owns its product configuration and run artifacts, while using the
framework command/process/log primitives.  This deliberately does not import
the legacy rw_toggle or global_cache suites.
"""

import json
import os
import re
import shutil
import socket
import time
from datetime import datetime
from pathlib import Path

import yaml

from framework.clients.psql import build_psql_command, parse_psql_table, assert_table_rows
from framework.configuration import load_regression_config
from framework.evidence.log_window import (
    LocalLogWindow,
    parse_remote_log_snapshot,
    remote_collect_script,
    remote_snapshot_script,
)
from framework.evidence import (
    EvidenceStep, StepJournal, render_jdbc_action, without_phase_markers,
)
from framework.execution.command import run_logged_command
from framework.execution.shell import LoggedShellRunner, quote_arguments
from framework.persistence.atomic import atomic_write_text
from framework.reporting import (
    ReportCheck, ReportDocument, ReportStep, is_transport_only_success, render_report,
)
from products.fbasecman.process import FbasecmanProcess, FbasecmanProcessError
from products.fbasecman.config import remove_config_block_line, set_config_block_line
from suites.handover.manifest import HANDOVER_CASES


class HandoverFailure(RuntimeError):
    pass


def _port_free(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # A stopped listener can leave accepted connections in TIME_WAIT.  That
    # does not mean another process owns the listening port, and fbasecman can
    # bind it with SO_REUSEADDR during an immediate restart.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _handover_port_pair(case_index, forbidden=()):
    """Pick a case-local pair outside the previous runner's fixed port range."""
    # A new runner gets a new PID-derived range.  This prevents an aborted
    # previous runner from poisoning the same case in the next invocation.
    seed = (os.getpid() * 79 + case_index * 2) % 28000
    for offset in range(0, 28000, 2):
        port = 20000 + ((seed + offset) % 28000)
        if (port, port + 1) not in forbidden and _port_free(port) and _port_free(port + 1):
            return port, port + 1
    raise HandoverFailure("no free adjacent TCP port pair available for handover case")


def _json_write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _relevant_proxy_lines(text, limit=24):
    """Keep business evidence in report.txt; preserve the complete raw log on disk."""
    keywords = ("(query)", "(heartbeat)", "(routing)", "routing ", "attached", "detached",
                "read only", "read write", "(parse)", "(bind)",
                "(execute)", "(sync)", "error ", "errorresponse",
                "deleted backend cache", "outstanding_request")
    lines = [line for line in text.splitlines() if any(key in line.lower() for key in keywords)]
    if not lines:
        return "<无与本步骤直接相关的代理日志>"
    if len(lines) > limit:
        lines = lines[:limit] + ["... 其余相关日志保存在 fbasecman.log"]
    return "\n".join(lines)


def _relevant_pg_lines(text, sql=None, limit=36):
    """Render only action-scoped PG evidence; raw remote windows stay in logs/."""
    if not text.strip():
        return "<无新增日志>"
    if sql is None:
        return "<控制台命令不转发 PostgreSQL；完整并发 PG 日志保存在 logs/remote/>"
    normalized = " ".join(sql.lower().split())
    fragments = [part.strip() for part in normalized.split(";") if len(part.strip()) >= 3]
    lines = text.splitlines()
    selected = []
    for line in lines:
        lower = line.lower()
        if "===== log:" in lower:
            selected.append(line)
        elif ("statement:" in lower or "error:" in lower or "statement:" in lower) and (
                not fragments or any(fragment in lower for fragment in fragments)):
            selected.append(line)
    if not selected:
        selected = [line for line in lines if "error:" in line.lower()][:limit]
    if not selected:
        return "<无与当前 SQL 直接相关的 PostgreSQL 日志；完整日志保存在 logs/remote/>"
    if len(selected) > limit:
        selected = selected[:limit] + ["... 其余相关 PostgreSQL 日志保存在 logs/remote/"]
    return "\n".join(selected)


class HandoverRuntime(object):
    def __init__(self, root, case):
        self.root = Path(root)
        self.case = case
        self.env = load_regression_config(self.root)
        if not self.env.test_context_file.exists():
            raise HandoverFailure("missing %s, run ./run.sh env setup first" % self.env.test_context_file)
        self.context = yaml.safe_load(self.env.test_context_file.read_text(encoding="utf-8")) or {}
        self.run_root = self.env.output_dir / "runs" / "handover" / case.name
        self.workdir = self.run_root / "workdir"
        self.logs_dir = self.run_root / "logs"
        if self.run_root.exists():
            shutil.rmtree(str(self.run_root))
        self.logs_dir.mkdir(parents=True)
        self.workdir.mkdir(parents=True)
        self.started_at = datetime.now()
        self.finished_at = None
        self.steps = []
        self.checks = []
        self._step_order = 0
        self.step_journal = StepJournal(self.run_root / "steps.json", case.target)
        self._command_no = 0
        self._pg_marks = None
        self._pg_runner = LoggedShellRunner(self.logs_dir / "remote", verbose=False)
        self.local_windows = LocalLogWindow()
        self.proxy_log = self.run_root / "fbasecman.log"
        self._last_evidence_windows = ("", "")
        # Cases are serial, but an aborted prior runner can leave a listener.
        # Use a fresh runner-local range instead of reusing fixed case ports.
        self.case_index = [item.name for item in HANDOVER_CASES].index(case.name)
        self._port_pairs = []
        self.listen_port, self.read_port = _handover_port_pair(self.case_index)
        self._port_pairs.append((self.listen_port, self.read_port))
        self.pid_file = self.workdir / "fbasecman.pid"
        self._new_process()

    def _new_process(self):
        self.process = FbasecmanProcess(
            self.env.config["fbasecman"]["fbasecman_bin"],
            self.env.config["local"]["postgres_dir"],
            self.listen_port,
            self._prom_port(),
            self.pid_file,
            self.workdir / "locks",
            self.proxy_log,
            self.logs_dir,
            self.run_command,
            self.trace,
            _port_free,
        )

    def _refresh_ports(self):
        self.listen_port, self.read_port = _handover_port_pair(self.case_index, tuple(self._port_pairs))
        self._port_pairs.append((self.listen_port, self.read_port))
        self._new_process()
        self.trace("[retry] reassigned handover ports to %s,%s" % (self.listen_port, self.read_port))

    def _prom_port(self):
        start = 50000 + (sum(ord(ch) for ch in self.case.name) % 5000)
        for offset in range(5000):
            candidate = 50000 + ((start - 50000 + offset) % 5000)
            if candidate not in (self.listen_port, self.read_port) and _port_free(candidate):
                return candidate
        raise HandoverFailure("no free TCP port available for handover prometheus endpoint")

    def trace(self, message):
        with (self.run_root / "events.log").open("a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (datetime.now().strftime("%H:%M:%S"), message))

    def run_command(self, command, logfile, cwd=None, env=None, echo=False,
                    check=True, step_title=None, record=True):
        result = run_logged_command(command, logfile, cwd=cwd or self.root, env=env, echo=echo)
        if record:
            self.record_step(step_title or "执行命令", command=result.command, actual=result.output,
                             expected="命令执行完成", result="PASS" if result.returncode == 0 else "FAIL")
        if check and result.returncode != 0:
            raise HandoverFailure("command failed rc=%s: %s" % (result.returncode, result.command))
        return result.returncode, result.output

    def record_step(self, title, command=None, expected=None, actual=None, result=None, details=None):
        item = {"title": title, "command": command, "expected": expected,
                "actual": actual, "result": result, "details": details or [],
                "order": self._next_step_order()}
        self.steps.append(item)
        self._write_report("RUNNING")
        return item

    def _next_step_order(self):
        self._step_order += 1
        return self._step_order

    def evidence_step(self, title, sql=None, console=False, expected=None,
                      collect_logs=False):
        """Create one immediately-persisted business action with optional logs.

        Console output or JDBC results are the primary evidence for most
        business assertions.  A caller opts into log windows only when a
        conclusion specifically depends on proxy or PostgreSQL internals.
        """
        order = self._next_step_order()

        if not collect_logs:
            self._last_evidence_windows = ("", "")
            return EvidenceStep(
                title, self.step_journal, expected=expected,
                metadata={"order": order, "console": bool(console)},
                on_change=lambda: self._write_report("RUNNING"),
            )

        def open_window():
            marks = {"proxy": self.local_windows.mark([self.proxy_log])}
            if not console:
                marks["pg"] = self.pg_snapshot()
            return marks

        def collect_window(marks):
            proxy_window = self.local_windows.collect(marks["proxy"]).get(str(self.proxy_log), "")
            pg_window = self.pg_collect(marks["pg"]) if "pg" in marks else ""
            self._last_evidence_windows = (proxy_window, pg_window)
            return [
                ("fbasecman 证据", _relevant_proxy_lines(proxy_window)),
                ("PG 证据", _relevant_pg_lines(pg_window, None if console else sql)),
            ]

        return EvidenceStep(
            title, self.step_journal, open_window=open_window,
            collect_window=collect_window, expected=expected,
            metadata={"order": order, "console": bool(console)},
            on_change=lambda: self._write_report("RUNNING"),
        )

    def set_latest_evidence_outcome(self, expected, actual, passed):
        """Replace the transport outcome with the caller's business assertion."""
        if not self.step_journal.steps:
            raise HandoverFailure("no evidence step is available for outcome update")
        step = self.step_journal.steps[-1]
        step["expected"] = str(expected).strip()
        step["actual"] = str(actual).strip()
        step["result"] = "PASS" if passed else "FAIL"
        self.step_journal.save()
        self._write_report("RUNNING")

    def check(self, title, expected, actual, passed):
        result = "PASS" if passed else "FAIL"
        check = ReportCheck(title, expected, actual, result)
        self.checks.append(check)
        self._write_report("RUNNING")
        if not passed:
            raise HandoverFailure("%s: expected %s, actual %s" % (title, expected, actual))
        return check

    def _datasources(self):
        ports = self.env.config["database"]["ports"]
        db = self.env.config["database"]
        if self.case.topology == "ha_rep":
            return [
                ("pg_230", db["mmr_host"], ports["mmr2"]),
                ("pg_250", db["mmr_host"], ports["mmr2_standby1"]),
            ]
        if self.case.topology == "rep":
            return [
                ("pg_220", db["mmr_host"], ports["mmr1"]),
                ("pg_230", db["mmr_host"], ports["mmr1_standby1"]),
                ("pg_240", db["mmr_host"], ports["mmr1_standby2"]),
            ]
        if self.case.topology == "balance":
            return [
                ("pg_220", db["mmr_host"], ports["mmr1"]),
                ("pg_230", db["mmr_host"], ports["mmr2"]),
                ("pg_240", db["mmr_host"], ports["mmr3"]),
            ]
        return [
            ("pg_220", db["mmr_host"], ports["mmr1"]),
            ("pg_230", db["mmr_host"], ports["mmr2"]),
            ("pg_240", db["mmr_host"], ports["mmr1_standby1"]),
            ("pg_250", db["mmr_host"], ports["mmr2_standby1"]),
        ]

    def _configuration_metadata(self):
        """Read only the live identifiers required by the active document check."""
        needs_system_identifier = self.case.executor == "configuration"
        needs_mmr_metadata = (self.case.topology == "mmr" and
                              self.case.executor in ("configuration", "console_metadata"))
        if not needs_system_identifier and not needs_mmr_metadata:
            return {}
        db = self.env.config["database"]
        postgres = Path(self.env.config["local"]["postgres_dir"]) / "bin" / "psql"
        user = db["mmr_pg_user"]
        host = db["mmr_host"]
        values = {"system_identifiers": {}}
        if needs_system_identifier:
            for name, _, port in self._datasources():
                command = [str(postgres), "-h", host, "-p", str(port), "-U", user, "-d", "postgres",
                           "-At", "-c", "SELECT system_identifier FROM pg_control_system();"]
                result = run_logged_command(command, self.logs_dir / ("config_sysid_%s.log" % name), cwd=self.workdir)
                identifier = result.output.strip()
                if result.returncode == 0 and identifier.isdigit():
                    values["system_identifiers"][name] = identifier
        if needs_mmr_metadata:
            command = [str(postgres), "-h", host, "-p", str(db["ports"]["mmr1"]), "-U", user,
                       "-d", "postgres", "-At", "-c",
                       "SELECT group_name || '|' || group_uuid FROM fdd.mmr_group ORDER BY group_name LIMIT 1;"]
            result = run_logged_command(command, self.logs_dir / "config_group_metadata.log", cwd=self.workdir)
            fields = result.output.strip().split("|", 1)
            if result.returncode == 0 and len(fields) == 2 and all(fields):
                values["real_group_name"], values["group_uuid"] = fields
        return values

    def render_conf(self, extra_lines=None, group_overrides=None,
                    datasource_overrides=None, user_overrides=None,
                    foreground=False, heartbeat_request="select 12"):
        if self.case.topology not in ("mmr", "rep", "ha_rep", "balance"):
            raise HandoverFailure("%s requires a database topology" % self.case.target)
        group_overrides = group_overrides or {}
        datasource_overrides = datasource_overrides or {}
        user_overrides = user_overrides or {}
        metadata = self._configuration_metadata()
        group_mode = "replication" if self.case.topology in ("rep", "ha_rep") else self.case.topology
        route = self.case.route_mode or "hint"
        ports = '"%s,%s"' % (self.listen_port, self.read_port) if route == "port" else '"%s"' % self.listen_port
        write_port = "    write_port %s\n" % self.listen_port if route == "port" else ""
        if self.case.topology in ("rep", "ha_rep"):
            cluster_name = "mmr_cluster_2" if self.case.topology == "ha_rep" else "rep_cluster"
            cluster_names = {name: cluster_name for name, _, _ in self._datasources()}
            group_lines = {
                "storage_db": '    storage_db "postgres"',
                "backend_clusters": '    backend_clusters "%s"' % cluster_name,
                "check": '    check "auto"',
            }
        elif self.case.topology == "balance":
            cluster_names = {
                name: "balance_cluster_%d" % index
                for index, (name, _, _) in enumerate(self._datasources(), 1)
            }
            group_lines = {
                "storage_db": '    storage_db "postgres"',
                "backend_clusters": '    backend_clusters "%s"' % ",".join(cluster_names.values()),
                "access_mode": '    access_mode "read_write"',
                "check": '    check "auto"',
            }
        else:
            cluster_names = {
                "pg_220": "mmr_cluster_1", "pg_240": "mmr_cluster_1",
                "pg_230": "mmr_cluster_2", "pg_250": "mmr_cluster_2",
            }
            group_lines = {
                "storage_db": '    storage_db "postgres"',
                "backend_clusters": '    backend_clusters "mmr_cluster_1,mmr_cluster_2"',
                "write_cluster": '    write_cluster "mmr_cluster_1"',
                "promoted_cluster": '    promoted_cluster "mmr_cluster_2"',
                "check": '    check "auto"',
            }
        if self.case.topology == "mmr":
            if metadata.get("real_group_name"):
                group_lines["real_group_name"] = '    real_group_name "%s"' % metadata["real_group_name"]
                group_lines["group_uuid"] = '    group_uuid "%s"' % metadata["group_uuid"]
        group_lines.update(group_overrides)
        data_blocks = []
        for name, host, port in self._datasources():
            system_identifier = metadata.get("system_identifiers", {}).get(name)
            if self.case.topology == "mmr":
                app_name = name if name in ("pg_240", "pg_250") else ""
            elif self.case.topology == "rep":
                app_name = "pg_240" if name == "pg_230" else "pg_241" if name == "pg_240" else ""
            else:
                app_name = ""
            node_lines = {
                "host": '    host "%s"' % host,
                "port": '    port %s' % port,
                "cluster_name": '    cluster_name "%s"' % cluster_names[name],
                "application_name": ('    application_name "%s"' % app_name
                                     if app_name else ''),
                "weight": '    weight 10',
                "system_identifier": ('    system_identifier "%s"' % system_identifier)
                if system_identifier else '',
                "server_max_routing": '    server_max_routing 100',
                "tls": '    tls "disable"',
            }
            node_lines.update(datasource_overrides.get(name, {}))
            data_blocks.append('\n'.join([
                'datasources "%s" {' % name, '\n'.join(node_lines.values()), '}',
            ]))
        user_lines = {
            "rw_split_method": '    rw_split_method "%s"' % route,
            "pool": '    pool "transaction"',
            "pool_size": '    pool_size 20',
            "pool_discard": '    pool_discard no',
            "pool_reserve_prepared_statement": '    pool_reserve_prepared_statement yes',
        }
        user_lines.update(user_overrides)
        config = '\n'.join([
            'pid_file "%s"' % self.pid_file, 'daemonize %s' % ("no" if foreground else "yes"), 'unix_socket_dir "/tmp"', 'unix_socket_mode "0644"',
            'locks_dir "%s"' % (self.workdir / "locks"), 'log_to_stdout no', 'log_syslog no',
            'log_format "%p %t %l [%i %s] (%c) %m\\n"',
            'log_debug yes', 'log_config yes', 'log_session yes', 'log_query yes', 'log_stats yes',
            'coroutine_stack_size 16',
            'log_file "%s"' % self.proxy_log, 'log_min_messages "debug1"',
            'promhttp_server_port %s' % self._prom_port(), 'enable_guc_sync yes',
            'heartbeat_request "%s"' % heartbeat_request, 'admin_database "console"', 'host "*"', 'ports %s' % ports,
            'backlog 128', 'compression yes', 'license_dir "%s"' % self.env.config["fbasecman"]["license_dir"],
            '', 'group "postgres" {', '    group_mode "%s"' % group_mode, write_port.rstrip(),
            group_lines["storage_db"], group_lines["backend_clusters"],
            group_lines.get("access_mode", ""), group_lines.get("write_cluster", ""),
            group_lines.get("promoted_cluster", ""),
            group_lines.get("real_group_name", ""), group_lines.get("group_uuid", ""),
            group_lines["check"], '}', '', '\n\n'.join(data_blocks), '',
            'user "postgres" {', '    group_names "postgres"', '    authentication "none"',
            '    storage_user "postgres"', '\n'.join(user_lines.values()), '}', '',
            'user "admin" {', '    authentication "none"', '    pool "session"', '    role "admin"', '}',
            '\n'.join(extra_lines or []), '',
        ])
        path = self.workdir / ("%s.conf" % self.case.name)
        path.write_text("\n".join(line for line in config.splitlines() if line.strip()) + "\n", encoding="utf-8")
        return path

    def start(self, foreground=False, **kwargs):
        conf = self.render_conf(foreground=foreground, **kwargs)
        # A daemon may acknowledge --stop before its listener has disappeared.
        # Wait here so the following case never turns that cleanup race into a
        # product failure.
        self.process._force_cleanup(best_effort=True, record=False)
        deadline = time.time() + 10.0
        while not _port_free(self.listen_port) and time.time() < deadline:
            time.sleep(0.2)
        last_error = None
        for attempt in range(3):
            try:
                # Handover records one business-facing start step below after
                # readiness succeeds.  Keep the adapter command in its raw log
                # instead of rendering a second, partial start step.
                self.process.start(conf, foreground=foreground, record=False)
                last_error = None
                break
            except FbasecmanProcessError as exc:
                last_error = exc
                self.process._force_cleanup(best_effort=True, record=False)
                if attempt < 2:
                    self._refresh_ports()
                    conf = self.render_conf(foreground=foreground, **kwargs)
                time.sleep(1.0)
        if last_error is not None:
            raise last_error
        self.record_step("启动 fbasecman", command="%s %s" % (self.process.binary, conf),
                         actual="控制台连接成功", details=[("节点和路由配置", "拓扑=%s, 路由=%s, 写端口=%s, 读端口=%s" % (
                             self.case.topology, self.case.route_mode, self.listen_port, self.read_port))])
        return conf

    def stop(self):
        self.process.stop(best_effort=True, record=False)

    def restart_active_configuration(self, ready_timeout=30.0):
        """Restart fbasecman with the edited active configuration.

        Some topology changes are intentionally rejected by RELOAD because
        keeping existing runtime topology objects would be unsafe.  Chapter
        8.4 needs to load a separately valid read-only topology after proving
        that the in-place RELOAD is rejected.
        """
        conf = self.process.active_conf
        if conf is None:
            raise HandoverFailure("cannot restart without an active fbasecman configuration")
        try:
            self.process.stop(best_effort=False, record=False)
            self.process.start(conf, ready_timeout=ready_timeout, record=False)
        except FbasecmanProcessError as exc:
            raise HandoverFailure("failed to restart fbasecman with edited configuration: %s" % exc)
        self.record_step(
            "重启 fbasecman 加载降级配置",
            command="%s %s --stop; %s %s" % (
                self.process.binary, conf, self.process.binary, conf,
            ),
            expected="修改后的独立 replication 配置启动并可登录控制台",
            actual="控制台连接成功",
            result="PASS",
        )
        return conf

    def update_group_config(self, key, line):
        conf = self.process.active_conf
        if conf is None:
            raise HandoverFailure("cannot update group configuration before start")
        rendered = conf.read_text(encoding="utf-8")
        rendered = set_config_block_line(rendered, "group", "postgres", key, line)
        conf.write_text(rendered, encoding="utf-8")
        self.record_step("修改故障切换配置", command="编辑 %s" % conf,
                         actual=line, expected="配置写入 group postgres", result="PASS")

    def update_datasource_config(self, datasource, key, line):
        conf = self.process.active_conf
        if conf is None:
            raise HandoverFailure("cannot update datasource configuration before start")
        rendered = conf.read_text(encoding="utf-8")
        rendered = set_config_block_line(rendered, "datasources", datasource, key, line)
        conf.write_text(rendered, encoding="utf-8")
        self.record_step(
            "修改数据源状态: %s" % datasource,
            command="编辑 %s" % conf,
            actual=line,
            expected="配置写入 datasources %s" % datasource,
            result="PASS",
        )

    def remove_group_config(self, key):
        conf = self.process.active_conf
        if conf is None:
            raise HandoverFailure("cannot update group configuration before start")
        rendered = conf.read_text(encoding="utf-8")
        updated = remove_config_block_line(rendered, "group", "postgres", key)
        if updated == rendered:
            raise HandoverFailure("group configuration %s was not present" % key)
        conf.write_text(updated, encoding="utf-8")
        self.record_step("删除冲突的故障切换配置", command="编辑 %s" % conf,
                         actual="删除 group postgres 的 %s" % key,
                         expected="故障场景不保留与隔离节点或 replication 组冲突的配置", result="PASS")

    def reload(self):
        rc, output, _, _ = self.console("RELOAD;", "console=> RELOAD;", check_rc=False)
        self.set_latest_evidence_outcome("控制台返回 RELOAD", output,
                                        rc == 0 and "RELOAD" in output.upper())
        self.check("配置重载成功", "输出 RELOAD", output, rc == 0 and "RELOAD" in output.upper())

    def psql(self, sql, database="postgres", user="postgres", port=None, title=None,
             check_rc=True, expected=None, collect_logs=False):
        self._command_no += 1
        port = int(port or self.listen_port)
        logfile = self.logs_dir / ("%02d_psql.log" % self._command_no)
        command = build_psql_command(self.env.config["local"]["postgres_dir"], "127.0.0.1", port,
                                     user, database, sql)
        with self.evidence_step(
                title or "psql 执行 SQL", sql=sql, console=(database == "console"),
                expected=expected or "psql 命令完成", collect_logs=collect_logs) as step:
            rc, output = self.run_command(command, logfile, check=False, record=False)
            actual = output.rstrip() or "<empty>"
            step.actual_execution("$ " + quote_arguments(command), actual)
            step.assess(expected or "psql 命令完成", actual, rc == 0)
        proxy_window, pg_window = self._latest_evidence_windows()
        if check_rc and rc != 0:
            raise HandoverFailure("psql failed: %s" % actual)
        return rc, output, proxy_window, pg_window

    def psql_script(self, statements, database="postgres", user="postgres", port=None, title=None,
                    check_rc=True, expected=None, collect_logs=False):
        """Run one SQL statement per frontend message while retaining one psql session."""
        self._command_no += 1
        port = int(port or self.listen_port)
        script = self.workdir / ("%02d_script.sql" % self._command_no)
        script.write_text("\\set ON_ERROR_STOP off\n" + "\n".join(
            statement.rstrip(";") + ";" for statement in statements
        ) + "\n", encoding="utf-8")
        logfile = self.logs_dir / ("%02d_psql_script.log" % self._command_no)
        command = build_psql_command(self.env.config["local"]["postgres_dir"], "127.0.0.1", port,
                                     user, database, "")
        # build_psql_command adds -c; replace that empty query with the script form.
        command = command[:-2] + ["-f", str(script)]
        script_text = script.read_text(encoding="utf-8")
        with self.evidence_step(
                title or "psql 执行 SQL 脚本", sql=script_text,
                console=(database == "console"), expected=expected or "psql 脚本执行完成",
                collect_logs=collect_logs) as step:
            rc, output = self.run_command(command, logfile, check=False, record=False)
            actual = output.rstrip() or "<empty>"
            step.actual_execution(
                "$ " + quote_arguments(command) + "\n\n-- psql 输入脚本 --\n" + script_text,
                actual,
            )
            passed = (rc == 0) if check_rc else True
            step.assess(expected or "psql 脚本执行完成", actual, passed)
        proxy_window, pg_window = self._latest_evidence_windows()
        if check_rc and rc != 0:
            raise HandoverFailure("psql script failed: %s" % output)
        return rc, output, proxy_window, pg_window

    def _latest_evidence_windows(self):
        return self._last_evidence_windows

    def observe_jdbc_phases(self, process, source, jdbc_url, actions, observe,
                            timeout=30, finish_timeout=30, collect_logs=False):
        """Inspect the proxy while a JDBC driver deliberately holds a phase."""
        observations = {}
        output_offset = 0
        try:
            for action in actions:
                title = getattr(action, "title", None) or ("JDBC 阶段: %s" % action.name)
                expected = getattr(action, "expected", None) or "JDBC 到达 %s 并保持连接，观察状态符合预期" % action.name
                with self.evidence_step(title, sql="", expected=expected,
                                        collect_logs=collect_logs) as step:
                    marker = process.wait_for(action.marker, timeout=timeout)
                    current_output = process.output
                    jdbc_action, visible_output = render_jdbc_action(
                        source, jdbc_url,
                        without_phase_markers(current_output[output_offset:]),
                    )
                    step.actual_execution(jdbc_action, visible_output)
                    observed = observe(action.name, marker)
                    if isinstance(observed, dict) and "command" in observed:
                        step.intermediate_state(observed["command"], observed.get("output", ""))
                    observations[action.name] = observed
                    if isinstance(observed, dict):
                        expected = observed.get("expected", expected)
                        passed = observed.get("passed", True)
                        actual = observed.get("actual")
                    else:
                        passed = True
                        actual = None
                    step.assess(expected, actual or "JDBC 已到达该阶段并保持连接等待控制端继续。", passed)
                    if not passed:
                        raise HandoverFailure("%s: %s" % (title, actual or "中间状态不符合预期"))
                    output_offset = len(current_output)
                if action.resume_command is not None:
                    process.resume(action.resume_command)
            rc, output = process.finish(timeout=finish_timeout)
            return observations, rc, output, self._last_evidence_windows
        except Exception:
            process.terminate()
            raise

    def console(self, sql, title=None, check_rc=True, expected=None, collect_logs=False):
        return self.psql(
            sql, database="console", user="admin", title=title or ("console=> %s" % sql),
            check_rc=check_rc, expected=expected, collect_logs=collect_logs,
        )

    def assert_console_table(self, sql, expected_rows, title=None, key="node_name"):
        """
        Execute console SQL, parse output table, and strictly verify expected rows and fields.
        Records an evidence step and adds a formal ReportCheck for every field.
        """
        rc, output, proxy, pg = self.console(
            sql,
            title=title or ("验证控制台表格字段: %s" % sql),
            expected="控制台输出包含预期的行和字段值",
        )
        passed, summary, details = assert_table_rows(output, expected_rows, key=key)
        
        # Format expected & actual for the ReportCheck
        expected_desc = []
        actual_desc = []
        for key_val, expected_fields in expected_rows.items():
            expected_desc.append("%s: %s" % (key_val, ", ".join("%s=%s" % (k, v) for k, v in expected_fields.items())))
        for item in details:
            if item["status"] == "PASS":
                actual_desc.append("%s: %s" % (item["key"], ", ".join(item["matches"])))
            elif item["status"] == "MISSING":
                actual_desc.append("%s: <未找到该行>" % item["key"])
            else:
                actual_desc.append("%s: 不匹配项 [%s]" % (item["key"], "; ".join(item["errors"])))

        self.check(
            title or ("控制台 %s 字段比对" % sql),
            "\n".join(expected_desc),
            "\n".join(actual_desc),
            passed,
        )
        return output

    def assert_proxy_log_pattern(self, pattern, title=None, expected=None):
        """
        Assert that proxy log contains the given pattern.
        """
        proxy_window, _ = self._latest_evidence_windows()
        text_to_search = proxy_window if proxy_window.strip() else (
            self.proxy_log.read_text(encoding="utf-8", errors="replace") if self.proxy_log.exists() else ""
        )
        matched = bool(re.search(pattern, text_to_search, re.IGNORECASE))
        self.check(
            title or ("代理日志匹配: %s" % pattern),
            expected or ("日志包含模式: %s" % pattern),
            "找到匹配日志" if matched else "未在当前时间窗口日志中找到匹配项",
            matched,
        )
        return matched

    def assert_pg_log_pattern_absent(self, pattern, title=None, expected=None):
        """
        Assert that remote PostgreSQL logs do NOT contain the given pattern.
        """
        _, pg_window = self._latest_evidence_windows()
        matched = bool(re.search(pattern, pg_window, re.IGNORECASE))
        self.check(
            title or ("PostgreSQL 日志无匹配: %s" % pattern),
            expected or ("PG 日志绝对不包含: %s" % pattern),
            "未发现该语句日志（符合预期，拦截成功）" if not matched else "错误：PG 日志中发现了被拦截语句！",
            not matched,
        )
        return not matched

    def pg_snapshot(self):
        db = self.env.config["database"]
        base = db.get("mmr_data_root", db["mmr_postgres_dir"])
        user = db["mmr_pg_user"]
        host = db["mmr_host"]
        nodes = ("test_mmr1", "test_mmr1_s1", "test_mmr1_s2") if self.case.topology == "rep" else (
            "test_mmr1", "test_mmr1_s1", "test_mmr2", "test_mmr2_s1", "test_mmr3")
        log_dirs = ["%s/%s/pg_log" % (base, node) for node in nodes]
        script = remote_snapshot_script(log_dirs)
        result = self._pg_runner.run_remote(user, host, script, "pg_snapshot_%02d.log" % self._command_no, check=False)
        return (user, host, log_dirs, parse_remote_log_snapshot(result.stdout))

    def pg_collect(self, snapshot):
        user, host, log_dirs, marks = snapshot
        script = remote_collect_script(log_dirs, marks)
        result = self._pg_runner.run_remote(user, host, script, "pg_window_%02d.log" % self._command_no, check=False)
        return result.stdout

    def control_pg_node(self, node, action):
        """Perform a documented HA fault/recovery action through the framework shell runner."""
        if action not in ("start", "stop", "promote"):
            raise ValueError("unsupported PG action: %s" % action)
        db = self.env.config["database"]
        data_root = db.get("mmr_data_root", db["mmr_postgres_dir"])
        pg_dir = db["mmr_postgres_dir"]
        nodes = {
            "mmr1": ("test_mmr1", db["mmr_pg_user"], db["mmr_host"]),
            "mmr2": ("test_mmr2", db["mmr_pg_user"], db["mmr_host"]),
            "mmr1_s1": ("test_mmr1_s1", db["mmr_pg_user"], db["mmr_host"]),
            "mmr2_s1": ("test_mmr2_s1", db["mmr_pg_user"], db["mmr_host"]),
        }
        pgdata_name, user, host = nodes[node]
        pgdata = "%s/%s" % (data_root, pgdata_name)
        if action == "stop":
            command = '%s/bin/pg_ctl -D %s -m immediate stop' % (pg_dir, pgdata)
        elif action == "promote":
            command = '%s/bin/pg_ctl -D %s promote -w' % (pg_dir, pgdata)
        else:
            # A just-restarted node can report "not running" while recovery is
            # still in progress.  Poll status first, then issue a non-waiting
            # request; readiness is checked by the case/environment afterwards.
            pg_ctl = '%s/bin/pg_ctl -D %s' % (pg_dir, pgdata)
            command = (
                'for n in 1 2 3 4 5; do %s status >/dev/null 2>&1 && '
                '{ echo "running: %s"; exit 0; }; sleep 1; done; '
                '%s -W start >/dev/null 2>&1 || true; echo "start requested: %s"'
            ) % (pg_ctl, pgdata_name, pg_ctl, pgdata_name)
        result = self._pg_runner.run_remote(user, host, command, "pg_%s_%s.log" % (action, node), check=False)
        self.record_step("PG 节点%s: %s" % (action, node), command=command,
                         expected="pg_ctl %s 成功" % action,
                         actual=(result.stdout + result.stderr).strip() or "<empty>",
                         result="PASS" if result.returncode == 0 else "FAIL")
        if result.returncode != 0:
            raise HandoverFailure("pg_ctl %s %s failed" % (action, node))

    def remote_pg_sql(self, node, sql, dbname="postgres"):
        """Execute SQL directly on the backend PG node (e.g. for pause/resume replay)."""
        db = self.env.config["database"]
        pg_dir = db["mmr_postgres_dir"]
        nodes = {
            "mmr1": ("test_mmr1", db["mmr_pg_user"], db["mmr_host"], db["ports"]["mmr1"]),
            "mmr2": ("test_mmr2", db["mmr_pg_user"], db["mmr_host"], db["ports"]["mmr2"]),
            "mmr1_s1": ("test_mmr1_s1", db["mmr_pg_user"], db["mmr_host"], db["ports"]["mmr1_standby1"]),
            "mmr2_s1": ("test_mmr2_s1", db["mmr_pg_user"], db["mmr_host"], db["ports"]["mmr2_standby1"]),
        }
        pgdata_name, user, host, port = nodes[node]
        psql_cmd = '%s/bin/psql -h 127.0.0.1 -p %s -U %s -d %s -c "%s"' % (
            pg_dir, port, user, dbname, sql.replace('"', '\\"')
        )
        result = self._pg_runner.run_remote(user, host, psql_cmd, "pg_sql_%s_%02d.log" % (node, self._command_no), check=False)
        self.record_step("PG 节点执行 SQL: %s (%s)" % (node, sql), command=psql_cmd,
                         expected="SQL 执行成功",
                         actual=(result.stdout + result.stderr).strip() or "<empty>",
                         result="PASS" if result.returncode == 0 else "FAIL")
        return result.stdout

    def rebuild_pg_standby(self, node="mmr2_s1"):
        """Rebuild a promoted standby replica back into streaming replication."""
        db = self.env.config["database"]
        pg_dir = db["mmr_postgres_dir"]
        data_root = db.get("mmr_data_root", db["mmr_postgres_dir"])
        user = db["mmr_pg_user"]
        host = db["mmr_host"]

        configs = {
            "mmr2_s1": {
                "dir_name": "test_mmr2_s1",
                "primary_port": db["ports"]["mmr2"],
                "standby_port": db["ports"]["mmr2_standby1"],
                "app_name": "pg_250",
            },
            "mmr1_s1": {
                "dir_name": "test_mmr1_s1",
                "primary_port": db["ports"]["mmr1"],
                "standby_port": db["ports"]["mmr1_standby1"],
                "app_name": "pg_240",
            },
        }
        cfg = configs[node]
        standby_dir = "%s/%s" % (data_root, cfg["dir_name"])
        script = (
            '{pg_dir}/bin/pg_ctl -D {standby_dir} stop -m immediate || true; '
            'rm -rf {standby_dir}; mkdir -p {standby_dir}; chmod 700 {standby_dir}; '
            '{pg_dir}/bin/pg_basebackup -h 127.0.0.1 -U replicator -p {primary_port} -w -F p -P -X stream -R -D {standby_dir}; '
            'sed -i "/^primary_conninfo = / s/\'$/ application_name={app_name}\'/" {standby_dir}/postgresql.auto.conf; '
            'echo "port={standby_port}" >> {standby_dir}/postgresql.conf; '
            '{pg_dir}/bin/pg_ctl -D {standby_dir} -l {standby_dir}/logfile start'
        ).format(
            pg_dir=pg_dir,
            standby_dir=standby_dir,
            primary_port=cfg["primary_port"],
            standby_port=cfg["standby_port"],
            app_name=cfg["app_name"],
        )
        result = self._pg_runner.run_remote(user, host, script, "pg_rebuild_%s.log" % node, check=False)
        self.record_step("重建并恢复 PG 备库: %s" % node, command=script,
                         expected="备库重建并恢复流复制成功",
                         actual=result.stdout.strip() or "<empty>",
                         result="PASS" if result.returncode == 0 else "FAIL")
        if result.returncode != 0:
            raise HandoverFailure("rebuild standby %s failed" % node)

    def _coverage_for_step(self, step_number, title):
        """Return the document-content association for one rendered step.

        Old reports had only a case-level source heading.  Resolve metadata
        here, after legacy and phased-JDBC steps have been merged, so the
        mapping always describes the report the reader is looking at.
        """
        if self.case.step_mapping and step_number <= len(self.case.step_mapping):
            _, content, check = self.case.step_mapping[step_number - 1]
            return content, check
        for expression, content, check in getattr(self.case, "step_rules", ()):
            if re.search(expression, title, re.IGNORECASE):
                return content, check
        
        # Robust fallback: associate with general document test content instead of crashing
        notes_count = len(getattr(self.case, "notes", ())) or 1
        return "1 至 %d" % notes_count, "执行文档对应小节的业务时序与验证步骤"

    def _document_steps(self):
        document_steps = []
        timeline = []
        for item in self.steps:
            timeline.append((item.get("order", 0), "legacy", item))
        for item in self.step_journal.steps:
            timeline.append((item.get("order", 0), "evidence", item))
        for _, kind, item in sorted(timeline, key=lambda row: row[0]):
            if kind == "legacy":
                details = list(item["details"])
                if item["command"]:
                    details.insert(0, ("执行命令", item["command"]))
                document_steps.append(ReportStep(
                    item["title"], details=details, expected=item["expected"],
                    actual=item["actual"], result=item["result"],
                ))
                continue
            transport_only = is_transport_only_success(
                item.get("expected"), item.get("result"),
            )
            document_steps.append(ReportStep(
                item["title"], execution=item.get("execution", []),
                intermediate=item.get("intermediate", []), evidence=item.get("evidence", []),
                key_expected=None if transport_only else item.get("expected") or None,
                actual=None if transport_only else item.get("actual") or None,
                result=None if transport_only else item.get("result") or None,
            ))
        for step_number, step in enumerate(document_steps, 1):
            step.coverage, step.coverage_check = self._coverage_for_step(
                step_number, step.title,
            )
        return document_steps

    def _write_report(self, status, reason=None):
        finished_at = self.finished_at or datetime.now()
        document_steps = []
        document_steps.extend(self._document_steps())
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
                    document_steps.append(ReportStep(
                        "🚨 进程崩溃诊断 (Crash Forensics)",
                        execution=["gdb --batch -ex 'bt full' [binary] [core]"],
                        intermediate=[
                            "崩溃信号: %s" % (crash_info.get("signal") or "异常终止"),
                            "Core 文件: %s" % (crash_info.get("core_file") or "未定位到 core 文件"),
                            "\n%s" % (bt or "无调用栈信息"),
                        ],
                        expected="fbasecman 稳定运行，无段错误或异常信号退出",
                        actual="检测到异常崩溃退出并抓取到现场",
                        result="FAIL",
                        coverage="崩溃诊断",
                        coverage_check="自动捕获 Core Dump 与 GDB 调用栈",
                    ))
            except Exception as crash_err:
                self.trace("[crash] failed to diagnose crash: %s" % crash_err)

        if self.checks:
            document_steps.append(ReportStep(
                "产品行为检测", checks=self.checks,
                coverage="1 至 %d" % len(self.case.notes),
                coverage_check="汇总各文档测试内容的业务断言",
            ))
        resolved_mapping = [
            (str(index), step.coverage, step.coverage_check)
            for index, step in enumerate(document_steps, 1)
        ]
        document = ReportDocument(
            self.case.target, status,
            self.started_at.strftime("%Y-%m-%d %H:%M:%S"), finished_at.strftime("%Y-%m-%d %H:%M:%S"),
            self.case.summary,
            config_lines=["来源章节: %s" % ", ".join(self.case.source_sections),
                          "拓扑: %s" % (self.case.topology or "独立配置"),
                          "读写模式: %s" % (self.case.route_mode or "不适用")],
            coverage_items=list(self.case.notes), coverage_mapping=resolved_mapping,
            steps=document_steps,
            pass_reason=reason if status == "PASS" else None,
            failure_reason=reason if status == "FAIL" else None,
        )
        atomic_write_text(self.run_root / "report.txt", render_report(document))
        return document

    def finish(self, status, reason=None):
        self.finished_at = datetime.now()
        document = self._write_report(status, reason)
        _json_write(self.run_root / "summary.json", {
            "target": self.case.target, "status": status, "reason": reason,
            "source_sections": self.case.source_sections,
            "started_at": document.started_at, "finished_at": document.finished_at,
        })
        self.stop()
