"""Stable workload lifecycle, monitoring, diagnostics, and reporting."""

import csv
import os
import re
import shlex
import shutil
import socket
import tarfile
import time
from datetime import datetime
from pathlib import Path

from cmanconf import validate_profile_isolation
from platform_regress.clients.psql import build_psql_command
from platform_regress.execution.background import start_background
from platform_regress.execution.command import run_logged_command
from platform_regress.execution.locking import ExclusiveFileLock
from platform_regress.execution.longrun import transition_status
from platform_regress.execution.processes import (
    is_alive,
    managed_pid,
    stop_managed,
)
from platform_regress.execution.shell import LoggedShellRunner
from suites.stable.config import StableConfig, render_fbasecman_config
from suites.stable.manifest import find_workload
from suites.stable.state import StateStore

from products.fbasecman.process import FbasecmanProcess

LOCAL_HOST = os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1")
class StableFailure(RuntimeError):
    pass


def _port_free(port):
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((LOCAL_HOST, int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def choose_ports(preferred_main=None, preferred_write=None):
    """Return an available stable fbasecman listener and Prometheus triplet.

    Stable has configured listener ports so it remains predictably separate
    from regression.  A free dynamic triplet still lets a stale local process
    or a second checkout avoid a port conflict.
    """
    if preferred_main and preferred_write:
        ports = (int(preferred_main), int(preferred_write), int(preferred_main) + 2)
        if len(set(ports)) == 3 and all(_port_free(port) for port in ports):
            return ports
    seed = 28000 + (os.getpid() * 37 % 16000)
    for offset in range(0, 16000, 3):
        ports = (seed + offset, seed + offset + 1, seed + offset + 2)
        if all(_port_free(port) for port in ports):
            return ports
    raise StableFailure("no free stable port triplet")


def format_elapsed(seconds):
    seconds = max(0, int(seconds or 0))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return "%dh%02dm%02ds" % (hours, minutes, seconds)
    return "%dm%02ds" % (minutes, seconds)


def workload_progress(cfg, state, name, item, now=None):
    """Calculate a workload's live progress, including old state files."""
    now = int(time.time() if now is None else now)
    try:
        workload = find_workload(name)
        configured_duration = cfg.duration(workload.kind)
    except (KeyError, AttributeError):
        configured_duration = 0
    duration = int(item.get("duration_seconds", 0) or configured_duration)
    started_at = int(item.get("started_at", 0) or state.get("started_at", 0) or 0)
    elapsed = max(0, now - started_at) if started_at else 0
    remaining = max(0, duration - elapsed) if duration else 0
    active = item.get("status") == "running"
    return {
        "duration_seconds": duration,
        "duration_text": format_elapsed(duration) if duration else "unknown",
        "started_at": started_at,
        "elapsed_seconds": elapsed,
        "elapsed_text": format_elapsed(min(elapsed, duration) if duration else elapsed) if started_at else "not started",
        "remaining_seconds": remaining,
        "remaining_text": format_elapsed(remaining) if active and started_at else ("not started" if not started_at else "finished"),
        "ends_at": started_at + duration if started_at and duration else 0,
        "ends_at_text": datetime.fromtimestamp(started_at + duration).strftime("%Y-%m-%d %H:%M:%S") if started_at and duration else "-",
    }


def run_progress(cfg, state, now=None):
    now = int(time.time() if now is None else now)
    items = [workload_progress(cfg, state, name, item, now)
             for name, item in state.get("workloads", {}).items()]
    started_at = int(state.get("started_at", 0) or 0)
    elapsed = max(0, now - started_at) if started_at else 0
    active = [item for item in items if item["remaining_text"] not in ("finished", "not started")]
    planned_end = max((item["ends_at"] for item in items), default=0)
    remaining = max(0, planned_end - now) if active and planned_end else 0
    return {
        "elapsed_text": format_elapsed(elapsed) if started_at else "not started",
        "remaining_text": format_elapsed(remaining) if active and planned_end else "finished",
        "ends_at_text": datetime.fromtimestamp(planned_end).strftime("%Y-%m-%d %H:%M:%S") if planned_end else "-",
    }


def workload_status_text(item):
    """Return a concise terminal status with the result that produced it."""
    status = item.get("status", "unknown")
    if status == "completed":
        return "PASS"
    if status == "pending":
        return "NOT_STARTED"
    if status == "stopped":
        return "STOPPED"
    if status == "running":
        return "RUNNING"
    if status == "failed":
        result = item.get("result", {})
        details = []
        if "transactions" in result:
            if result.get("failures", 0):
                details.append("transaction_failures=%s" % result["failures"])
            details.append("transactions=%s" % result["transactions"])
        elif "failures" in result:
            details.append("client_failures=%s" % result["failures"])
        if result.get("returncode") not in (None, 0):
            details.append("rc=%s" % result["returncode"])
        return "FAIL(%s)" % ", ".join(details) if details else "FAIL(no result)"
    return status.upper()


def displayed_pid(state, item, key="pid"):
    """Show PIDs only for currently running state, never historical records."""
    if item.get("status") != "running":
        return "-"
    return str(item.get(key) or "-")










def pgbench_result(text, returncode=0, allow_config_lock_conflict=False):
    retry = re.search(r"HA_RETRY_SUMMARY attempts=(\d+) successes=(\d+) "
                      r"reload_busy=(\d+)(?: refresh_retry=(\d+))? "
                      r"unexpected_failures=(\d+)", text)
    if retry:
        attempts, successes, reload_busy, refresh_retry, unexpected = retry.groups()
        attempts, successes, reload_busy, unexpected = map(
            int, (attempts, successes, reload_busy, unexpected))
        refresh_retry = int(refresh_retry or 0)
        return {"ok": returncode == 0 and successes > 0 and unexpected == 0,
                "transactions": successes, "failures": unexpected,
                "reload_busy": reload_busy, "refresh_retry": refresh_retry,
                "attempts": attempts,
                "returncode": returncode}
    from platform_regress.clients.pgbench import parse_pgbench_result
    metrics = parse_pgbench_result(text, returncode)
    count, failed = metrics["transactions"], metrics["failures"]
    lock_conflict = "another reload or configuration persistence command is running" in text
    error_lines = [line for line in text.splitlines()
                   if re.search(r"ERROR:|FATAL:|PANIC:|server closed|connection refused|backend died", line, re.I)]
    only_expected_lock_conflict = lock_conflict and all(
        "another reload or configuration persistence command is running" in line
        for line in error_lines)
    expected = allow_config_lock_conflict and only_expected_lock_conflict
    return {"ok": (returncode == 0 and count > 0 and failed == 0) or expected,
            "transactions": count, "failures": failed, "returncode": returncode}


def jdbc_result(text, returncode=0):
    match = re.search(
        r"RESULT:\s*(?:PASS|FAIL).*?success=(\d+).*?failures=(\d+).*?prepares=(\d+).*?"
        r"rw_switch_failures=(\d+).*?heartbeat_failures=(\d+).*?guc_failures=(\d+)",
        text, re.I | re.S)
    if not match:
        return {"ok": False, "returncode": returncode, "reason": "RESULT summary missing"}
    success, failures, prepares, rw, heartbeat, guc = [int(value) for value in match.groups()]
    return {"ok": returncode == 0 and success > 0 and prepares > 0 and
            failures == rw == heartbeat == guc == 0,
            "returncode": returncode, "success": success, "failures": failures,
            "prepares": prepares, "rw_switch_failures": rw,
            "heartbeat_failures": heartbeat, "guc_failures": guc}


ROUTE_RULES = {
    # The scheduler may choose any healthy eligible node.  Ordinary workloads
    # therefore prove the expected route family, while balance alone must
    # demonstrate all three configured MMR primary clusters.
    "pgbench.mmr_hint_long": ("mmrhint", {"pg_220", "pg_230", "pg_240", "pg_241", "pg_242", "pg_250", "pg_251", "pg_252"}, 1),
    "pgbench.mmr_hint_short": ("mmrhint", {"pg_220", "pg_230", "pg_240", "pg_241", "pg_242", "pg_250", "pg_251", "pg_252"}, 1),
    "pgbench.mmr_port_read": ("mmrport", {"pg_220", "pg_230", "pg_240", "pg_241", "pg_242", "pg_250", "pg_251", "pg_252"}, 1),
    "pgbench.mmr_port_write": ("mmrport", {"pg_220", "pg_230", "pg_240", "pg_241", "pg_242", "pg_250", "pg_251", "pg_252"}, 1),
    "pgbench.rep_hint": ("rephint", {"rep_pg_220", "rep_pg_230", "rep_pg_240"}, 1),
    "pgbench.balance": ("balance", {"pg_220", "pg_230"}, 2),
    "pgbench.console": ("admin", {"default_console"}, 1),
    "pgbench.reload_status_toggle": ("mmrhint", {"pg_220", "pg_230", "pg_240", "pg_241", "pg_242", "pg_250", "pg_251", "pg_252"}, 1),
    "pgbench.ha_node_state": ("admin", {"default_console"}, 1),
    "pgbench.ha_group_route": ("admin", {"default_console"}, 1),
    "pgbench.ha_weight": ("admin", {"default_console"}, 1),
    "pgbench.ha_cluster": ("admin", {"default_console"}, 1),
    "jdbc.prepared_leak": ("mmrhint", {"pg_220", "pg_230", "pg_240", "pg_241", "pg_242", "pg_250", "pg_251", "pg_252"}, 1),
}


def route_startup_pending(output):
    """Return whether a route failure only means monitor bootstrap is pending."""
    text = output.lower()
    return any(message in text for message in (
        "have no node for write operation",
        "no healthy write server is available",
        "have no node for read operation",
        "no healthy read server is available",
    ))


def route_evidence(log_text, workload_names):
    """Extract only client-routing records; group-check records are excluded."""
    routes = {}
    for name in workload_names:
        user, allowed, minimum = ROUTE_RULES[name]
        matched = set(re.findall(
            r"\(routing\) matched rule\((\S+)\s+\S+\s+%s\s+\)" % re.escape(user),
            log_text))
        unexpected = sorted(matched - allowed)
        matched_allowed = matched & allowed
        missing = sorted(allowed - matched) if minimum == len(allowed) else []
        routes[name] = {"user": user, "allowed": sorted(allowed), "minimum": minimum,
                        "matched": sorted(matched), "missing": missing, "unexpected": unexpected,
                        "ok": len(matched_allowed) >= minimum and not unexpected}
    return routes


def pg_log_findings(text):
    """Errors relevant to stable's fixture objects, not unrelated shared-suite traffic."""
    return [line for line in text.splitlines()
            if re.search(r"\b(ERROR|FATAL|PANIC)\b", line, re.I)
            and re.search(r"table_test|test_prepare|mmrhint|mmrport|rephint|balance", line, re.I)]


def pg_log_window_script(postgres_dir, port, user, since, label):
    from platform_regress.evidence.postgresql_logs import postgres_log_window_script
    return postgres_log_window_script(postgres_dir, port, user, since, label, host=LOCAL_HOST,
              patterns=("table_test", "test_prepare", "mmrhint", "mmrport", "rephint", "balance", "ERROR", "FATAL", "PANIC"))


def pg_log_targets(config):
    db = config.runtime_config.config["database"]
    return (
        ("mmr1", db["mmr_host"], db["ports"]["mmr1"], db["mmr_pg_user"]),
        ("mmr2", db["mmr_host"], db["ports"]["mmr2"], db["mmr_pg_user"]),
    )


def pg_log_archive_script(postgres_dir, port, user, since, label):
    from platform_regress.evidence.postgresql_logs import postgres_log_archive_script
    return postgres_log_archive_script(postgres_dir, port, user, since, label, host=LOCAL_HOST,
              archive_subdir="stable_archive", archive_prefix="stable", remove_sources=True)


class StableRuntime(object):
    def __init__(self, root, extra_configs=None, run_id=None):
        self.root = Path(root)
        self.extra_configs = [Path(path) for path in extra_configs or ()]
        self.cfg = StableConfig(root, extra_configs=extra_configs)
        self.command_timeout = self.cfg.runtime_config.config["framework"].get("default_timeout", 60)
        self.store = StateStore(self.cfg.state_file)
        self.run_id = run_id or (datetime.now().strftime("run_%Y%m%d_%H%M%S_") + str(os.getpid()))
        current = self.store.load()
        if (current.get("status") in ("running", "degraded", "finalizing", "stopping")
                and managed_pid(current.get("fbasecman_pid"), current.get("run_dir", ""))):
            raise StableFailure("stable runtime is already running: %s" % current.get("run_id"))
        self.run_dir = self.cfg.output_dir / "current"
        if self.run_dir.exists():
            shutil.rmtree(str(self.run_dir))
        self.logs = self.run_dir / "logs"
        self.product_logs = self.cfg.output_dir / "fbasecman-logs"
        self.product_logs.mkdir(parents=True, exist_ok=True)
        self.product_log = self.product_logs / (self.run_id + ".log")
        self.monitor = self.run_dir / "monitor"
        self.config_dir = self.run_dir / "config"
        self.workload_dir = self.run_dir / "workloads"
        self.diagnostics = self.run_dir / "diagnostics"
        for path in (self.logs, self.monitor, self.config_dir, self.workload_dir, self.diagnostics):
            path.mkdir(parents=True, exist_ok=True)
        fbasecman = self.cfg.runtime_config.config["fbasecman"]
        self.main_port, self.write_port, self.prom_port = choose_ports(
            fbasecman.get("read_port"), fbasecman.get("write_port")
        )
        self.conf = self.config_dir / "fbasecman.conf"
        # Route preflight needs fbasecman's debug-level routing records. It is
        # switched off before the long workload to avoid multi-GB logs.
        self.conf.write_text(render_fbasecman_config(self.cfg, self.run_dir,
                                                     self.main_port, self.write_port,
                                                     debug_logging=True,
                                                     product_log=self.product_log), encoding="utf-8")
        self.process = FbasecmanProcess(
            self.cfg.runtime_config.config["fbasecman"]["fbasecman_bin"],
            self.cfg.runtime_config.config["local"]["postgres_dir"], self.main_port, self.prom_port,
            self.run_dir / "fbasecman.pid", self.run_dir / "locks",
            self.product_log, self.logs,
            self._execute, self._trace, _port_free)
        self.runner = LoggedShellRunner(
            self.logs / "fixture", verbose=False, default_timeout=self.command_timeout,
        )

    def _trace(self, message):
        with (self.logs / "events.log").open("a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (datetime.now().isoformat(), message))

    def _execute(self, command, log_path, check=False, step_title=None, record=True):
        result = run_logged_command(
            command, log_path, cwd=self.run_dir, timeout=self.command_timeout,
        )
        if check and result.returncode:
            raise StableFailure("%s failed: %s" % (step_title or result.command, result.output))
        return result.returncode, result.output

    def health(self):
        from products.fbasecman.environment.health import check_environment
        values = check_environment(self.cfg.runtime_config, self.runner, verbose=False)
        ok = (values.get("mmr_non_active") == "0" and
              values.get("rep_streaming") == "2")
        if not ok:
            raise StableFailure("stable environment unhealthy: %s" % values)
        return values

    def capture_pg_log_windows(self, since):
        """Capture a bounded, read-only PG log window for this stable run."""
        postgres_dir = self.cfg.runtime_config.config["local"]["postgres_dir"]
        output_dir = self.logs / "postgres"
        output_dir.mkdir(parents=True, exist_ok=True)
        findings = []
        for label, host, port, user in pg_log_targets(self.cfg):
            remote = pg_log_window_script(postgres_dir, port, user, since, label)
            result = self.runner.run_remote(user, host, remote, "pg_log_%s.log" % label, check=False)
            target = output_dir / (label + ".log")
            target.write_text(result.stdout + ("\n[stderr]\n" + result.stderr if result.stderr else ""), encoding="utf-8")
            if result.returncode:
                raise StableFailure("PG log capture failed on %s: %s" % (label, result.stderr.strip()))
            findings.extend("%s: %s" % (label, line) for line in pg_log_findings(result.stdout))
        return {"directory": str(output_dir), "findings": findings}

    def compress_pg_log_windows(self, since):
        """Archive this run's closed PostgreSQL logs after their evidence is copied."""
        postgres_dir = self.cfg.runtime_config.config["local"]["postgres_dir"]
        output_dir = self.logs / "postgres-archive"
        output_dir.mkdir(parents=True, exist_ok=True)
        archives = []
        reports = []
        for label, host, port, user in pg_log_targets(self.cfg):
            result = self.runner.run_remote(
                user, host, pg_log_archive_script(postgres_dir, port, user, since, label),
                "pg_log_archive_%s.log" % label, check=False)
            report = output_dir / (label + ".log")
            report.write_text(result.stdout + ("\n[stderr]\n" + result.stderr if result.stderr else ""), encoding="utf-8")
            if result.returncode:
                raise StableFailure("PG log archive failed on %s: %s" % (label, result.stderr.strip()))
            match = re.search(r"^ARCHIVE=(.+)$", result.stdout, re.M)
            archive = match.group(1).strip() if match and match.group(1).strip() else ""
            reports.append(str(report))
            if archive:
                archives.append(archive)
        return {"directory": str(output_dir), "archives": archives, "reports": reports}

    def prepare_fixture(self):
        db = self.cfg.runtime_config.config["database"]
        pg = Path(self.cfg.runtime_config.config["local"]["postgres_dir"]) / "bin" / "psql"
        object_sql = """
DO $do$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mmrhint') THEN CREATE ROLE mmrhint LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mmrport') THEN CREATE ROLE mmrport LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rephint') THEN CREATE ROLE rephint LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='balance') THEN CREATE ROLE balance LOGIN; END IF;
END $do$;
CREATE TABLE IF NOT EXISTS table_test(id SERIAL PRIMARY KEY,data TEXT,insert_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS test_prepare(id SERIAL PRIMARY KEY,data TEXT,insert_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
GRANT ALL ON table_test,test_prepare TO mmrhint,mmrport,rephint,balance;
GRANT ALL ON SEQUENCE table_test_id_seq,test_prepare_id_seq TO mmrhint,mmrport,rephint,balance;
"""
        targets = (
            ("mmr1", db["mmr_host"], db["ports"]["mmr1"], db["mmr_pg_user"]),
            ("mmr2", db["mmr_host"], db["ports"]["mmr2"], db["mmr_pg_user"]),
        )
        # A write is replicated immediately in MMR, while DDL is deliberately
        # local.  Establish the relation on every target before any DML.
        for label, host, port, user in targets:
            result = run_logged_command([str(pg), "-h", host, "-p", str(port), "-U", user,
                                         "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", object_sql],
                                        self.logs / ("fixture_%s.log" % label), cwd=self.run_dir,
                                        timeout=self.command_timeout)
            if result.returncode:
                raise StableFailure("stable fixture failed on %s: %s" % (label, result.output))
        data_sql = """
TRUNCATE table_test, test_prepare RESTART IDENTITY;
INSERT INTO table_test(data) SELECT 'seed-' || g FROM generate_series(1,10) g;
"""
        for label, host, port, user in (targets[0], targets[-1]):
            result = run_logged_command([str(pg), "-h", host, "-p", str(port), "-U", user,
                                         "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", data_sql],
                                        self.logs / ("fixture_data_%s.log" % label), cwd=self.run_dir,
                                        timeout=self.command_timeout)
            if result.returncode:
                raise StableFailure("stable fixture data setup failed on %s: %s" % (label, result.output))
        time.sleep(2)

    def start_product(self):
        self.process.start(self.conf, ready_timeout=30)

    def _console_reload(self):
        command = build_psql_command(
            self.cfg.runtime_config.config["local"]["postgres_dir"], LOCAL_HOST, self.main_port,
            "admin", "console", "RELOAD;", footer=False)
        result = run_logged_command(
            command, self.logs / "fbasecman.reload.log", cwd=self.run_dir,
            timeout=self.command_timeout,
        )
        if result.returncode != 0 or "RELOAD" not in result.output.upper():
            raise StableFailure("fbasecman reload failed: %s" % result.output.strip())
        return command, result

    def set_workload_logging(self):
        """Disable debug only after route preflight has saved its evidence."""
        current = self.conf.read_text(encoding="utf-8")
        updated = current.replace("log_debug yes", "log_debug no")
        if updated == current:
            raise StableFailure("stable configuration does not contain log_debug yes")
        self.conf.write_text(updated, encoding="utf-8")
        command, result = self._console_reload()
        return {"command": command, "returncode": result.returncode, "output": result.output.strip()}

    def verify_workload_routes(self, workloads):
        """Exercise each workload's actual frontend route before starting its load."""
        postgres_dir = self.cfg.runtime_config.config["local"]["postgres_dir"]
        checks = {}
        product_log = self.product_log
        for workload in workloads:
            offset = product_log.stat().st_size if product_log.exists() else 0
            port = self.write_port if workload.port_kind == "write" else self.main_port
            user = workload.user or "mmrhint"
            database = workload.database or "mmrhint"
            sql = "SELECT 1;"
            if workload.database == "console":
                # fbasecman console has its own SET grammar.  Use a documented
                # read-only console command instead of a PostgreSQL GUC.
                sql = "SHOW NODE_STATUS;"
            # Balance is required to use every configured primary. Routing is
            # deliberately random, so sample fresh sessions until all three
            # nodes are observed (or the bounded diagnostic limit is reached)
            # before debug logging is disabled for the long load phase.
            attempts = 12 if workload.name == "pgbench.balance" else 20
            results = []
            command = None
            for attempt in range(attempts):
                marker = "stable_route_%s_%d" % (workload.name.replace(".", "_"), attempt + 1)
                command = build_psql_command(
                    postgres_dir, LOCAL_HOST, port, user, database,
                    sql, footer=False)
                env = os.environ.copy()
                env["PGAPPNAME"] = marker
                result = run_logged_command(
                    command,
                    self.logs / ("route_%s_%d.log" % (workload.name.replace(".", "_"), attempt + 1)),
                    cwd=self.run_dir, env=env, timeout=self.command_timeout)
                results.append({"marker": marker, "returncode": result.returncode,
                                "output": result.output.strip()})
                if result.returncode:
                    # The console listener becomes available before monitor has
                    # published the first writable node. Retry only that narrow
                    # bootstrap condition; every other client failure is final.
                    if route_startup_pending(result.output) and attempt + 1 < attempts:
                        time.sleep(0.5)
                        continue
                    break
                if workload.name == "pgbench.balance" and product_log.exists():
                    sampled = route_evidence(
                        product_log.read_text(encoding="utf-8", errors="replace")[offset:],
                        (workload.name,))[workload.name]
                    if sampled["ok"]:
                        break
                elif workload.name != "pgbench.balance":
                    break
            # The client can receive its result before fbasecman flushes the
            # corresponding debug record.  Wait only for that already-issued
            # request to reach the log; do not retry the business SQL.
            routes = {"ok": False, "matched": [], "missing": [], "unexpected": []}
            for _ in range(20):
                window = product_log.read_text(encoding="utf-8", errors="replace")[offset:] if product_log.exists() else ""
                routes = route_evidence(window, (workload.name,))[workload.name]
                if routes["ok"]:
                    break
                time.sleep(0.1)
            client_ok = any(item["returncode"] == 0 for item in results)
            checks[workload.name] = {"command": command,
                                     "returncode": max(item["returncode"] for item in results),
                                     "attempts": results, "route": routes,
                                     "ok": client_ok and routes["ok"]}
            if not checks[workload.name]["ok"]:
                raise StableFailure("route preflight failed for %s: %s" % (workload.name, checks[workload.name]))
        return checks

    def reload_product(self, state):
        """Re-render the current cluster-model configuration and reload it once."""
        ports = state.get("ports", {})
        main_port = int(ports.get("main", 0))
        write_port = int(ports.get("write", 0))
        if not main_port or not write_port:
            raise StableFailure("stable run has no recorded listener ports")
        if not managed_pid(state.get("fbasecman_pid"), str(self.run_dir)):
            raise StableFailure("cannot reload: current stable fbasecman is not running")
        self.conf.write_text(render_fbasecman_config(
            self.cfg, self.run_dir, main_port, write_port,
            product_log=self.product_log), encoding="utf-8")
        command, result = self._console_reload()
        ok = True
        entry = {"at": datetime.now().isoformat(), "command": command,
                 "returncode": result.returncode, "output": result.output.strip(), "ok": ok}
        state.setdefault("reloads", []).append(entry)
        if not ok:
            raise StableFailure("fbasecman reload failed: %s" % result.output.strip())
        return entry

    def _pgbench_command(self, workload):
        postgres = Path(self.cfg.runtime_config.config["local"]["postgres_dir"])
        port = self.write_port if workload.port_kind == "write" else self.main_port
        sql_path = self._render_pgbench_sql(workload)
        from platform_regress.clients.pgbench import (
            PgbenchRequest,
            filtered_pgbench_command,
        )
        command = PgbenchRequest(str(postgres / "bin" / "pgbench"), LOCAL_HOST,
            port, workload.user, workload.database, str(sql_path),
            workload.clients, workload.jobs,
            connect_per_transaction=workload.connect_per_transaction).argv()
        if workload.name == "pgbench.reload_status_toggle":
            values = self.cfg.values["reload_status_toggle"]
            command.extend(("-T", str(self.cfg.duration("pgbench"))))
            return [
                "bash", str(self.root / "suites" / "stable" / "assets" /
                            "pgbench" / "run_reload_stress.sh"),
                str(self.cfg.duration("pgbench")), str(values["interval_seconds"]),
                str(self.conf), str(values["datasource"]),
                str(postgres / "bin" / "psql"), LOCAL_HOST, str(self.main_port),
            ] + command
        if workload.name.startswith("pgbench.ha_"):
            command.extend(("-t", "1"))
            return ["bash", str(self.root / "suites" / "stable" / "assets" /
                                "pgbench" / "run_ha_pgbench.sh"),
                    str(self.cfg.duration("pgbench"))] + command
        command.extend(("-T", str(self.cfg.duration("pgbench"))))
        return filtered_pgbench_command(command)

    def _render_pgbench_sql(self, workload):
        """Render endpoint targets from the resolved stable configuration."""
        source = self.root / "suites" / "stable" / "assets" / "pgbench" / workload.sql_asset
        text = source.read_text(encoding="utf-8")
        if "{{" not in text:
            return source

        database = self.cfg.runtime_config.config["database"]
        ports = database["ports"]
        values = {
            "mmr_host": database["mmr_host"],
            "mmr1_port": ports["mmr1"],
            "mmr1_standby1_port": ports["mmr1_standby1"],
            "mmr1_standby2_port": ports["mmr1_standby2"],
            "mmr1_standby3_port": ports["mmr1_standby3"],
            "mmr2_port": ports["mmr2"],
            "mmr2_standby1_port": ports["mmr2_standby1"],
            "mmr2_standby2_port": ports["mmr2_standby2"],
        }
        for name, value in values.items():
            text = text.replace("{{%s}}" % name, str(value))
        if "{{" in text:
            raise StableFailure("unresolved pgbench SQL placeholder: %s" % source)

        target = self.config_dir / "pgbench" / workload.sql_asset
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def _jdbc_command(self):
        values = self.cfg.values["jdbc"]
        script = self.root / "suites" / "stable" / "assets" / "jdbc" / "run_prepared_leak.sh"
        return ["bash", str(script), "--workload", "prepared_leak", "--run-dir", str(self.run_dir),
                "--duration", str(self.cfg.duration("jdbc")),
                "--long-clients", str(values["long_conn_clients"]), "--short-clients", str(values["short_conn_clients"]),
                "--shared-sql-count", str(values["shared_sql_count"]), "--private-sql-count", str(values["private_sql_count"]),
                "--short-batch", str(values["short_conn_batch"]), "--short-idle-ms", str(values["short_conn_idle_ms"]),
                "--rw-switch-enabled", str(values["rw_switch_enabled"]).lower(),
                "--rw-switch-interval-ops", str(values["rw_switch_interval_ops"]),
                "--heartbeat-enabled", str(values["heartbeat_enabled"]).lower(),
                "--heartbeat-interval-ops", str(values["heartbeat_interval_ops"]),
                "--guc-enabled", str(values["guc_enabled"]).lower(), "--guc-interval-ops", str(values["guc_interval_ops"]),
                "--guc-distinct-count", str(values["guc_distinct_count"])]

    def launch_workload(self, workload):
        log = self.logs / (workload.name.replace(".", "_") + ".log")
        command = self._jdbc_command() if workload.kind == "jdbc" else self._pgbench_command(workload)
        env = os.environ.copy()
        if workload.kind == "jdbc":
            env.update({"JDBC_URL": f"jdbc:postgresql://{LOCAL_HOST}:{self.main_port}/mmrhint",
                        "JDBC_USER": "mmrhint", "JDBC_PASSWORD": "", "JDBC_PREPARE_THRESHOLD": "1",
                        "JDBC_DIR": str(self.root / self.cfg.runtime_config.config["local"]["jdbc_lib_dir"])})
        process = start_background(command, cwd=self.run_dir, env=env, output_path=log)
        return process, log, command

    def workload_fingerprint(self, workload):
        if workload.kind == "jdbc":
            # Present both in the bash launch arguments and the JVM property
            # after the launcher execs Java.
            return str(self.run_dir)
        if workload.name.startswith("pgbench.ha_"):
            return str(self.root / "suites" / "stable" / "assets" /
                       "pgbench" / "run_ha_pgbench.sh")
        if workload.name == "pgbench.reload_status_toggle":
            return str(self.root / "suites" / "stable" / "assets" /
                       "pgbench" / "run_reload_stress.sh")
        return str(self.root / "suites" / "stable" / "assets" / "pgbench" / workload.sql_asset)

    def start_monitor(self):
        command = [str(self.root / "stable.sh"), "internal-monitor", "--state-file", str(self.cfg.state_file)]
        process = start_background(command, cwd=self.root)
        return process.pid, " ".join(command)

    def start_supervisor(self):
        from suites.stable.supervisor import launch_supervisor

        return launch_supervisor(
            self.root, self.cfg.state_file, self.extra_configs,
            output_path=self.logs / "supervisor.log",
        )

    def initial_state(self, workloads):
        return {"status": "running", "run_id": self.run_id, "run_dir": str(self.run_dir),
                "product_log": str(self.product_log),
                "started_at": int(time.time()), "fbasecman_pid": 0, "monitor_pid": 0,
                "supervisor_pid": 0,
                "workloads": {item.name: {"pid": 0, "status": "pending", "returncode": None,
                                            "duration_seconds": self.cfg.duration(item.kind), "started_at": 0}
                              for item in workloads},
                "commands": {}, "ports": {"main": self.main_port, "write": self.write_port,
                                             "prometheus": self.prom_port}, "reloads": []}

    def foreground(self, workloads):
        validate_profile_isolation(self.cfg.runtime_config)
        with ExclusiveFileLock(self.cfg.output_dir / "stable.lock", "stable runtime"):
            before_health = self.health()
            self.prepare_fixture()
            try:
                self.start_product()
                state = self.initial_state(workloads)
                state["fbasecman_pid"] = int((self.run_dir / "fbasecman.pid").read_text().strip())
                state["commands"][str(state["fbasecman_pid"])] = str(self.conf)
                state["route_preflight"] = self.verify_workload_routes(workloads)
                state["workload_logging"] = self.set_workload_logging()
                self.store.save(state)
            except Exception:
                # A route/configuration failure happens before the normal
                # workload cleanup block. Never leave this run's proxy alive.
                self.process.stop(best_effort=True, record=False)
                raise
            monitor_pid, monitor_command = self.start_monitor()
            state["monitor_pid"] = monitor_pid
            state["commands"][str(monitor_pid)] = "internal-monitor"
            from platform_regress.execution.longrun import WorkloadGroup
            def evaluate(name, text, rc):
                item = find_workload(name)
                return jdbc_result(text, rc) if item.kind == "jdbc" else pgbench_result(
                    text, rc, allow_config_lock_conflict=name.startswith("pgbench.ha_"))
            group = WorkloadGroup(self.store, state, launch=self.launch_workload,
                describe=lambda item: {"duration_seconds": self.cfg.duration(item.kind),
                                       "fingerprint": self.workload_fingerprint(item)},
                evaluate=evaluate)
            completed = False
            try:
                group.run(workloads)
                self.finalize_state(state, before_health)
                self.store.save(state)
                completed = state["status"] == "completed"
            finally:
                from platform_regress.execution.longrun import cleanup_actions
                def stop_product():
                    self.process.stop(best_effort=True, record=False)
                    return not is_alive(state.get("fbasecman_pid"))
                state["cleanup"] = cleanup_actions({
                    "workloads_stopped": group.close,
                    "monitor_stopped": lambda: stop_managed(state.get("monitor_pid"), "internal-monitor") or not is_alive(state.get("monitor_pid")),
                    "fbasecman_stopped": stop_product,
                    "environment_after_cleanup": self.health,
                })
                if state["cleanup"].get("errors"):
                    state["status"] = "failed"
                    completed = False
                self.store.save(state)
                self.write_reports(state, state.get("environment_before"), state.get("environment_after"))
            return completed

    def background(self, workloads):
        validate_profile_isolation(self.cfg.runtime_config)
        with ExclusiveFileLock(self.cfg.output_dir / "stable.lock", "stable start"):
            current = self.store.load()
            if current.get("status") == "running" and managed_pid(current.get("fbasecman_pid"), current.get("run_dir")):
                raise StableFailure("stable runtime is already running: %s" % current.get("run_id"))
            before_health = self.health(); self.prepare_fixture()
            try:
                self.start_product()
                state = self.initial_state(workloads)
                state["fbasecman_pid"] = int((self.run_dir / "fbasecman.pid").read_text().strip())
                state["commands"][str(state["fbasecman_pid"])] = str(self.run_dir)
                state["route_preflight"] = self.verify_workload_routes(workloads)
                state["workload_logging"] = self.set_workload_logging()
                state["environment_before"] = before_health
                state["config_files"] = [str(path) for path in self.extra_configs]
                self.store.save(state)
            except Exception:
                self.process.stop(best_effort=True, record=False)
                raise
            pid, command = self.start_monitor(); state["monitor_pid"] = pid; state["commands"][str(pid)] = "internal-monitor"
            for item in workloads:
                process, log, command = self.launch_workload(item); process.close_output()
                state["workloads"][item.name].update({"pid": process.pid, "status": "running",
                                                       "log": str(log), "command": command,
                                                       "started_at": int(time.time()),
                                                       "duration_seconds": self.cfg.duration(item.kind),
                                                       "fingerprint": self.workload_fingerprint(item)})
                state["commands"][str(process.pid)] = str(self.run_dir)
            self.store.save(state)
            supervisor_pid, supervisor_command = self.start_supervisor()
            def record_supervisor(current):
                current["supervisor_pid"] = supervisor_pid
                current.setdefault("commands", {})[str(supervisor_pid)] = " ".join(supervisor_command)
            self.store.update(record_supervisor)
            return self.store.load()

    def finalize_state(self, state, before_health=None):
        """Finish a run only after workload, product, PG-log, and health checks."""
        after_health = self.health()
        findings = scan_logs(self.run_dir, self.product_log)
        cores = core_files(self.root, state.get("started_at", 0))
        product_log = self.product_log.read_text(encoding="utf-8", errors="replace")
        routes = route_evidence(product_log, state["workloads"].keys())
        preflight = state.get("route_preflight", {})
        pg_logs = self.capture_pg_log_windows(state["started_at"])
        archive_error = ""
        try:
            pg_archives = self.compress_pg_log_windows(state["started_at"])
        except StableFailure as exc:
            archive_error = str(exc)
            pg_archives = {"directory": "", "archives": [], "reports": []}
        state["log_findings"] = findings
        state["core_files"] = [str(path) for path in cores]
        state["route_observations"] = routes
        state["route_evidence"] = {
            name: item.get("route", {}) for name, item in preflight.items()
        }
        state["pg_log_directory"] = pg_logs["directory"]
        state["pg_log_findings"] = pg_logs["findings"]
        state["pg_log_archive_directory"] = pg_archives["directory"]
        state["pg_log_archives"] = pg_archives["archives"]
        state["pg_log_archive_error"] = archive_error
        state["environment_before"] = before_health or state.get("environment_before", {})
        state["environment_after"] = after_health
        target_status = "completed" if (
            all(value["status"] == "completed" for value in state["workloads"].values())
            and not findings and not cores and not pg_logs["findings"] and not archive_error
            and all(item.get("ok") for item in preflight.values())) else "failed"
        transition_status(state, target_status)
        return state

    def write_reports(self, state, before_health=None, after_health=None):
        resources = resource_summary(self.monitor / "resources.csv")
        findings = state.get("log_findings", scan_logs(self.run_dir, self.product_log))
        cores = state.get("core_files", [str(path) for path in core_files(self.root, state.get("started_at", 0))])
        pg_findings = state.get("pg_log_findings", [])
        cleanup = state.get("cleanup", {})
        lines = ["Stable 常稳运行报告", "运行编号: %s" % state.get("run_id"),
                 "状态: %s" % state.get("status"), "开始时间: %s" % datetime.fromtimestamp(state.get("started_at", 0)),
                 "pgbench 时长: %ss" % self.cfg.duration("pgbench"), "JDBC 时长: %ss" % self.cfg.duration("jdbc"),
                 "配置文件: %s" % self.conf, "", "环境健康:", "  before=%s" % before_health, "  after=%s" % after_health,
                 "", "资源统计:", "  %s" % resources, "", "日志与崩溃:",
                 "  fbasecman/workload 未预期错误数: %d" % len(findings), "  新增 core 数: %d" % len(cores),
                 "  PG 业务日志错误数: %d" % len(pg_findings),
                 "  PG 日志窗口: %s" % state.get("pg_log_directory", "<未采集>"),
                 "  PG 日志归档数: %d" % len(state.get("pg_log_archives", [])),
                 "  PG 归档报告: %s" % state.get("pg_log_archive_directory", "<未归档>")]
        if state.get("pg_log_archive_error"):
            lines.append("  PG 日志归档错误: %s" % state["pg_log_archive_error"])
        lines.extend(("", "路由证据:"))
        lines.extend("  %s" % item for item in findings[:20])
        lines.extend("  %s" % item for item in pg_findings[:20])
        lines.extend("  PG archive: %s" % item for item in state.get("pg_log_archives", [])[:20])
        for name, value in sorted(state.get("route_evidence", {}).items()):
            lines.append("  %-32s %s matched=%s missing=%s" %
                         (name, "SUCCESS" if value.get("ok") else "FAIL", value.get("matched"), value.get("missing")))
        lines.extend(["", "路由预检:"])
        for name, value in sorted(state.get("route_preflight", {}).items()):
            lines.append("  %-32s %s matched=%s" %
                         (name, "SUCCESS" if value.get("ok") else "FAIL",
                          value.get("route", {}).get("matched", [])))
        lines.extend(["", "配置重载:"])
        reloads = state.get("reloads", [])
        if reloads:
            for item in reloads:
                lines.append("  %s result=%s output=%s" %
                             (item.get("at"), "SUCCESS" if item.get("ok") else "FAIL",
                              item.get("output") or "<empty>"))
        else:
            lines.append("  本轮未执行显式 RELOAD。")
        lines.extend(["", "Workloads:"])
        for name, value in sorted(state.get("workloads", {}).items()):
            lines.append("  %-32s %s" % (name, value.get("status")))
            report_dir = self.workload_dir / name
            report_dir.mkdir(parents=True, exist_ok=True)
            log = Path(value.get("log", ""))
            actual = log.read_text(encoding="utf-8", errors="replace") if log.exists() else "<missing log>"
            summary = workload_log_summary(actual, find_workload(name).kind)
            command = " ".join(shlex.quote(str(part)) for part in value.get("command", []))
            workload = find_workload(name)
            sql = "<JDBC PreparedLeakMain>"
            if workload.sql_asset:
                sql = (self.root / "suites" / "stable" / "assets" / "pgbench" / workload.sql_asset).read_text(encoding="utf-8").strip()
            (report_dir / "report.txt").write_text(
                "用例: stable.%s\n结论: %s\n命令: %s\n\nSQL/调用:\n%s\n\n"
                "预期: 正常完成且失败计数为 0，路由落在配置的目标 cluster。\n实际结果: %s\n"
                "路由证据: %s\n原始 workload 日志: %s\nfbasecman 日志: %s\n\n实际输出摘要:\n%s\n" %
                (name, "SUCCESS" if value.get("status") == "completed" else "FAIL", command, sql,
                 value.get("result"), state.get("route_evidence", {}).get(name, "<未采集>"), log,
                 self.product_log, summary), encoding="utf-8")
        lines.extend(["", "清理与恢复:"])
        if cleanup:
            lines.extend("  %s=%s" % (key, value) for key, value in sorted(cleanup.items()))
        else:
            lines.append("  后台运行尚未执行 stop；共享 PostgreSQL 不由 stable 停止。")
        (self.run_dir / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def stop_runtime(cfg):
    store = StateStore(cfg.state_file)
    def mark_stopping(value):
        if value.get("status") in ("running", "degraded", "finalizing"):
            transition_status(value, "stopping")
    state = store.update(mark_stopping); expected = state.get("run_dir", "")
    stop_managed(state.get("supervisor_pid"), str(cfg.state_file))
    for item in state.get("workloads", {}).values():
        stop_managed(item.get("pid"), item.get("fingerprint", expected))
    stop_managed(state.get("monitor_pid"), "internal-monitor")
    stop_managed(state.get("fbasecman_pid"), expected)
    if state.get("status") == "stopping":
        transition_status(state, "stopped")
    state["supervisor_pid"] = 0
    store.save(state); return state


def stop_target(cfg, target):
    store = StateStore(cfg.state_file); state = store.load(); expected = state.get("run_dir", "")
    if target in ("all", "fbasecman"):
        return stop_runtime(cfg)
    if target == "monitor":
        stop_managed(state.get("monitor_pid"), "internal-monitor")
        def clear_monitor(current):
            current["monitor_pid"] = 0
        return store.update(clear_monitor)
    else:
        kind = "jdbc" if target.startswith("jdbc") else "pgbench"
        selected = [
            (name, item.get("pid"), item.get("fingerprint", expected))
            for name, item in state.get("workloads", {}).items()
            if name.startswith(kind + ".")
        ]
        def mark_stopping(current):
            for name, _, _ in selected:
                if name in current.get("workloads", {}):
                    current["workloads"][name]["status"] = "stopping"
        store.update(mark_stopping)
        for _, pid, fingerprint in selected:
            stop_managed(pid, fingerprint)
        def mark_stopped(current):
            for name, _, _ in selected:
                if name in current.get("workloads", {}):
                    current["workloads"][name]["status"] = "stopped"
        return store.update(mark_stopped)


def refresh_status(cfg):
    """Compatibility name for a strictly read-only state observation."""
    return StateStore(cfg.state_file).load()


def resource_summary(path):
    path = Path(path)
    if not path.exists(): return "<无监控数据>"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    if not rows: return "<无监控采样>"
    def values(name): return [float(row.get(name, 0) or 0) for row in rows]
    return "samples=%d rss_kb=%d..%d cpu_max=%.2f threads_max=%d fd_max=%d" % (
        len(rows), min(values("rss_kb")), max(values("rss_kb")), max(values("cpu_percent")),
        max(values("threads")), max(values("fd_count")))


def write_report_from_state(cfg):
    state = StateStore(cfg.state_file).load()
    if not state.get("run_dir"): raise StableFailure("no stable run is recorded")
    # `report` is normally invoked after a background run without repeating
    # its --config arguments. Rehydrate those recorded settings so durations
    # and workload options describe the run that actually happened.
    config_files = [Path(path) for path in state.get("config_files", [])]
    report_cfg = StableConfig(cfg.root, config_files) if config_files else cfg
    runtime = runtime_for_state(report_cfg, state)
    runtime.write_reports(
        state, state.get("environment_before"), state.get("environment_after"),
    )
    return runtime.run_dir / "report.txt"


def runtime_for_state(cfg, state):
    """Attach reporting/inspection operations to an existing run without creating one."""
    runtime = StableRuntime.__new__(StableRuntime)
    runtime.root = cfg.root
    runtime.cfg = cfg
    runtime.command_timeout = cfg.runtime_config.config["framework"].get("default_timeout", 60)
    runtime.store = StateStore(cfg.state_file)
    runtime.run_id = state["run_id"]
    runtime.run_dir = Path(state["run_dir"])
    runtime.logs = runtime.run_dir / "logs"
    runtime.product_logs = runtime.cfg.output_dir / "fbasecman-logs"
    runtime.product_log = Path(state.get(
        "product_log", runtime.product_logs / (runtime.run_id + ".log")))
    runtime.monitor = runtime.run_dir / "monitor"
    runtime.config_dir = runtime.run_dir / "config"
    runtime.workload_dir = runtime.run_dir / "workloads"
    runtime.diagnostics = runtime.run_dir / "diagnostics"
    runtime.conf = runtime.config_dir / "fbasecman.conf"
    ports = state.get("ports", {})
    runtime.main_port = int(ports.get("main", 0))
    runtime.write_port = int(ports.get("write", 0))
    runtime.prom_port = int(ports.get("prometheus", 0))
    runtime.runner = LoggedShellRunner(
        runtime.logs / "fixture", verbose=False, default_timeout=runtime.command_timeout,
    )
    return runtime


def workload_log_summary(text, kind):
    lines = text.splitlines()
    if kind == "jdbc":
        selected = [line for line in lines if " CONFIG " in line or " PROGRESS " in line or
                    " ERROR " in line or " RESULT:" in line]
        return "\n".join(selected[-200:]) or "<no JDBC summary>"
    markers = ("transaction type:", "number of clients:", "number of threads:", "duration:",
               "number of transactions actually processed:", "number of failed transactions:",
               "latency average =", "tps =", "statement latencies")
    selected = [line for line in lines if line.startswith(markers)]
    return "\n".join(selected) or "\n".join(lines[-80:])


def monitor_loop(state_file, interval):
    from platform_regress.execution.monitoring import monitor_state
    store = StateStore(state_file)
    output = Path(store.load().get("run_dir", ".")) / "monitor" / "resources.csv"
    return monitor_state(store, pid_key="fbasecman_pid", output=output, interval=interval)


def scan_logs(run_dir, product_log=None):
    findings = []
    paths = list(Path(run_dir).glob("logs/*.log"))
    if product_log is not None and Path(product_log).exists():
        paths.append(Path(product_log))
        paths.extend(Path(product_log).parent.glob(Path(product_log).name + "_bak_*"))
    for path in paths:
        # Product readiness probes intentionally retain failed attempts before
        # the listener becomes available; the successful start is validated
        # separately by FbasecmanProcess.start().
        if path.name.startswith("wait_console_ready_") or path.name == "fbasecman.port_probe.log":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if re.search(r"\b(ERROR|FATAL|PANIC)\b", line, re.I):
                if "another reload or configuration persistence command is running" in line:
                    continue
                findings.append("%s:%d:%s" % (path, number, line))
    return findings


def core_files(root, since=0):
    result = []
    for directory in (Path(root),
                      Path(os.environ.get("FBCMAN_CORE_DIR", "/home/postgres/corefile"))):
        if directory.exists():
            for pattern in ("core*", "*.core"):
                result.extend(path for path in directory.glob(pattern) if path.is_file() and path.stat().st_mtime >= since)
    return sorted(set(result), key=lambda path: path.stat().st_mtime)


def archive_run(run_dir):
    run_dir = Path(run_dir); target = run_dir.parent / (run_dir.name + ".tar.gz")
    with tarfile.open(str(target), "w:gz") as archive: archive.add(str(run_dir), arcname=run_dir.name)
    return target
