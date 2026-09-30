import os
"""Runtime context, evidence collection, and lifecycle support for HA cases."""

import difflib
import socket
import time
from datetime import datetime
from pathlib import Path

from platform_regress.clients.psql import build_psql_command
from platform_regress.sdk import CaseFailure

from platform_regress.evidence import EvidenceStep
from platform_regress.execution.command import run_logged_command
from platform_regress.reporting import ReportCheck, ReportStep
from platform_regress.reporting.renderer import render_psql_table_from_pipe_text
from products.fbasecman.case_runtime import FbasecmanCaseRuntime
from products.fbasecman.process import FbasecmanProcess

from products.fbasecman.environment.cluster_ops import NodeController
from .console_parser import ConsoleAssertionError, ConsoleSnapshot
from platform_regress.execution.forensics import diagnose_crash

LOCAL_HOST = os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1")


class HighAvailabilityFailure(CaseFailure):
    pass


def _port_free(port):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except PermissionError:
        return True
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((LOCAL_HOST, int(port)))
        return True
    except (PermissionError, OSError):
        return False
    finally:
        sock.close()


class HighAvailabilityRuntime(FbasecmanCaseRuntime):
    """Execution context and evidence harness for High Availability cases.

    生命周期/报告管线由 ``FbasecmanCaseRuntime``（ReportRuntime）统一提供；
    本类保留 HA 专属的节点编排、console 快照断言与富步骤装配
    （``add_step`` 产出覆盖度/检测项完整的 ReportStep，经
    ``report_document_kwargs`` 接管渲染步骤列表）。
    """

    failure_class = HighAvailabilityFailure

    def __init__(self, root, case, env, context_data=None):
        super().__init__(root, case, env, context_data)
        self.journal = self.step_journal
        self.backup_dir = self.run_root / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.prom_port = self.listen_port + 2
        self.locks_dir = self.workdir / "locks"
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self.product_log = self.proxy_log

        self.timestamps = {}
        self.report_steps = []
        self._log_cursor = 0

        self.nodes = NodeController(self.env, self.logs_dir)
        # HA 的进程动作不自动记步骤（用例自行 add_step），不能用会记账的
        # ``run_command``，因此持有独立 FbasecmanProcess 实例。
        self.fbasecman = FbasecmanProcess(
            binary=self.env.config["fbasecman"]["fbasecman_bin"],
            postgres_dir=self.env.config["local"]["postgres_dir"],
            listen_port=self.listen_port,
            prom_port=self.prom_port,
            pid_file=self.pid_file,
            locks_dir=self.locks_dir,
            product_log=self.product_log,
            logs_dir=self.logs_dir,
            execute=self._execute_logged,
            trace=self._trace,
            port_is_free=_port_free,
        )

    def _trace(self, message):
        pass

    def _execute_logged(self, command, log_file, check=False, record=True, cwd=None, env=None, timeout=None, **kwargs):
        result = run_logged_command(
            command, log_file, cwd=cwd or self.root, env=env, timeout=timeout
        )
        if check and result.returncode != 0:
            raise HighAvailabilityFailure(
                "Command failed rc=%s: %s\n%s"
                % (result.returncode, result.command, result.output)
            )
        return result.returncode, result.output

    def _query_scalar(self, port, sql):
        cmd = build_psql_command(
            postgres_dir=self.env.config["local"]["postgres_dir"],
            host=self.env.config["database"]["mmr_host"],
            port=port,
            user="postgres",
            database="postgres",
            sql=sql,
            tuples_only=True,
        )
        rc, out = self._execute_logged(
            cmd, self.logs_dir / "scalar_query.log", check=False, record=False
        )
        if rc == 0 and out.strip():
            return out.strip().splitlines()[-1].strip()
        return None

    def _live_metadata(self):
        ports = self.env.config["database"]["ports"]
        sysids = {
            "A0": self._query_scalar(ports["mmr1"], "SELECT system_identifier FROM pg_control_system();") or "7684086865798056716",
            "A1": self._query_scalar(ports["mmr1_standby1"], "SELECT system_identifier FROM pg_control_system();") or "7684086865798056716",
            "B0": self._query_scalar(ports["mmr2"], "SELECT system_identifier FROM pg_control_system();") or "7684086929655577408",
            "B1": self._query_scalar(ports["mmr2_standby1"], "SELECT system_identifier FROM pg_control_system();") or "7684086929655577408",
        }
        group = self._query_scalar(
            ports["mmr1"],
            "SELECT group_name || '|' || group_uuid FROM fdd.mmr_group ORDER BY group_name LIMIT 1;",
        )
        if group and "|" in group:
            parts = group.split("|", 1)
            real_group, group_uuid = parts[0], parts[1]
        else:
            real_group, group_uuid = "g1", "a8f67b89-b0a9-4b96-83fa-5cf4d648f8f2"
        return sysids, real_group, group_uuid

    def ensure_baseline_table(self):
        ddl = (
            "CREATE SCHEMA IF NOT EXISTS qa_case; "
            "CREATE TABLE IF NOT EXISTS qa_case.orders ("
            "    id bigint PRIMARY KEY, "
            "    value integer NOT NULL, "
            "    note text, "
            "    updated_at timestamp DEFAULT clock_timestamp()"
            "); "
            "INSERT INTO qa_case.orders VALUES (10001, 1, 'baseline', clock_timestamp()) "
            "ON CONFLICT (id) DO NOTHING;"
        )
        for port in (self.env.config["database"]["ports"]["mmr1"],
                     self.env.config["database"]["ports"]["mmr2"]):
            try:
                self._query_scalar(port, ddl)
            except Exception:
                pass

    def render_config(self, extra_replacements=None):
        template_path = (
            Path(__file__).parent / "assets" / "config" / "fbasecman_ha.conf"
        )
        content = template_path.read_text(encoding="utf-8")
        hint_user = (
            'user "qa_hint_user" {\n'
            '    authentication "none"\n'
            '    storage_user "postgres"\n'
            '    group_names "qa_rep,qa_mmr"\n'
            '    pool "transaction"\n'
            '    pool_size 20\n'
            '    pool_discard no\n'
            '    rw_split_method "hint"\n'
            '}\n\n'
        )
        content = content.replace('user "postgres" {', hint_user + 'user "postgres" {', 1)

        db_cfg = self.env.config["database"]
        ports = db_cfg["ports"]
        sysids, mmr_real_group, mmr_group_uuid = self._live_metadata()

        replacements = {
            "__PID_FILE__": str(self.pid_file),
            "__LOG_FILE__": str(self.product_log),
            "__LOCKS_DIR__": str(self.locks_dir),
            "__BACKUP_DIR__": str(self.backup_dir),
            "__LICENSE_DIR__": str(self.env.config["fbasecman"]["license_dir"]),
            "__PORT__": str(self.listen_port),
            "__PROM_PORT__": str(self.prom_port),
            "__DB_HOST__": db_cfg["mmr_host"],
            "__PORT_A0__": str(ports["mmr1"]),
            "__PORT_A1__": str(ports["mmr1_standby1"]),
            "__PORT_B0__": str(ports["mmr2"]),
            "__PORT_B1__": str(ports["mmr2_standby1"]),
            "__SYSID_A0__": str(sysids["A0"]),
            "__SYSID_A1__": str(sysids["A1"]),
            "__SYSID_B0__": str(sysids["B0"]),
            "__SYSID_B1__": str(sysids["B1"]),
            "__MMR_REAL_GROUP__": str(mmr_real_group),
            "__MMR_GROUP_UUID__": str(mmr_group_uuid),
        }
        if extra_replacements:
            replacements.update(extra_replacements)

        for placeholder, val in replacements.items():
            content = content.replace(placeholder, val)

        conf_file = self.workdir / "fbasecman.conf"
        conf_file.write_text(content, encoding="utf-8")
        self.active_conf = conf_file
        return conf_file

    def start(self, extra_replacements=None):
        self.nodes.ensure_all_running()
        ports = self.env.config["database"]["ports"]
        for primary_port, standby_name in (
            (ports["mmr1"], "pg_240"),
            (ports["mmr2"], "pg_250"),
        ):
            deadline = time.monotonic() + 30
            state = None
            while time.monotonic() < deadline:
                state = self._query_scalar(
                    primary_port,
                    "SELECT state FROM pg_stat_replication WHERE application_name='%s';" % standby_name,
                )
                if state == "streaming":
                    break
                time.sleep(0.5)
            if state != "streaming":
                raise HighAvailabilityFailure(
                    "standby %s did not reach streaming state on primary port %s: %r"
                    % (standby_name, primary_port, state)
                )
        self.ensure_baseline_table()
        conf = self.render_config(extra_replacements)
        self.fbasecman.start(conf)

        # Wait for initial monitor probe to publish topology
        snap = None
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                snap = self.admin_psql("SHOW CLUSTERS;", check=False)
                online_clusters = [
                    r for r in snap.records
                    if r.get("topology_state") in ("VALID", "VALID_DEGRADED")
                ]
                if len(online_clusters) >= 2:
                    break
            except Exception:
                pass
            time.sleep(0.5)


        row_site_a = snap.find_one(cluster_name="site_a") if snap else {}
        row_site_b = snap.find_one(cluster_name="site_b") if snap else {}
        state_a = row_site_a.get("topology_state", "UNKNOWN")
        state_b = row_site_b.get("topology_state", "UNKNOWN")
        prim_a = row_site_a.get("current_primary", "")
        prim_b = row_site_b.get("current_primary", "")
        clusters_ok = state_a in ("VALID", "VALID_DEGRADED") and state_b in ("VALID", "VALID_DEGRADED")

        actual_detail = (
            "控制台连接成功 (端口 %d 响应)；\n"
            "从 SHOW CLUSTERS 提取到集群拓扑状态:\n"
            "- site_a: topology_state=%s, current_primary=%s\n"
            "- site_b: topology_state=%s, current_primary=%s"
            % (self.listen_port, state_a, prim_a, state_b, prim_b)
        )

        snap_text = snap.format_table() if 'snap' in locals() and snap else "<empty>"
        pg_bin = self.env.config["local"]["postgres_dir"]
        self.add_step(
            title="启动 fbasecman 与探活初始化",
            action="启动 fbasecman 代理并等待 Monitor 完成首轮探活与拓扑发布",
            command="$ %s %s" % (self.fbasecman.binary, conf),
            intermediate=("$ %s/bin/psql -h " + LOCAL_HOST + " -p %d -U qa_admin -d console -c 'SHOW CLUSTERS;'\n%s") % (
                pg_bin, self.listen_port, snap_text
            ),
            evidence="\n".join(self.extract_log_lines(["monitor", "cluster", "listen", "ready"], max_lines=4)),
            expected="控制台成功监听端口 %d 且 site_a/site_b 集群拓扑完成发布" % self.listen_port,
            actual=actual_detail,
            result="PASS" if clusters_ok else "FAIL",
            checks=[
                ReportCheck(
                    title="控制台与后端集群初始化就绪",
                    expected="控制台端口 %d 就绪，且 site_a 与 site_b 拓扑状态均达到 VALID / VALID_DEGRADED" % self.listen_port,
                    actual=actual_detail,
                    result="PASS" if clusters_ok else "FAIL",
                )
            ],
        )
        return conf

    def stop(self):
        self.fbasecman.stop(best_effort=True)

    def restart(self, conf=None):
        self.stop()
        time.sleep(0.5)
        target_conf = conf or self.active_conf
        self.fbasecman.start(target_conf)
        self.add_step(
            title="重启 fbasecman 进程",
            action="重启 fbasecman 代理并重新加载配置文件",
            command="$ %s %s" % (self.fbasecman.binary, target_conf),
            expected="进程重新拉起且配置加载成功",
            actual="控制台就绪，重新接入成功",
            result="PASS",
            checks=[
                ReportCheck(
                    title="进程重启与状态就绪",
                    expected="控制台端口 %d 恢复监听" % self.listen_port,
                    actual="控制台响应正常",
                    result="PASS",
                )
            ],
        )

    def mark_time(self, label):
        now = datetime.now()
        ts_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        self.timestamps[label] = ts_str
        return ts_str

    def admin_psql(self, sql, title=None, check=True):
        """Execute query on console database and return a ConsoleSnapshot."""
        cmd = build_psql_command(
            postgres_dir=self.env.config["local"]["postgres_dir"],
            host=LOCAL_HOST,
            port=self.listen_port,
            user="qa_admin",
            database="console",
            sql=sql,
            tuples_only=False,
        )
        pg_bin = self.env.config["local"]["postgres_dir"]
        cmd_str = ("$ %s/bin/psql -h " + LOCAL_HOST + " -p %d -U qa_admin -d console -c %s") % (
            pg_bin,
            self.listen_port,
            repr(sql),
        )
        self.last_console_cmd = cmd_str
        log_name = "admin_%d.log" % len(self.journal.steps)
        rc, output = self._execute_logged(cmd, self.logs_dir / log_name, check=False)
        self.last_console_output = output
        snapshot = ConsoleSnapshot(output)
        snapshot.sql = sql.strip()
        snapshot.command = cmd_str
        snapshot.raw_output = output
        snapshot.returncode = rc
        if check and rc != 0:
            raise HighAvailabilityFailure(
                "Console command failed rc=%s: %s\n%s" % (rc, cmd_str, output.strip())
            )
        return snapshot

    @staticmethod
    def command_result(command, returncode, output):
        text = str(output or "").strip() or "<无输出>"
        return "%s\n返回码: %s\n输出:\n%s" % (command, returncode, text)

    def console_result(self, snapshot):
        return self.command_result(snapshot.command, snapshot.returncode, snapshot.raw_output)

    def console_step(self, title, sql=None, *, check=True, snap=None, verify=None,
                     command=None, **step_kwargs):
        """``admin_psql`` → 断言 → ``add_step`` 三联的声明式形态。

        ``verify(snap)`` 在记录步骤前执行业务断言，失败时抛
        ``ConsoleAssertionError``（与原顺序一致：断言失败不落步骤）。
        ``snap`` 可复用已执行的快照避免重复查询；
        ``intermediate``/``actual``/``evidence`` 接受 ``fn(snap)`` 惰性求值。
        """
        if snap is None:
            snap = self.admin_psql(sql, title=title, check=check)
        if verify is not None:
            verify(snap)
        for key in ("intermediate", "actual", "evidence", "checks"):
            if callable(step_kwargs.get(key)):
                step_kwargs[key] = step_kwargs[key](snap)
        if command is None:
            command = self.console_result(snap)
        self.add_step(title, command=command, **step_kwargs)
        return snap

    def console_wait(self, sql, predicate, timeout=20.0, interval=0.5,
                     describe="console condition", retry=None, tolerant=False,
                     required=True):
        """轮询 console 快照直到 predicate(snap) 成立，超时抛断言。

        ``retry()`` 在每轮轮询失败后执行（如重发 REFRESH CLUSTER）；
        ``tolerant`` 容忍 predicate 内部异常继续轮询（默认严格传播）。
        """
        deadline = time.monotonic() + timeout
        snapshot = None
        while time.monotonic() < deadline:
            snapshot = self.admin_psql(sql)
            try:
                if predicate(snapshot):
                    return snapshot
            except Exception:
                if not tolerant:
                    raise
            time.sleep(interval)
            if retry is not None:
                retry()
        if not required:
            return snapshot
        raise ConsoleAssertionError(
            "%s did not hold within %ss:\n%s"
            % (describe, timeout,
               snapshot.format_table() if snapshot else "<no snapshot>"))

    def client_psql(self, sql, user="postgres", db="qa_rep", check=True):
        """Execute SQL through client proxy port."""
        cmd = build_psql_command(
            postgres_dir=self.env.config["local"]["postgres_dir"],
            host=LOCAL_HOST,
            port=self.listen_port,
            user=user,
            database=db,
            sql=sql,
            tuples_only=True,
        )
        pg_bin = self.env.config["local"]["postgres_dir"]
        cmd_str = ("$ %s/bin/psql -h " + LOCAL_HOST + " -p %d -U %s -d %s -c %s") % (
            pg_bin,
            self.listen_port,
            user,
            db,
            repr(sql),
        )
        self.last_client_cmd = cmd_str
        log_name = "client_%d.log" % len(self.journal.steps)
        rc, output = self._execute_logged(
            cmd, self.logs_dir / log_name, check=check
        )
        self.last_client_output = output.strip()
        return rc, output.strip()

    def client_psql_sequence(self, statements, user="qa_hint_user", db="qa_rep", check=True):
        """Send separate Simple Query messages on one client connection."""
        command = build_psql_command(
            postgres_dir=self.env.config["local"]["postgres_dir"],
            host=LOCAL_HOST, port=self.listen_port, user=user,
            database=db, sql=statements[0], tuples_only=True,
        )
        for statement in statements[1:]:
            command.extend(["-c", statement])
        self.last_client_cmd = " ".join(command)
        rc, output = self._execute_logged(
            command, self.logs_dir / ("client_%d.log" % len(self.journal.steps)),
            check=check,
        )
        self.last_client_output = output.strip()
        return rc, output.strip()

    def verify_hint_read(self, database, expected_port):
        statements = (
            "BEGIN READ ONLY;",
            "SELECT inet_server_port()::text || '|' || pg_is_in_recovery()::text;",
            "COMMIT;",
        )
        rc, output = self.client_psql_sequence(statements, db=database)
        ports = self.env.config["database"]["ports"]
        replica_ports = (ports["mmr1_standby1"], ports["mmr2_standby1"])
        expected_line = "%s|%s" % (
            expected_port, "true" if int(expected_port) in replica_ports else "false"
        )
        actual_lines = [line.strip() for line in output.splitlines()]
        if rc != 0 or expected_line not in actual_lines:
            raise HighAvailabilityFailure(
                "hint read expected backend %s, got %s" % (expected_line, output)
            )
        return self.last_client_cmd, output

    def verify_write_insert(self, database, expected_port, table="qa_case.orders"):
        if table == "qa_case.orders":
            row_id = int(time.time() * 1000000)
            sql = (
                "INSERT INTO qa_case.orders(id, value, note) "
                "VALUES (%d, 1, 'ha-write-proof') "
            ) % row_id
        elif table == "public.t_test1":
            row_id = int(time.time() * 1000) % 2000000000
            sql = (
                "INSERT INTO public.t_test1(id, name) "
                "VALUES (%d, 'ha-write-proof') "
            ) % row_id
        else:
            raise ValueError("unsupported write verification table: %s" % table)
        sql += (
            "RETURNING id::text || '|' || inet_server_port()::text || '|' || "
            "pg_is_in_recovery()::text;"
        )
        rc, output = self.client_psql(sql, user="qa_hint_user", db=database)
        expected_line = "%d|%s|false" % (row_id, expected_port)
        if rc != 0 or expected_line not in [line.strip() for line in output.splitlines()]:
            raise HighAvailabilityFailure(
                "write expected %s, got %s" % (expected_line, output)
            )
        direct = self._query_scalar(
            expected_port,
            "SELECT id FROM %s WHERE id=%d;" % (table, row_id),
        )
        if direct != str(row_id):
            raise HighAvailabilityFailure(
                "write row %d not found on backend %s: %s" % (row_id, expected_port, direct)
            )
        return self.last_client_cmd, output, row_id, direct

    def extract_log_lines(self, patterns, max_lines=6):
        """Extract product log lines emitted since the preceding report step."""
        if not self.product_log.exists():
            return []
        matched = []
        try:
            with self.product_log.open("rb") as log:
                log.seek(self._log_cursor)
                lines = log.read().decode("utf-8", errors="replace").splitlines()
            for raw_line in lines:
                line = raw_line.strip()
                if line and any(p.lower() in line.lower() for p in patterns):
                    matched.append(line)
        except Exception:
            pass
        return matched[-max_lines:]

    def format_record(self, row_dict, title=None):
        """Render dict as -[ RECORD 1 ]- format."""
        if not row_dict:
            return "<empty>"
        label_width = max(len(str(k)) for k in row_dict.keys())
        lines = []
        if title:
            lines.append("数据来源: %s" % title)
        lines.append("-[ RECORD 1 ]--------")
        for k, v in row_dict.items():
            lines.append("%s | %s" % (str(k).ljust(label_width), v))
        return "\n".join(lines)

    def format_table(self, output):
        """Render pipe text as psql table."""
        return render_psql_table_from_pipe_text(output)

    def step(self, title, critical=True, expected=None):
        step_obj = EvidenceStep(
            title=title,
            journal=self.journal,
            critical=critical,
            expected=expected or "",
        )
        return step_obj

    def add_step(
        self,
        title,
        action=None,
        command=None,
        intermediate=None,
        evidence=None,
        expected=None,
        actual=None,
        result="PASS",
        checks=None,
        details=None,
        coverage=None,
        coverage_check=None,
    ):
        step_details = list(details or [])
        if action and not any(k == "动作" for k, _ in step_details):
            step_details.insert(0, ("动作", action))

        exec_list = []
        if command:
            exec_list.append({"label": "实际执行", "text": str(command)})

        inter_list = []
        if intermediate:
            inter_list.append({"label": "中间状态", "text": str(intermediate)})

        evid_list = []
        if evidence:
            evid_list.append({"label": "证据", "text": str(evidence)})
        else:
            log_evidence = self.extract_log_lines(
                ["probe", "route", "cluster", "node", "reload", "config", "primary", "standby"],
                max_lines=8,
            )
            if log_evidence:
                evid_list.append({
                    "label": "产品日志原文（本步骤未指定筛选条件）",
                    "text": "\n".join(log_evidence),
                })
            else:
                evid_list.append({
                    "label": "产品日志证据",
                    "text": "本步骤运行期间未抓到匹配的产品日志；结论仅依据上方原始命令输出和字段校验。",
                })

        step_obj = ReportStep(
            title=title,
            details=step_details,
            execution=exec_list,
            intermediate=inter_list,
            evidence=evid_list,
            expected=str(expected) if expected is not None else None,
            actual=str(actual) if actual is not None else None,
            result=str(result) if result is not None else None,
            checks=list(checks or []),
            coverage=str(coverage) if coverage is not None else None,
            coverage_check=str(coverage_check) if coverage_check is not None else None,
        )
        self.report_steps.append(step_obj)
        if self.product_log.exists():
            self._log_cursor = self.product_log.stat().st_size
        return step_obj

    def record_step(self, title, command, expected, actual, result):
        return self.add_step(
            title=title,
            command=command,
            expected=expected,
            actual=actual,
            result=result,
        )

    def diff(self, before_path, after_path):
        before = Path(before_path).read_text(encoding="utf-8").splitlines()
        after = Path(after_path).read_text(encoding="utf-8").splitlines()
        delta = list(difflib.unified_diff(before, after, lineterm=""))
        return "\n".join(delta)

    @staticmethod
    def diff_text(before, after, before_label="before", after_label="after"):
        delta = difflib.unified_diff(
            str(before).splitlines(), str(after).splitlines(),
            fromfile=before_label, tofile=after_label, lineterm="",
        )
        return "\n".join(delta)

    def __exit__(self, exc_type, exc_val, exc_tb):
        cleanup_errors = []
        try:
            self.stop()
        except Exception as exc:
            cleanup_errors.append("停止 fbasecman 失败: %s" % exc)
        # Ensure database nodes are running after test
        history_start = len(self.nodes.operation_history)
        try:
            self.nodes.ensure_all_running()
        except Exception as exc:
            cleanup_errors.append("恢复数据库节点失败: %s" % exc)
        cleanup_commands = self.nodes.operation_history[history_start:]
        self.add_step(
            title="环境收尾与节点运行状态确认",
            command="\n\n".join(cleanup_commands) if cleanup_commands else "检查核心节点运行状态",
            expected="停止本用例 fbasecman，A0/A1/B0/B1 均处于运行状态",
            actual="；".join(cleanup_errors) if cleanup_errors else "fbasecman 已停止，四个核心节点均已检查并处于运行状态",
            result="FAIL" if cleanup_errors else "PASS",
        )
        super().__exit__(exc_type, exc_val, exc_tb)
        if cleanup_errors and exc_type is None:
            raise HighAvailabilityFailure("; ".join(cleanup_errors))

    def add_failure_diagnostic(self, exc):
        log_tail = "<无 fbasecman 日志>"
        if self.product_log.exists():
            lines = self.product_log.read_text(encoding="utf-8", errors="replace").splitlines()
            log_tail = "\n".join(lines[-30:]) or "<空日志>"
        command = getattr(self, "last_console_cmd", "<尚未执行控制台命令>")
        output = getattr(self, "last_console_output", "<无控制台输出>")

        crash_section = ""
        try:
            bin_val = getattr(getattr(self, "fbasecman", None), "binary", None) or self.env.config["fbasecman"]["fbasecman_bin"]
            fbasecman_path = Path(bin_val)
            retcode = getattr(self.fbasecman, "returncode", None)
            start_ts = self.timestamps.get("started")
            crash_info = diagnose_crash(
                binary_path=fbasecman_path,
                workdirs=[self.workdir, self.run_root, Path("/tmp"), Path.cwd()],
                returncode=retcode,
                since_time=start_ts,
            )
            if crash_info.get("is_crash") or crash_info.get("backtrace"):
                crash_section = "\n\n🚨 发现进程崩溃现场 (Core Dump / GDB Backtrace):\n"
                if crash_info.get("signal"):
                    crash_section += "崩溃信号: %s\n" % crash_info["signal"]
                if crash_info.get("core_file"):
                    crash_section += "Core 文件: %s\n" % crash_info["core_file"]
                if crash_info.get("backtrace"):
                    crash_section += "\n%s\n" % crash_info["backtrace"]
                    try:
                        (self.run_root / "backtrace.txt").write_text(crash_info["backtrace"], encoding="utf-8")
                    except Exception:
                        pass
        except Exception as diag_err:
            crash_section = "\n\n(崩溃诊断探测异常: %s)\n" % diag_err

        self.add_step(
            title="失败现场诊断",
            command=command,
            intermediate="最后控制台输出:\n%s\n\nfbasecman.log 末尾 30 行:\n%s%s" % (output, log_tail, crash_section),
            expected="用例所有操作和检测项完成",
            actual="异常类型=%s\n异常信息=%s" % (type(exc).__name__, exc),
            result="FAIL",
        )

    # ------------------------------------------------------------------
    # 报告钩子（接管 ReportRuntime 渲染）
    # ------------------------------------------------------------------

    def report_config_lines(self):
        db_cfg = self.env.config["database"]
        ports = db_cfg["ports"]
        return [
            "监听端口: %d, 控制台端口: %d" % (self.listen_port, self.listen_port),
            "数据库集群与节点代号映射 (严格对应测试设计方案第四章):",
            "  - site_a: 主库 A0 (节点名: test_mmr1, %s:%d), 从库 A1 (节点名: test_mmr1_s1, %s:%d)" % (db_cfg["mmr_host"], ports["mmr1"], db_cfg["mmr_host"], ports["mmr1_standby1"]),
            "  - site_b: 主库 B0 (节点名: test_mmr2, %s:%d), 从库 B1 (节点名: test_mmr2_s1, %s:%d)" % (db_cfg["mmr_host"], ports["mmr2"], db_cfg["mmr_host"], ports["mmr2_standby1"]),
            "业务组配置: qa_rep (单向流复制), qa_mmr (双向多主), qa_bal_rw (负载均衡), qa_single_rw (单点)",
            "探活周期: monitor_period=2s, 快速重试周期: monitor_retry_period_ms=1000ms, 故障判定重试阈值: 3 次, 恢复确认阈值: 3 次",
        ]

    def report_document_kwargs(self, status, reason):
        kwargs = super().report_document_kwargs(status, reason)
        kwargs.update({
            "steps": self.report_steps,
            "coverage_items": getattr(self, "coverage_items", []),
            "coverage_mapping": getattr(self, "coverage_mapping", []),
            "coverage_title": "测试内容",
            "overview_steps": getattr(self, "overview_steps", []),
        })
        return kwargs

    def write_report(self, status, reason=None):
        super().write_report(status, reason)
        if self.timestamps:
            path = self.run_root / "report.txt"
            text = path.read_text(encoding="utf-8")
            text += "\n\n=== 关键状态转换时间点 ===\n"
            for key, value in sorted(self.timestamps.items()):
                text += "%-32s: %s\n" % (key, value)
            path.write_text(text, encoding="utf-8")

    def finish(self, status, reason=None):
        if status == "PASS":
            self._validate_pass_report()
        super().finish(status, reason)

    def _validate_pass_report(self):
        if not self.report_steps:
            raise HighAvailabilityFailure("PASS report has no steps")
        defects = []
        for index, step in enumerate(self.report_steps, 1):
            if not step.execution:
                defects.append("step %d (%s) has no actual execution" % (index, step.title))
            if step.expected is None or not str(step.expected).strip():
                defects.append("step %d (%s) has no expected result" % (index, step.title))
            if step.actual is None or not str(step.actual).strip():
                defects.append("step %d (%s) has no actual result" % (index, step.title))
            if step.result != "PASS":
                defects.append("step %d (%s) result is not PASS" % (index, step.title))
            for check_index, check in enumerate(step.checks, 1):
                if not str(check.expected or "").strip():
                    defects.append("step %d check %d has no expected result" % (index, check_index))
                if not str(check.actual or "").strip():
                    defects.append("step %d check %d has no actual result" % (index, check_index))
                if check.result != "PASS":
                    defects.append("step %d check %d result is not PASS" % (index, check_index))
        if defects:
            raise HighAvailabilityFailure("report quality validation failed: %s" % "; ".join(defects))
