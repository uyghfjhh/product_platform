"""Global-cache run context during product-adapter migration."""

import json
import os
import re
import shlex
import shutil
import socket
import time
from datetime import datetime
from pathlib import Path

import yaml

from framework.configuration import load_regression_config
from framework.execution.command import run_logged_command
from framework.execution.phased_process import PhasedProcess
from framework.evidence import (
    EvidenceStep, StepJournal, render_jdbc_action, without_phase_markers,
)
from framework.evidence.log_window import (
    LocalLogWindow,
    parse_remote_log_snapshot,
    remote_collect_script,
    remote_snapshot_script,
)
from framework.clients.psql import build_psql_command
from products.fbasecman.console import ConsoleQueryError, parse_pipe_rows
from products.fbasecman.config import (
    apply_datasource_runtime,
    extract_config_lines,
    set_or_append_config_line,
)
from products.fbasecman.process import FbasecmanProcess
from framework.execution.shell import quote_arguments
from framework.persistence.atomic import atomic_write_text
from framework.reporting import (
    ReportDocument, ReportStep, is_transport_only_success, render_report,
    render_psql_table_from_pipe_text,
)
from suites.global_cache.state import capture_global_cache_state
from suites.global_cache.errors import GlobalCacheFailure, VerificationFailure
from suites.global_cache.paths import asset_path as global_cache_asset_path
from suites.global_cache.manifest import (
    BACKEND_PS_LIMIT_KEY,
    GLOBAL_CACHE_CASES,
    GLOBAL_PS_LIMIT_KEY,
    NEGATIVE_LOG_PATTERNS,
    NOISE_PATTERNS,
    formal_case_items,
)
from suites.global_cache.reports.runtime import GlobalCacheReportMixin

DEFAULT_FBASECMAN_LOG_LEVEL = os.environ.get("FBASECMAN_LOG_LEVEL", "debug1")
REPORT_FBASECMAN_CONFIG_PREFIXES = (
    "enable_guc_sync",
    "heartbeat_request",
    "global_prepared_statements_limit",
    "backend_prepared_statements_limit",
    "server_lifetime",
    "pool_discard",
    "pool_reserve_prepared_statement",
)

def case_items():
    return list(GLOBAL_CACHE_CASES)


def case_names():
    return [case.name for case in case_items()]


def enabled_case_names():
    return [case.name for case in formal_case_items()]


def advanced_case_names():
    return [case.name for case in formal_case_items() if case.report_level == "advanced"]


def _validate_report_levels():
    unsupported = sorted(
        case.name for case in GLOBAL_CACHE_CASES
        if case.enabled and case.report_level != "advanced"
    )
    if unsupported:
        raise GlobalCacheFailure("cases use unsupported report contract: %s" % ", ".join(unsupported))


def _find_case(target):
    for case in GLOBAL_CACHE_CASES:
        if case.name == target:
            return case
    raise GlobalCacheFailure("unknown global_cache case: %s" % target)


def _load_env(root):
    env = load_regression_config(root)
    if not env.test_context_file.exists():
        raise GlobalCacheFailure(
            "missing %s, run ./run.sh env setup first" % env.test_context_file
        )
    context = yaml.safe_load(env.test_context_file.read_text(encoding="utf-8")) or {}
    return env, context


def _format_case(case):
    if not case.enabled:
        status = "disabled"
    else:
        status = "formal"
    return "%-52s batch=%-2s driver=%-12s report=%-8s [%s] %s" % (
        case.target,
        case.batch,
        case.driver,
        case.report_level,
        status,
        case.summary,
    )


def show():
    _validate_report_levels()
    lines = ["global_cache"]
    for case in case_items():
        lines.append("  - %s" % _format_case(case))
    lines.append("")
    lines.append("Formal gate: %d / %d" % (len(enabled_case_names()), len(case_items())))
    lines.append("Advanced report level: %d / %d" % (len(advanced_case_names()), len(case_items())))
    return "\n".join(lines)


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _case_port(case):
    base = 40000
    total = sum(ord(ch) for ch in case.name)
    return base + (total % 4000)


def _case_prom_port(case):
    base = 45432
    total = sum(ord(ch) for ch in case.name)
    return base + (total % 2000)


def _is_local_tcp_port_free(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _write_json(path, payload):
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _relevant_fbasecman_lines(text, limit=32, keywords=None):
    """Keep action-scoped proxy evidence readable; full window remains on disk."""
    keywords = tuple(keywords or (
        "(query)", "(routing)", "routing ", "(parse)", "(bind)",
        "(execute)", "global prepared", "backend cache", "error", "discard",
    ))
    lines = [line for line in text.splitlines() if any(key in line.lower() for key in keywords)]
    if not lines:
        return "<无与本步骤直接相关的 fbasecman 新增日志>"
    if len(lines) > limit:
        lines = lines[:limit] + ["... 其余相关日志保存在 fbasecman.log"]
    return "\n".join(lines)


def _relevant_pg_lines(text, sql=None, limit=36):
    if not text.strip():
        return "<无新增 PostgreSQL 日志>"
    if sql is None:
        return "<console 命令不转发 PostgreSQL；完整新增日志保存在 logs/>"
    fragments = [item.strip().lower() for item in str(sql).split(";") if len(item.strip()) >= 3]
    selected = []
    for line in text.splitlines():
        lower = line.lower()
        if "===== log:" in lower:
            selected.append(line)
        elif ("statement:" in lower or "error:" in lower) and (
                not fragments or any(fragment in lower for fragment in fragments)):
            selected.append(line)
    if not selected:
        selected = [line for line in text.splitlines() if "error:" in line.lower()]
    if not selected:
        return "<无与当前 SQL 直接相关的 PostgreSQL 新增日志；完整日志保存在 logs/>"
    if len(selected) > limit:
        selected = selected[:limit] + ["... 其余相关 PostgreSQL 日志保存在 logs/>"]
    return "\n".join(selected)


class _JournaledJdbcPhaseProcess(object):
    """PhasedProcess plus one action-scoped, crash-safe JDBC evidence step."""

    def __init__(self, runtime, command, source, jdbc_url, logfile, title, cwd=None,
                 sql_operations=None):
        self.runtime = runtime
        self.source = source
        self.jdbc_url = jdbc_url
        self.sql_operations = list(sql_operations or [])
        self.step = runtime.evidence_step(
            title,
            sql="; ".join(item["sql"] for item in (sql_operations or [])),
            expected="阶段 JDBC driver 完成并保持文档规定的连接状态",
            metadata={"jdbc_driver_parent": True},
        )
        self.step.__enter__()
        self._closed = False
        self._observed_phase_count = 0
        self._last_observed_output_length = 0
        try:
            self.process = PhasedProcess(command, logfile, cwd=cwd)
        except Exception as exc:
            self._close(type(exc), exc, None)
            raise

    @property
    def output(self):
        return self.process.output

    def wait_for(self, marker, timeout=30):
        return self.process.wait_for(marker, timeout=timeout)

    def resume(self, command="continue"):
        return self.process.resume(command)

    def mark_phase_observed(self):
        self._observed_phase_count += 1
        self._last_observed_output_length = len(self.output)

    def _close(self, exc_type=None, exc_value=None, traceback=None):
        if self._closed:
            return
        self._closed = True
        # The parent driver has been running while its child phase steps were
        # observed.  Once it completes, render its final result after those
        # observations, which is the order a reader experienced it.
        self.step.record["order"] = self.runtime._next_step_order()
        self.step.__exit__(exc_type, exc_value, traceback)

    def render_action(self, output):
        action, visible_output = render_jdbc_action(
            self.source, self.jdbc_url, without_phase_markers(output),
            operations=self.sql_operations or None,
        )
        return action, visible_output

    def finish(self, timeout=30):
        try:
            rc, output = self.process.finish(timeout=timeout)
            action, visible_output = self.render_action(output)
            if self._observed_phase_count:
                visible_output = without_phase_markers(
                    output[self._last_observed_output_length:]
                ).strip() or "<阶段恢复后无新增业务输出>"
                self.step.record["title"] = (
                    "JDBC driver 完成后的业务输出: %s" % self.source.stem
                )
                self.step.actual_execution("JDBC driver 阶段恢复后的新增输出", visible_output)
            else:
                self.step.actual_execution(action, visible_output)
            self.step.assess("阶段 JDBC driver 退出成功", visible_output, rc == 0)
            self._close()
            return rc, output
        except Exception as exc:
            self._close(type(exc), exc, None)
            raise

    def terminate(self):
        try:
            self.process.terminate()
        finally:
            self._close(RuntimeError, RuntimeError("阶段 JDBC driver 被终止"), None)


class CaseRuntime(GlobalCacheReportMixin):
    def __init__(self, root, env, context, case):
        self.root = root
        self.env = env
        self.context = context
        self.case = case
        self.run_root = root / "output" / "runs" / "global_cache" / case.name
        self.workdir = self.run_root / "workdir"
        self.logs_dir = self.run_root / "logs"
        self.build_dir = self.workdir / "build"
        self.driver_dir = self.workdir / "drivers"
        self.run_root.mkdir(parents=True, exist_ok=True)
        if self.logs_dir.exists():
            shutil.rmtree(str(self.logs_dir))
        if self.workdir.exists():
            shutil.rmtree(str(self.workdir))
        self.workdir.mkdir(parents=True)
        self.logs_dir.mkdir(parents=True)
        self.build_dir.mkdir(parents=True)
        self.driver_dir.mkdir(parents=True)
        self.manifest_file = self.run_root / "manifest.json"
        self.events_file = self.run_root / "events.log"
        self.console_log = self.logs_dir / "console.log"
        self.jdbc_log = self.logs_dir / "jdbc.log"
        self.libpq_log = self.logs_dir / "libpq.log"
        self.fbasecman_log = self.run_root / "fbasecman.log"
        self.report_file = self.run_root / "report.txt"
        for stale in (self.events_file, self.report_file, self.fbasecman_log):
            if stale.exists():
                stale.unlink()
        self.summary = {}
        self.started_at = datetime.now()
        self.finished_at = None
        self.local_log_window = LocalLogWindow()
        self.step_records = []
        self._step_order = 0
        self.step_journal = StepJournal(self.run_root / "steps.json", case.target)
        self._last_evidence_windows = ("", "")
        self.listen_port = self._allocate_port(_case_port(case), step=17)
        self.prom_port = self._allocate_port(_case_prom_port(case), step=19)
        self.pid_file = self.workdir / "fbasecman.pid"
        self.locks_dir = self.workdir / "locks"
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self.product_process = FbasecmanProcess(
            self.env.config["fbasecman"]["fbasecman_bin"],
            self.env.config["local"]["postgres_dir"],
            self.listen_port,
            self.prom_port,
            self.pid_file,
            self.locks_dir,
            self.fbasecman_log,
            self.logs_dir,
            self.run_command,
            self.trace,
            _is_local_tcp_port_free,
        )
        self._core_before = self._list_core_files()
        self._snapshot_manifest()

    def _allocate_port(self, preferred, step):
        candidate = preferred
        for _ in range(128):
            if _is_local_tcp_port_free(candidate):
                return candidate
            candidate += step
        raise GlobalCacheFailure("failed to allocate free local tcp port near %s" % preferred)

    def _list_core_files(self):
        candidates = []
        for pattern in ("core*",):
            candidates.extend(self.root.glob(pattern))
        return {
            str(path.resolve())
            for path in candidates
            if path.is_file()
        }

    def detect_new_core(self):
        after = self._list_core_files()
        created = sorted(after - self._core_before)
        if not created:
            return None
        core_path = created[-1]
        cmd = "gdb %s %s" % (
            self.env.config["fbasecman"]["fbasecman_bin"],
            core_path,
        )
        self.summary["core_file"] = core_path
        self.summary["gdb_command"] = cmd
        return core_path, cmd

    def log_offset(self, path):
        if not path.exists():
            return 0
        return path.stat().st_size

    def read_log_slice(self, path, start_offset, end_offset=None):
        if not path.exists():
            return ""
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(max(0, start_offset))
            if end_offset is None:
                return fh.read()
            remaining = max(0, end_offset - start_offset)
            return fh.read(remaining)

    def _mmr_pg_log_dirs(self):
        base = self.env.config["database"]["mmr_postgres_dir"]
        return [
            "%s/test_mmr1/pg_log" % base,
            "%s/test_mmr2/pg_log" % base,
            "%s/test_mmr3/pg_log" % base,
            "%s/test_mmr1_s1/pg_log" % base,
            "%s/test_mmr2_s1/pg_log" % base,
        ]

    def _pg_log_source(self):
        database = self.env.config["database"]
        if self.case.topology == "rep":
            base = database["rep_postgres_dir"]
            dirs = [
                "%s/test_rep1/pg_log" % base,
                "%s/test_rep1_s1/pg_log" % base,
                "%s/test_rep1_s2/pg_log" % base,
            ]
            return database["rep_host"], database["rep_pg_user"], dirs
        return database["mmr_host"], database["mmr_pg_user"], self._mmr_pg_log_dirs()

    def begin_fbasecman_log_window(self):
        return self.local_log_window.mark([self.fbasecman_log])

    def collect_fbasecman_log_window(self, marks, patterns=None):
        captured = self.local_log_window.collect(marks)
        text = captured.get(str(self.fbasecman_log), "")
        if patterns:
            lines = self.local_log_window.matching_lines(captured, patterns)
            return text, [line for _, line in lines]
        return text, []

    def _run_remote_log_script(self, host, user, script, logfile):
        cmd = [
            "ssh",
            "-F",
            "/dev/null",
            "%s@%s" % (user, host),
            "bash -lc %s" % shlex.quote(script),
        ]
        return self.run_command(cmd, logfile, check=True, record=False)

    def begin_pg_log_window(self, stem):
        host, user, log_dirs = self._pg_log_source()
        logfile = self.logs_dir / ("%s.pg.snapshot.log" % stem)
        _, output = self._run_remote_log_script(
            host, user, remote_snapshot_script(log_dirs), logfile
        )
        return {
            "host": host,
            "user": user,
            "log_dirs": log_dirs,
            "marks": parse_remote_log_snapshot(output),
            "snapshot_log": str(logfile),
        }

    def collect_pg_log_window(self, window, stem, patterns=None):
        logfile = self.logs_dir / ("%s.pg.window.log" % stem)
        _, text = self._run_remote_log_script(
            window["host"],
            window["user"],
            remote_collect_script(window["log_dirs"], window["marks"]),
            logfile,
        )
        lines = [line for line in text.splitlines() if line and not line.startswith("===== LOG:")]
        if patterns:
            matched = [
                line for line in lines
                if any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns)
            ]
        else:
            matched = lines
        return {
            "text": text,
            "lines": matched,
            "logfile": str(logfile),
            "paths": list(window["log_dirs"]),
        }

    def capture_core_log_evidence(self):
        """Persist per-case fbasecman and PostgreSQL evidence for every result."""
        host, user, log_dirs = self._pg_log_source()
        pg_snapshot = self.logs_dir / "postgresql.log"
        dir_args = quote_arguments(log_dirs)
        script = (
            "for dir in %s; do\n"
            "  [ -d \"$dir\" ] || continue\n"
            "  for file in \"$dir\"/*; do\n"
            "    [ -f \"$file\" ] || continue\n"
            "    echo \"===== PG LOG: $file =====\"\n"
            "    tail -n 400 \"$file\"\n"
            "  done\n"
            "done\n"
        ) % dir_args
        cmd = ["ssh", "-F", "/dev/null", "%s@%s" % (user, host), "bash -lc %s" % shlex.quote(script)]
        rc, _ = self.run_command(cmd, pg_snapshot, check=False, record=False)
        self.summary["log_evidence"] = {
            "fbasecman": str(self.fbasecman_log),
            "postgresql": str(pg_snapshot),
            "pg_capture_rc": rc,
            "pg_log_dirs": log_dirs,
        }

    def remote_grep_count(self, host, user, log_dirs, patterns, logfile, step_title=None):
        if not patterns:
            raise GlobalCacheFailure("remote_grep_count requires at least one pattern")
        # grep -E uses POSIX ERE; it does not understand Python's (?:...).
        grep_pattern = shlex.quote("(%s)" % "|".join(patterns))
        dir_args = quote_arguments(log_dirs)
        script = (
            "count=0\n"
            "for dir in %s; do\n"
            "  if [ -d \"$dir\" ]; then\n"
            "    value=$(grep -r -h -E -c %s \"$dir\"/* 2>/dev/null | awk '{sum+=$1} END{print sum+0}')\n"
            "    count=$((count + value))\n"
            "  fi\n"
            "done\n"
            "echo \"$count\"\n"
        ) % (dir_args, grep_pattern)
        cmd = ["ssh", "-F", "/dev/null", "%s@%s" % (user, host), "bash -lc %s" % shlex.quote(script)]
        _, output = self.run_command(cmd, logfile, step_title=step_title, check=True)
        text = output.strip()
        try:
            count = int(text.splitlines()[-1]) if text else 0
        except ValueError:
            raise GlobalCacheFailure("failed to parse remote grep count: %s" % text)
        if self.step_records:
            step = self.step_records[-1]
            if step.get("title") == step_title:
                if self.case.name == "heartbeat_reload_reclassifies_existing_normal_entry":
                    detail_lines = [
                        "pattern: %s" % ", ".join(patterns),
                        "checked_dirs=%s" % len(log_dirs),
                        "累计命中次数: %s" % count,
                    ]
                else:
                    detail_lines = [
                        "目标 pattern:",
                    ]
                    for pattern in patterns:
                        detail_lines.append("  %s" % pattern)
                    detail_lines.append("检查的 PG 日志目录:")
                    for log_dir in log_dirs:
                        detail_lines.append("  %s" % log_dir)
                    detail_lines.append("累计命中次数: %s" % count)
                step["output"] = "\n".join(detail_lines)
        return count

    def remote_grep_lines(self, host, user, log_dirs, patterns, logfile, step_title=None, max_lines=20):
        """Collect readable PG evidence, rather than reporting only a grep count."""
        if not patterns:
            raise GlobalCacheFailure("remote_grep_lines requires at least one pattern")
        grep_pattern = shlex.quote("(%s)" % "|".join(patterns))
        dir_args = quote_arguments(log_dirs)
        script = (
            "for dir in %s; do\n"
            "  [ -d \"$dir\" ] || continue\n"
            "  grep -r -h -E %s \"$dir\"/* 2>/dev/null || true\n"
            "done | tail -n %d\n"
        ) % (dir_args, grep_pattern, max_lines)
        cmd = ["ssh", "-F", "/dev/null", "%s@%s" % (user, host), "bash -lc %s" % shlex.quote(script)]
        _, output = self.run_command(cmd, logfile, step_title=step_title, check=True)
        lines = [line.rstrip() for line in output.splitlines() if line.strip()]
        if self.step_records and self.step_records[-1].get("title") == step_title:
            step = self.step_records[-1]
            step["output"] = (
                "匹配 SQL: %s\n" % ", ".join(patterns)
                + ("\n".join(lines) if lines else "<本次窗口未发现该 SQL 到达 PostgreSQL>")
            )
        return lines

    def _snapshot_manifest(self):
        payload = {
            "target": self.case.target,
            "summary": self.case.summary,
            "batch": self.case.batch,
            "priority": self.case.priority,
            "driver": self.case.driver,
            "topology": self.case.topology,
            "rw_split_method": self.case.rw_split_method,
            "pool_mode": self.case.pool_mode,
            "report_level": self.case.report_level,
            "issue_id": self.case.issue_id,
            "notes": list(self.case.notes),
            "fbasecman": dict(self.case.fbasecman),
            "runtime": {
                "listen_port": self.listen_port,
                "prom_port": self.prom_port,
                "pid_file": str(self.pid_file),
                "locks_dir": str(self.locks_dir),
            },
            "jdbc": dict(self.case.jdbc),
            "sql": dict(self.case.sql),
            "reload": self.case.reload,
        }
        _write_json(self.manifest_file, payload)

    def log(self, message):
        line = message.rstrip()
        print(line, flush=True)
        with self.events_file.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def trace(self, message):
        with self.events_file.open("a", encoding="utf-8") as fh:
            fh.write(message.rstrip() + "\n")

    def _next_step_order(self):
        self._step_order = getattr(self, "_step_order", 0) + 1
        return self._step_order

    def evidence_step(self, title, sql=None, console=False, expected=None,
                      collect_logs=False, metadata=None):
        """Persist one business action, collecting logs only when required."""
        order = self._next_step_order()

        step_metadata = {"order": order, "console": bool(console)}
        step_metadata.update(metadata or {})

        if not collect_logs:
            self._last_evidence_windows = ("", "")
            return EvidenceStep(
                title, self.step_journal, expected=expected,
                metadata=step_metadata, on_change=self.write_live_step_report,
            )

        def open_window():
            marks = {"proxy": self.begin_fbasecman_log_window()}
            if collect_logs != "proxy" and not console:
                marks["pg"] = self.begin_pg_log_window("step_%03d" % order)
            return marks

        def collect_window(marks):
            proxy_text, _ = self.collect_fbasecman_log_window(marks["proxy"])
            pg_text = (self.collect_pg_log_window(marks["pg"], "step_%03d" % order)["text"]
                       if "pg" in marks else "")
            self._last_evidence_windows = (proxy_text, pg_text)
            evidence = [("fbasecman 证据", _relevant_fbasecman_lines(
                proxy_text,
                keywords=getattr(getattr(self, "case", None), "evidence", {}).get(
                    "report_log_keywords"
                ),
            ))]
            if "pg" in marks:
                evidence.append(("PG 证据", _relevant_pg_lines(
                    pg_text, None if console else sql,
                )))
            return evidence

        return EvidenceStep(
            title, self.step_journal, open_window=open_window,
            collect_window=collect_window, expected=expected,
            metadata=step_metadata,
            on_change=self.write_live_step_report,
        )

    def write_live_step_report(self):
        """Keep report.txt useful after interruption, before the final renderer runs."""
        steps = []
        for item in sorted(self.step_journal.steps, key=lambda source: source.get("order", 0)):
            transport_only = is_transport_only_success(
                item.get("expected"), item.get("result"),
            )
            observation_only = bool(item.get("observation_only"))
            steps.append(ReportStep(
                item["title"], execution=item.get("execution", []),
                intermediate=item.get("intermediate", []), evidence=item.get("evidence", []),
                key_expected=None if transport_only else item.get("expected") or None,
                actual=None if transport_only or observation_only else item.get("actual") or None,
                result=None if transport_only or observation_only else item.get("result") or None,
            ))
        document = ReportDocument(
            self.case.target, "RUNNING",
            self.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.case.summary,
            config_lines=[
                "拓扑: %s" % self.case.topology,
                "读写模式: %s" % self.case.rw_split_method,
            ], overview_steps=list(self.case.notes), steps=steps,
        )
        self._apply_case_coverage(document)
        atomic_write_text(self.report_file, render_report(document))

    def execute_jdbc(self, command, source, logfile, title, jdbc_url, cwd=None,
                     expected="JDBC 程序退出成功", check=True, sql_operations=None):
        """Run a Java driver while reporting source-backed JDBC actions."""
        operations = []
        try:
            from framework.evidence import jdbc_prepared_operations
            operations = jdbc_prepared_operations(source)
        except OSError:
            pass
        report_operations = list(sql_operations or operations)
        sql = "; ".join(item["sql"] for item in report_operations)
        with self.evidence_step(title, sql=sql, expected=expected) as step:
            result = run_logged_command(command, logfile, cwd=cwd or self.root)
            action, output = render_jdbc_action(
                source, jdbc_url, without_phase_markers(result.output),
                operations=report_operations or None,
            )
            step.actual_execution(action, output)
            step.assess(expected, output, result.returncode == 0)
        if check and result.returncode != 0:
            raise GlobalCacheFailure("JDBC command failed (%s): %s" % (result.returncode, result.command))
        return result.returncode, result.output

    def set_latest_evidence_outcome(self, expected, actual, passed):
        if not self.step_journal.steps:
            raise GlobalCacheFailure("no evidence step is available for outcome update")
        step = self.step_journal.steps[-1]
        step["expected"] = str(expected).strip()
        step["actual"] = str(actual).strip()
        step["result"] = "PASS" if passed else "FAIL"
        self.step_journal.save()
        self.write_live_step_report()

    def observe_jdbc_phases(self, process, source, jdbc_url, actions, observe,
                            timeout=30, finish_timeout=30, collect_logs=False):
        """Inspect fbasecman while a JDBC driver keeps its connection paused.

        ``observe`` may return a plain business value or a mapping with
        ``command`` and ``output`` fields.  The latter is rendered as the
        phase's real console/database intermediate state.
        """
        observations = {}
        try:
            for action in actions:
                title = getattr(action, "title", None) or ("JDBC 阶段: %s" % action.name)
                expected = getattr(action, "expected", None) or "JDBC 到达 %s 并保持连接，控制台状态符合预期" % action.name
                if isinstance(collect_logs, dict):
                    phase_log_mode = collect_logs.get(action.name, False)
                else:
                    phase_log_mode = collect_logs
                with self.evidence_step(
                        title, expected=expected, collect_logs=phase_log_mode,
                        metadata={"jdbc_phase": True}) as step:
                    marker = process.wait_for(action.marker, timeout=timeout)
                    if hasattr(process, "render_action"):
                        jdbc_action, visible_output = process.render_action(process.output)
                    else:
                        jdbc_action, visible_output = render_jdbc_action(
                            source, jdbc_url, without_phase_markers(process.output),
                        )
                    step.actual_execution(jdbc_action, visible_output)
                    observed = observe(action.name, marker)
                    if isinstance(observed, dict) and "command" in observed:
                        step.intermediate_state(observed["command"], observed.get("output", ""))
                    observations[action.name] = observed
                    if hasattr(process, "mark_phase_observed"):
                        process.mark_phase_observed()
                    if isinstance(observed, dict) and "passed" in observed:
                        phase_expected = observed.get("expected", expected)
                        phase_actual = observed.get(
                            "actual", "JDBC 已到达该阶段，连接保持且中间状态已采集。",
                        )
                        passed = bool(observed.get("passed", True))
                    elif isinstance(observed, dict):
                        # The phase only captured an intermediate snapshot.
                        # Product checks attached to this step make the final
                        # judgment after interpreting the complete state.
                        step.mark_observation_only()
                        phase_expected = expected
                        phase_actual = ""
                        passed = None
                    else:
                        phase_expected = expected
                        phase_actual = str(observed) if observed is not None else (
                            "JDBC 已到达该阶段，连接保持且中间状态已采集。"
                        )
                        passed = True
                    if passed is not None:
                        step.assess(phase_expected, phase_actual, passed)
                    if passed is False:
                        raise GlobalCacheFailure(
                            "%s: expected %s, actual %s" % (
                                title, phase_expected, phase_actual,
                            )
                        )
                if action.resume_command is not None:
                    process.resume(action.resume_command)
            rc, output = process.finish(timeout=finish_timeout)
            return observations, rc, output
        except Exception:
            process.terminate()
            raise

    def start_jdbc_phase_process(self, command, source, jdbc_url, logfile, title,
                                 cwd=None, sql_operations=None):
        """Start a long-lived JDBC driver with a journaled evidence lifetime."""
        return _JournaledJdbcPhaseProcess(
            self, command, source, jdbc_url, logfile, title, cwd=cwd,
            sql_operations=sql_operations,
        )

    def record_step(self, title, command=None, logfile=None, rc=None, output=None, note=None,
                    expected=None, actual=None, result=None, phase=None):
        self.step_records.append(
            {
                "title": title or "",
                "command": command or "",
                "logfile": str(logfile) if logfile else "",
                "rc": rc,
                "output": output if output is not None else "",
                "note": note or "",
                "expected": expected or "",
                "actual": actual or "",
                "result": result or "",
                "phase": phase or "",
                "order": self._next_step_order(),
            }
        )

    def verify(self, title, expected, actual, passed, evidence=None, phase=None):
        check = {
            "title": title,
            "expected": str(expected),
            "actual": str(actual),
            "result": "PASS" if passed else "FAIL",
        }
        if evidence:
            check["evidence"] = str(evidence)
        self.summary.setdefault("verification_checks", []).append(check)
        self.record_step(
            "验证: %s" % title,
            output=str(evidence or actual),
            expected=str(expected),
            actual=str(actual),
            result=check["result"],
            phase=phase,
        )
        if not passed:
            self.summary["failed_step"] = dict(self.step_records[-1])
            self.summary["failed_check"] = dict(check)
            raise VerificationFailure(check)
        return check

    def run_command(self, cmd, logfile, cwd=None, env=None, echo=False, check=True, step_title=None, record=True):
        title = step_title or ("执行命令: %s" % Path(str(logfile)).name)
        result = run_logged_command(
            cmd, logfile, cwd=cwd or self.root, env=env, echo=echo
        )
        display = result.command
        rc = result.returncode
        output = result.output
        self.trace("[cmd] %s" % display)
        if record:
            self.record_step(
                title,
                command=display,
                logfile=logfile,
                rc=rc,
                output=output,
            )
        if check and rc != 0:
            if record and self.step_records:
                failed = self.step_records[-1]
                failed["expected"] = "command exits with rc=0"
                failed["actual"] = "command exited with rc=%s" % rc
                failed["result"] = "FAIL"
                self.summary["failed_step"] = dict(failed)
            raise GlobalCacheFailure("command failed (%s): %s" % (rc, display))
        return rc, output

    def psql_command(self, database, sql):
        cfg = self.env.config
        return build_psql_command(
            cfg["local"]["postgres_dir"],
            "localhost",
            self.listen_port,
            "admin" if database == "console" else "postgres",
            database,
            sql,
            footer=False,
            output_format="unaligned",
            field_separator="|",
        )

    def psql(self, database, sql, logfile, step_title=None, record=True):
        cmd = self.psql_command(database, sql)
        title = step_title or "psql 执行 SQL"
        if not record:
            _, output = self.run_command(cmd, logfile, step_title=title, record=False)
            return output
        with self.evidence_step(title, sql=sql, console=(database == "console"),
                                expected="psql 命令完成") as step:
            result = run_logged_command(cmd, logfile, cwd=self.root)
            output = result.output
            step.actual_execution("$ " + quote_arguments(cmd), render_psql_table_from_pipe_text(output))
            step.assess("psql 命令完成", output.rstrip() or "<empty>", result.returncode == 0)
        if result.returncode != 0:
            failed = self.step_journal.steps[-1]
            self.summary["failed_step"] = {
                "title": title,
                "expected": failed.get("expected", ""),
                "actual": failed.get("actual", ""),
                "result": "FAIL",
            }
            raise GlobalCacheFailure("psql command failed (%s): %s" % (result.returncode, result.command))
        return output

    def console_query(self, sql, stem, record=True):
        logfile = self.logs_dir / ("%s.raw.log" % stem)
        raw = self.psql(
            "console",
            sql,
            logfile,
            step_title="console 查询: %s" % sql,
            record=record,
        )
        try:
            return parse_pipe_rows(raw)
        except ConsoleQueryError as exc:
            raise GlobalCacheFailure(str(exc))

    def capture_console_state(self, prefix, include_server=True, record=True):
        return capture_global_cache_state(
            self.console_query, prefix, include_server, record=record
        )

    def write_summary(self):
        _write_json(self.run_root / "summary.json", self.summary)

    def finish(self):
        self.finished_at = datetime.now()

    def prune_artifacts(self):
        if self.summary.get("status") != "PASS":
            return
        # A PASS is still evidence: report assertions may rely on JDBC, console,
        # fbasecman and PostgreSQL log windows.  Keep those captured artifacts
        # so a successful gate remains independently auditable.
        keep = {"report.txt", "fbasecman.log", "logs", "summary.json", "steps.json"}
        for path in self.run_root.iterdir():
            if path.name in keep:
                continue
            if path.is_dir():
                shutil.rmtree(str(path))
            else:
                path.unlink()

    def base_conf_file(self):
        if self.case.topology == "mmr":
            return global_cache_asset_path(self.root, "config", "mmr_hint_pool.conf")
        return global_cache_asset_path(self.root, "config", "rep_hint_pool.conf")

    def start_fbasecman(self, conf=None):
        conf = conf or self.base_conf_file()
        if not conf.exists():
            raise GlobalCacheFailure("missing base conf: %s" % conf)
        if conf == self.base_conf_file():
            conf = self.rewrite_conf(
                self.product_process.config_replacements(DEFAULT_FBASECMAN_LOG_LEVEL),
                stem="global_cache_runtime.conf",
            )
        self.summary["fbasecman_config_lines"] = extract_config_lines(
            conf.read_text(encoding="utf-8", errors="replace"),
            REPORT_FBASECMAN_CONFIG_PREFIXES,
        ).splitlines()
        self.product_process.start(conf)

    def stop_fbasecman(self, best_effort=False, conf=None, record=True):
        self.product_process.stop(
            best_effort=best_effort,
            conf=conf or self.product_process.active_conf or self.base_conf_file(),
            record=record,
        )

    def rewrite_conf(self, replacements, stem=None):
        conf = self.base_conf_file()
        rendered = conf.read_text(encoding="utf-8")
        for old_value, new_value in replacements:
            if old_value and old_value in rendered:
                rendered = rendered.replace(old_value, new_value)
                continue
            key = new_value.strip().split(None, 1)[0] if new_value.strip() else ""
            if key:
                rendered = set_or_append_config_line(rendered, key, new_value)
        rendered = apply_datasource_runtime(rendered, self.case.topology, self.env.config)
        target_name = stem or conf.name
        temp_conf = self.workdir / target_name
        temp_conf.write_text(rendered, encoding="utf-8")
        return temp_conf

    def render_runtime_conf(self, replacements, stem):
        conf = self.base_conf_file()
        rendered = conf.read_text(encoding="utf-8")
        runtime_replacements = self.product_process.config_replacements(
            DEFAULT_FBASECMAN_LOG_LEVEL
        )
        for old_value, new_value in runtime_replacements + list(replacements):
            if old_value and old_value in rendered:
                rendered = rendered.replace(old_value, new_value)
                continue
            key = new_value.strip().split(None, 1)[0] if new_value.strip() else ""
            if key:
                rendered = set_or_append_config_line(rendered, key, new_value)
        rendered = apply_datasource_runtime(rendered, self.case.topology, self.env.config)
        target = self.workdir / stem
        target.write_text(rendered, encoding="utf-8")
        return target

    def console_reload(self):
        output = self.psql(
            "console",
            "reload;",
            self.logs_dir / "console_reload.log",
            step_title="console 执行 reload",
        )
        result = output.strip() or "<empty>"
        self.summary["reload_result"] = result
        return result

    def wait_for_console_ready(self, timeout=30.0):
        # An MMR group check can legitimately take longer than the old 15s
        # default before the proxy opens its client listener.
        return self.product_process.wait_ready(timeout)
