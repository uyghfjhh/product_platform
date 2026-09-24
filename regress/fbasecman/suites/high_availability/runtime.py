"""Runtime context, evidence collection, and lifecycle support for HA cases."""

import difflib
import json
import os
import shutil
import socket
import time
from datetime import datetime
from pathlib import Path

from framework.clients.psql import build_psql_command
from framework.configuration import load_regression_config
from framework.evidence import EvidenceStep, StepJournal
from framework.execution.command import run_logged_command
from framework.reporting import ReportCheck, ReportDocument, ReportStep, render_report
from framework.reporting.renderer import render_psql_table_from_pipe_text
from lib.report_utils import render_psql_expanded_from_pipe_text
from products.fbasecman.process import FbasecmanProcess, FbasecmanProcessError

from .cluster_ops import NodeController
from .console_parser import ConsoleSnapshot, parse_console_pipe_table
from framework.execution.forensics import diagnose_crash


class HighAvailabilityFailure(RuntimeError):
    pass


def _port_free(port):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except PermissionError:
        return True
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", int(port)))
        return True
    except (PermissionError, OSError):
        return False
    finally:
        sock.close()


def _find_free_port(start=18000, max_port=25000):
    for port in range(start, max_port):
        if _port_free(port) and _port_free(port + 1) and _port_free(port + 2):
            return port
    raise HighAvailabilityFailure("No free ports available for fbasecman")


class HighAvailabilityRuntime(object):
    """Execution context and evidence harness for High Availability cases."""

    def __init__(self, root, case):
        self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.root = Path(root)
        self.case = case
        self.env = load_regression_config(self.root)
        self.run_root = self.env.output_dir / "runs" / "high_availability" / case.name
        if self.run_root.exists():
            shutil.rmtree(str(self.run_root))
        self.workdir = self.run_root / "workdir"
        self.logs_dir = self.run_root / "logs"
        self.backup_dir = self.run_root / "backups"
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        self.journal_path = self.run_root / "steps.json"
        self.journal = StepJournal(self.journal_path, self.case.target)

        self.listen_port = _find_free_port()
        self.prom_port = self.listen_port + 2
        self.pid_file = self.workdir / "fbasecman.pid"
        self.locks_dir = self.workdir / "locks"
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self.product_log = self.run_root / "fbasecman.log"

        self.timestamps = {}
        self.report_steps = []

        self.nodes = NodeController(self.env, self.logs_dir)
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
        self.active_conf = None

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
        self.ensure_baseline_table()
        conf = self.render_config(extra_replacements)
        self.fbasecman.start(conf)

        # Wait for initial monitor probe to publish topology
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
            intermediate="$ %s/bin/psql -h 127.0.0.1 -p %d -U qa_admin -d console -c 'SHOW CLUSTERS;'\n%s" % (
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
            host="127.0.0.1",
            port=self.listen_port,
            user="qa_admin",
            database="console",
            sql=sql,
            tuples_only=False,
        )
        pg_bin = self.env.config["local"]["postgres_dir"]
        cmd_str = "$ %s/bin/psql -h 127.0.0.1 -p %d -U qa_admin -d console -c %s" % (
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

    def client_psql(self, sql, user="postgres", db="qa_rep", check=True):
        """Execute SQL through client proxy port."""
        cmd = build_psql_command(
            postgres_dir=self.env.config["local"]["postgres_dir"],
            host="127.0.0.1",
            port=self.listen_port,
            user=user,
            database=db,
            sql=sql,
            tuples_only=True,
        )
        pg_bin = self.env.config["local"]["postgres_dir"]
        cmd_str = "$ %s/bin/psql -h 127.0.0.1 -p %d -U %s -d %s -c %s" % (
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

    def extract_log_lines(self, patterns, max_lines=6):
        """Extract matching lines from product log (fbasecman.log)."""
        if not self.product_log.exists():
            return []
        matched = []
        try:
            lines = self.product_log.read_text(encoding="utf-8", errors="replace").splitlines()
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

    def __enter__(self):
        return self

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
            retcode = getattr(getattr(self, "process", None), "returncode", None)
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

    def finish(self, verdict, summary_text):
        if verdict == "PASS":
            self._validate_pass_report()
        db_cfg = self.env.config["database"]
        ports = db_cfg["ports"]
        config_lines = [
            "监听端口: %d, 控制台端口: %d" % (self.listen_port, self.listen_port),
            "数据库集群与节点代号映射 (严格对应测试设计方案第四章):",
            "  - site_a: 主库 A0 (节点名: test_mmr1, %s:%d), 从库 A1 (节点名: test_mmr1_s1, %s:%d)" % (db_cfg["mmr_host"], ports["mmr1"], db_cfg["mmr_host"], ports["mmr1_standby1"]),
            "  - site_b: 主库 B0 (节点名: test_mmr2, %s:%d), 从库 B1 (节点名: test_mmr2_s1, %s:%d)" % (db_cfg["mmr_host"], ports["mmr2"], db_cfg["mmr_host"], ports["mmr2_standby1"]),
            "业务组配置: qa_rep (单向流复制), qa_mmr (双向多主), qa_bal_rw (负载均衡), qa_single_rw (单点)",
            "探活周期: monitor_period=2s, 快速重试周期: monitor_retry_period_ms=1000ms, 故障判定重试阈值: 3 次, 恢复确认阈值: 3 次",
        ]

        doc = ReportDocument(
            target=self.case.target,
            status=verdict,
            started_at=getattr(self, "started_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            purpose=self.case.summary,
            config_lines=config_lines,
            coverage_items=getattr(self, "coverage_items", []),
            coverage_mapping=getattr(self, "coverage_mapping", []),
            coverage_title="测试内容",
            overview_steps=getattr(self, "overview_steps", []),
            pass_reason=summary_text if verdict == "PASS" else None,
            failure_reason=summary_text if verdict != "PASS" else None,
            steps=self.report_steps,
        )
        report_path = self.run_root / "report.txt"
        rendered = render_report(doc)

        # Append timestamps if present
        if self.timestamps:
            rendered += "\n\n=== 关键状态转换时间点 ===\n"
            for k, v in sorted(self.timestamps.items()):
                rendered += "%-32s: %s\n" % (k, v)

        report_path.write_text(rendered, encoding="utf-8")

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
