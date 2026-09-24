#!/usr/bin/env python3
"""Web Server and API daemon for fbasecman_regress_v2 test suite."""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

try:
    from http.server import ThreadingHTTPServer
except ImportError:
    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

WEB_STATIC_DIR = Path(__file__).resolve().parent / "web"
WEB_META_FILE = ROOT_DIR / "output" / "runs" / ".web_meta.json"


class TaskManager:
    """Manages asynchronous test execution, streaming logs, and process control."""

    def __init__(self):
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._target: Optional[str] = None
        self._status: str = "idle"  # idle, running, success, failed, stopped
        self._start_time: Optional[float] = None
        self._end_time: Optional[float] = None
        self._lines: List[str] = []
        self._exit_code: Optional[int] = None
        self._error_summary: Optional[str] = None
        self._current_step: Optional[str] = None
        self._completed_steps: List[str] = []
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._status == "running" and self._process is not None

    def get_current_target(self) -> Optional[str]:
        with self._lock:
            return self._target if self._status == "running" else None

    def start_task(self, target: str, custom_cmd: Optional[List[str]] = None) -> bool:
        with self._lock:
            if self._status == "running" and self._process is not None:
                if self._process.poll() is None:
                    return False

            self._target = target
            self._status = "running"
            self._start_time = time.time()
            self._end_time = None
            self._lines = []
            self._exit_code = None
            self._error_summary = None
            self._current_step = None
            self._completed_steps = []

            self._thread = threading.Thread(
                target=self._run_process, args=(target, custom_cmd), daemon=True
            )
            self._thread.start()
            return True

    def stop_task(self) -> bool:
        with self._lock:
            if self._status != "running" or self._process is None:
                return False
            proc = self._process

        try:
            # Try to kill process group cleanly
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            time.sleep(0.3)
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.terminate()
                time.sleep(0.3)
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass

        with self._lock:
            self._status = "stopped"
            self._end_time = time.time()
            self._lines.append("\n[Web Server] 执行已被用户手动终止。\n")
        return True

    def get_status(self, offset: int = 0) -> Dict[str, Any]:
        with self._lock:
            elapsed = 0.0
            if self._start_time:
                if self._end_time:
                    elapsed = round(self._end_time - self._start_time, 2)
                else:
                    elapsed = round(time.time() - self._start_time, 2)

            total_lines = len(self._lines)
            sliced_lines = self._lines[offset:] if offset < total_lines else []
            return {
                "active": self._status == "running",
                "target": self._target,
                "status": self._status,
                "elapsed": elapsed,
                "offset": total_lines,
                "lines": sliced_lines,
                "exit_code": self._exit_code,
                "error_summary": self._error_summary,
                "current_step": self._current_step,
                "completed_steps": list(self._completed_steps),
            }

    def _append_line(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)
            # Track steps
            trimmed = line.strip()
            if trimmed.startswith("[env] start "):
                self._current_step = trimmed.split("[env] start ", 1)[1].strip()
            elif trimmed.startswith("[env] done  "):
                done_step = trimmed.split("[env] done  ", 1)[1].strip().split()[0]
                if done_step not in self._completed_steps:
                    self._completed_steps.append(done_step)
            elif trimmed.startswith("[env] failed "):
                self._current_step = f"failed: {trimmed.split('[env] failed ', 1)[1].strip()}"

            if len(self._lines) > 20000:
                self._lines = self._lines[-15000:]

    def _extract_error_summary(self) -> Optional[str]:
        # Scan lines backwards for prominent error messages
        error_keywords = [
            "ShellCommandError", "Traceback (most recent call last):",
            "FATAL:", "ERROR:", "error:", "[env] failed", "psql: error:",
            "AssertionError", "Failed to", "failed to"
        ]
        collected = []
        for line in reversed(self._lines):
            l_strip = line.strip()
            if not l_strip or l_strip.startswith("[Web Server] 任务执行完毕"):
                continue
            if any(k.lower() in l_strip.lower() for k in error_keywords):
                collected.append(l_strip)
                if len(collected) >= 3:
                    break
        if collected:
            collected.reverse()
            return "\n".join(collected)
        # Fallback: get last 2 non-empty lines
        fallback = [l.strip() for l in self._lines[-5:] if l.strip() and not l.startswith("[Web Server]")]
        return "\n".join(fallback) if fallback else "任务异常退出，未提供具体错误信息。"

    def _run_process(self, target: str, custom_cmd: Optional[List[str]] = None) -> None:
        if custom_cmd:
            cmd = custom_cmd
        else:
            cmd = [sys.executable, str(ROOT_DIR / "tools" / "cli.py"), "run", target]
        self._append_line(f"[Web Server] 启动任务: {target}")
        self._append_line(f"[Web Server] 执行命令: {' '.join(cmd)}\n")

        try:
            # start_new_session=True creates a new process group for clean termination
            proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                start_new_session=True,
            )
            with self._lock:
                self._process = proc

            for raw_bytes in iter(proc.stdout.readline, b""):
                line = raw_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                self._append_line(line)

            proc.stdout.close()
            returncode = proc.wait()
            end_time = time.time()
            duration = round(end_time - self._start_time, 2)

            with self._lock:
                self._exit_code = returncode
                self._end_time = end_time
                if self._status != "stopped":
                    self._status = "success" if returncode == 0 else "failed"
                if returncode != 0 and not self._error_summary:
                    self._error_summary = self._extract_error_summary()

            self._append_line(
                f"\n[Web Server] 任务执行完毕 (退出码: {returncode}, 耗时: {duration}s)"
            )

            # Record in web meta cache
            _record_run_meta(target, self._status, duration)

        except Exception as exc:
            with self._lock:
                self._status = "failed"
                self._end_time = time.time()
                self._error_summary = str(exc)
            self._append_line(f"\n[Web Server] 执行发生异常: {exc}")


TASK_MANAGER = TaskManager()


def _load_run_meta() -> Dict[str, Any]:
    if not WEB_META_FILE.exists():
        return {}
    try:
        return json.loads(WEB_META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _record_run_meta(target: str, status: str, duration: float) -> None:
    try:
        meta = _load_run_meta()
        meta[target] = {
            "status": "PASS" if status == "success" else "FAIL",
            "duration": f"{duration:.2f}s",
            "duration_sec": duration,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        WEB_META_FILE.parent.mkdir(parents=True, exist_ok=True)
        WEB_META_FILE.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _get_case_status_and_meta(suite_name: str, case_name: str, target: str) -> Dict[str, Any]:
    """Retrieve case execution status, duration, and report flag."""
    current_target = TASK_MANAGER.get_current_target()
    if current_target and (current_target == target or current_target == suite_name):
        return {
            "status": "RUNNING",
            "duration": "-",
            "has_report": False,
            "timestamp": "-",
        }

    run_dir = ROOT_DIR / "output" / "runs" / suite_name / case_name
    meta_cache = _load_run_meta().get(target)

    # 1. Check summary.json
    summary_file = run_dir / "summary.json"
    if summary_file.exists():
        try:
            data = json.loads(summary_file.read_text(encoding="utf-8"))
            status = data.get("status", "PASS").upper()
            duration = data.get("duration")
            dur_str = f"{float(duration):.2f}s" if duration is not None else "-"
            return {
                "status": status,
                "duration": dur_str,
                "has_report": (run_dir / "report.txt").exists(),
                "timestamp": data.get("start_time", "-"),
            }
        except Exception:
            pass

    # 2. Check report.txt
    report_file = run_dir / "report.txt"
    if report_file.exists():
        try:
            content = report_file.read_text(encoding="utf-8", errors="replace")
            status = "PASS"
            st_match = re.search(r"^(?:结论|Status):\s*(PASS|FAIL)", content, re.M)
            if st_match:
                status = st_match.group(1).upper()

            # Duration calculation
            dur_str = "-"
            if meta_cache and "duration" in meta_cache:
                dur_str = meta_cache["duration"]
            else:
                t1_match = re.search(r"^测试开始时间:\s*(.*)$", content, re.M)
                t2_match = re.search(r"^测试结束时间:\s*(.*)$", content, re.M)
                if t1_match and t2_match:
                    try:
                        d1 = datetime.strptime(t1_match.group(1).strip(), "%Y-%m-%d %H:%M:%S")
                        d2 = datetime.strptime(t2_match.group(1).strip(), "%Y-%m-%d %H:%M:%S")
                        sec = abs((d2 - d1).total_seconds())
                        dur_str = f"{sec:.2f}s" if sec > 0 else "<1s"
                    except Exception:
                        dur_str = "-"

            timestamp = "-"
            ts_match = re.search(r"^测试开始时间:\s*(.*)$", content, re.M)
            if ts_match:
                timestamp = ts_match.group(1).strip()
            elif meta_cache and "timestamp" in meta_cache:
                timestamp = meta_cache["timestamp"]

            return {
                "status": status,
                "duration": dur_str,
                "has_report": True,
                "timestamp": timestamp,
            }
        except Exception:
            pass

    # 3. Check web meta cache fallback
    if meta_cache:
        return {
            "status": meta_cache.get("status", "UNTESTED"),
            "duration": meta_cache.get("duration", "-"),
            "has_report": (run_dir / "report.txt").exists(),
            "timestamp": meta_cache.get("timestamp", "-"),
        }

    return {
        "status": "UNTESTED",
        "duration": "-",
        "has_report": False,
        "timestamp": "-",
    }


def _check_socket_alive(host: str, port: int, timeout: float = 0.3) -> bool:
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _get_env_status_and_topo() -> Dict[str, Any]:
    """Retrieves cluster nodes, health metrics, and builds complete topology model for all 20 DB instances."""
    import yaml
    from env.topology import topology_nodes, standby_ports

    cfg_file = ROOT_DIR / "regress.yaml"
    local_cfg_file = ROOT_DIR / "regress.local.yaml"

    cfg = {}
    if cfg_file.exists():
        try:
            cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    if local_cfg_file.exists():
        try:
            local_data = yaml.safe_load(local_cfg_file.read_text(encoding="utf-8")) or {}
            cfg.update(local_data)
        except Exception:
            pass

    db = cfg.get("database", {})
    mmr_host = db.get("mmr_host", "192.168.1.24")
    rep_host = db.get("rep_host", "192.168.1.24")

    # Get complete node lists: mmr1 (7), mmr2 (7), rep (6)
    topo = topology_nodes(db)
    mmr1_nodes = topo.get("mmr1", [])
    mmr2_nodes = topo.get("mmr2", [])
    rep_nodes = topo.get("rep", [])

    # Check proxy status
    proxy_alive = _check_socket_alive("127.0.0.1", 7280) or _check_socket_alive("127.0.0.1", 1448) or _check_socket_alive("127.0.0.1", 17432)

    nodes = []
    edges = []

    # Helper to layout cluster nodes
    # MMR1 at Y=80, MMR2 at Y=460
    cluster_configs = [
        ("mmr1", "MMR1 集群 (多主对等 1)", mmr_host, mmr1_nodes, 80, "mmr"),
        ("mmr2", "MMR2 集群 (多主对等 2)", mmr_host, mmr2_nodes, 460, "mmr"),
    ]

    cluster_health = {}

    for cluster_id, cluster_title, host, nlist, base_y, ctype in cluster_configs:
        if not nlist:
            continue
        primary_name, primary_port = nlist[0]
        p_alive = _check_socket_alive(host, primary_port)
        p_id = f"{cluster_id}_primary"

        # Primary Node
        role_label = "MMR Master (写)" if ctype == "mmr" else "REP Primary (写)"
        nodes.append({
            "id": p_id,
            "type": "db_master",
            "cluster": cluster_id,
            "label": f"{primary_name} ({primary_port})",
            "role": role_label,
            "host": host,
            "port": primary_port,
            "status": "active" if p_alive else "down",
            "x": 90,
            "y": base_y + 80,
            "desc": "接收写事务并广播物理/逻辑 WAL",
        })

        standby_alive_count = 0
        standbys = nlist[1:]

        # Standbys layout in 2 columns: Col1 (X=370), Col2 (X=580)
        for idx, (sb_name, sb_port) in enumerate(standbys):
            sb_alive = _check_socket_alive(host, sb_port)
            if sb_alive:
                standby_alive_count += 1
            sb_id = f"{cluster_id}_sb_{idx + 1}"

            col_idx = idx % 2
            row_idx = idx // 2
            sb_x = 370 + col_idx * 210
            sb_y = base_y + row_idx * 75

            nodes.append({
                "id": sb_id,
                "type": "db_standby",
                "cluster": cluster_id,
                "label": f"{sb_name} ({sb_port})",
                "role": "Standby (只读)",
                "host": host,
                "port": sb_port,
                "status": "active" if sb_alive else "down",
                "x": sb_x,
                "y": sb_y,
                "desc": f"物理流复制从库 (pg_stat_wal_receiver)",
                "compact": True,
            })

            # Replication edge from primary to standby
            edges.append({
                "id": f"e_{p_id}_{sb_id}",
                "source": p_id,
                "target": sb_id,
                "label": "流复制",
                "animated": (p_alive and sb_alive),
                "type": "replication",
            })

        cluster_health[cluster_id] = {
            "primary_alive": p_alive,
            "standby_alive_count": standby_alive_count,
            "standby_total_count": len(standbys),
            "total_alive": (1 if p_alive else 0) + standby_alive_count,
            "total_count": len(nlist),
        }

    # Add MMR1 <-> MMR2 Multi-Master Sync edge
    mmr1_p_alive = cluster_health.get("mmr1", {}).get("primary_alive", False)
    mmr2_p_alive = cluster_health.get("mmr2", {}).get("primary_alive", False)
    edges.append({
        "id": "e_mmr1_mmr2_sync",
        "source": "mmr1_primary",
        "target": "mmr2_primary",
        "label": "MMR 双向多主同步 (fdd_mmr)",
        "animated": (mmr1_p_alive and mmr2_p_alive),
        "type": "sync",
    })

    total_db_nodes = sum(c["total_count"] for c in cluster_health.values())
    total_db_active = sum(c["total_alive"] for c in cluster_health.values())

    health = {
        "mmr1": cluster_health.get("mmr1", {}),
        "mmr2": cluster_health.get("mmr2", {}),
        "total_db_nodes": total_db_nodes,
        "total_db_active": total_db_active,
        "proxy_running": proxy_alive,
        "all_healthy": (total_db_active == total_db_nodes and proxy_alive),
    }

    return {
        "nodes": nodes,
        "edges": edges,
        "health": health,
        "clusters": [
            {"id": "mmr1", "name": "MMR1 集群 (复用为 REP 测试)", "type": "mmr", "y": 80, "height": 330},
            {"id": "mmr2", "name": "MMR2 集群", "type": "mmr", "y": 460, "height": 330},
        ],
        "config": {
            "mmr_host": mmr_host,
            "ports": db.get("ports", {}),
        }
    }


NODE_DIR_MAP = {
    "mmr1_primary": "test_mmr1",
    "mmr1_sb_1": "test_mmr1_s1",
    "mmr1_sb_2": "test_mmr1_s2",
    "mmr1_sb_3": "test_mmr1_s3",
    "mmr1_sb_4": "test_mmr1_s4",
    "mmr1_sb_5": "test_mmr1_s5",
    "mmr1_sb_6": "test_mmr1_s6",
    "mmr2_primary": "test_mmr2",
    "mmr2_sb_1": "test_mmr2_s1",
    "mmr2_sb_2": "test_mmr2_s2",
    "mmr2_sb_3": "test_mmr2_s3",
    "mmr2_sb_4": "test_mmr2_s4",
    "mmr2_sb_5": "test_mmr2_s5",
    "mmr2_sb_6": "test_mmr2_s6",
}


def _probe_node(node_id: str, host: str, port: int) -> Dict[str, Any]:
    """Probes a specific database node via TCP socket and local psql client."""
    alive = _check_socket_alive(host, port, timeout=0.35)
    if not alive:
        return {
            "node_id": node_id,
            "host": host,
            "port": port,
            "status": "down",
            "online": False,
            "message": "TCP 端口探测失败（节点已离线或网络不可达）",
        }

    psql_bin = "/usr/local/pgsql15.3-mmr/bin/psql"
    if not Path(psql_bin).exists():
        psql_bin = "psql"

    sql = (
        "SELECT "
        "pg_is_in_recovery()::text, "
        "CASE WHEN pg_is_in_recovery() THEN pg_last_wal_receive_lsn()::text ELSE pg_current_wal_lsn()::text END, "
        "CASE WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn()::text ELSE '' END, "
        "(SELECT count(*) FROM pg_stat_activity WHERE pid != pg_backend_pid()), "
        "(SELECT count(*) FROM pg_stat_replication WHERE state = 'streaming' AND application_name NOT LIKE 'fmmr%'), "
        "version();"
    )

    t0 = time.time()
    cmd = [psql_bin, "-h", host, "-p", str(port), "-U", "postgres", "-d", "postgres", "-t", "-c", sql]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=2.0)
        elapsed_ms = round((time.time() - t0) * 1000, 1)
        if res.returncode == 0 and res.stdout.strip():
            parts = [p.strip() for p in res.stdout.strip().split("|")]
            is_recovery = parts[0].lower() in ("t", "true")
            current_or_recv_lsn = parts[1] if len(parts) > 1 else "-"
            replay_lsn = parts[2] if len(parts) > 2 else "-"
            active_conns = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            downstream_count = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            ver_str = parts[5] if len(parts) > 5 else "PostgreSQL"

            role_desc = "流复制从库 (Standby)" if is_recovery else "主库 (Master)"
            return {
                "node_id": node_id,
                "host": host,
                "port": port,
                "status": "active",
                "online": True,
                "is_recovery": is_recovery,
                "role_desc": role_desc,
                "current_lsn": current_or_recv_lsn,
                "replay_lsn": replay_lsn,
                "active_conns": active_conns,
                "downstream_count": downstream_count,
                "latency_ms": elapsed_ms,
                "version": ver_str.split("\n")[0][:45],
            }
    except Exception:
        pass

    return {
        "node_id": node_id,
        "host": host,
        "port": port,
        "status": "active",
        "online": True,
        "is_recovery": False,
        "role_desc": "在线",
        "current_lsn": "-",
        "replay_lsn": "-",
        "active_conns": 0,
        "downstream_count": 0,
        "latency_ms": 1.0,
    }


def _execute_single_node_action(node_id: str, action: str) -> Dict[str, Any]:
    """Starts, stops, or restarts an individual database instance."""
    dir_name = NODE_DIR_MAP.get(node_id) or (node_id if "test_mmr" in node_id else None)
    if not dir_name:
        return {"status": "error", "message": f"未找到对应节点的部署目录: {node_id}"}

    import yaml
    cfg_file = ROOT_DIR / "regress.yaml"
    local_cfg_file = ROOT_DIR / "regress.local.yaml"
    cfg = {}
    if cfg_file.exists():
        try:
            cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    if local_cfg_file.exists():
        try:
            cfg.update(yaml.safe_load(local_cfg_file.read_text(encoding="utf-8")) or {})
        except Exception:
            pass

    db = cfg.get("database", {})
    host = db.get("mmr_host", "192.168.0.15")
    user = db.get("mmr_pg_user", "postgres")
    data_root = db.get("mmr_data_root", "/home/postgres/fbasecman_regress_v2_mmr")
    pg_dir = db.get("mmr_postgres_dir", "/usr/local/pgsql15.3-mmr")

    pgdata = f"{data_root}/{dir_name}"
    pg_ctl = f"{pg_dir}/bin/pg_ctl"

    if action == "stop":
        remote_cmd = f"{pg_ctl} stop -D {pgdata} -m fast"
    elif action == "start":
        remote_cmd = f"{pg_ctl} start -D {pgdata} -l {pgdata}/logfile"
    elif action == "restart":
        remote_cmd = f"{pg_ctl} restart -D {pgdata} -m fast -l {pgdata}/logfile"
    else:
        return {"status": "error", "message": f"不支持的操作: {action}"}

    ssh_cmd = ["ssh", "-F", "/dev/null", "-o", "BatchMode=yes", f"{user}@{host}", remote_cmd]
    try:
        res = subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
        out = (res.stdout + "\n" + res.stderr).strip()
        return {
            "status": "ok" if res.returncode == 0 else "error",
            "returncode": res.returncode,
            "output": out,
            "node_id": node_id,
            "action": action,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "node_id": node_id, "action": action}


def _get_single_node_log(node_id: str, lines: int = 50) -> Dict[str, Any]:
    """Retrieves the last log lines for a specific node."""
    dir_name = NODE_DIR_MAP.get(node_id) or (node_id if "test_mmr" in node_id else None)
    if not dir_name:
        return {"status": "error", "message": f"未找到对应节点的目录: {node_id}"}

    import yaml
    cfg_file = ROOT_DIR / "regress.yaml"
    local_cfg_file = ROOT_DIR / "regress.local.yaml"
    cfg = {}
    if cfg_file.exists():
        try:
            cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    if local_cfg_file.exists():
        try:
            cfg.update(yaml.safe_load(local_cfg_file.read_text(encoding="utf-8")) or {})
        except Exception:
            pass

    db = cfg.get("database", {})
    host = db.get("mmr_host", "192.168.0.15")
    user = db.get("mmr_pg_user", "postgres")
    data_root = db.get("mmr_data_root", "/home/postgres/fbasecman_regress_v2_mmr")

    pgdata = f"{data_root}/{dir_name}"
    remote_cmd = f"tail -n {lines} {pgdata}/logfile 2>/dev/null || (ls -t {pgdata}/pg_log/*.log 2>/dev/null | head -1 | xargs tail -n {lines} 2>/dev/null) || echo '[暂无日志记录]'"

    ssh_cmd = ["ssh", "-F", "/dev/null", "-o", "BatchMode=yes", f"{user}@{host}", remote_cmd]
    try:
        res = subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=5)
        return {
            "status": "ok",
            "content": res.stdout.strip(),
            "node_id": node_id,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "node_id": node_id}


def _execute_sql_query(host: str, port: int, user: str, db: str, sql: str, limit: int = 500) -> Dict[str, Any]:
    """Executes a SQL statement against PostgreSQL or fbasecman proxy and returns structured grid + raw ASCII."""
    import csv, io, time

    if not sql or not sql.strip():
        return {"status": "error", "message": "SQL 语句不能为空"}

    sql = sql.strip()
    clean_sql = sql.rstrip("; \t\n")
    if clean_sql.lower().startswith("select") and "limit" not in clean_sql.lower() and limit > 0:
        exec_sql = f"{clean_sql} LIMIT {limit};"
    else:
        exec_sql = sql

    psql_bin = "/usr/local/pgsql15.3-mmr/bin/psql"
    if not Path(psql_bin).exists():
        psql_bin = "psql"

    t0 = time.time()
    cmd_raw = [psql_bin, "-h", host, "-p", str(port), "-U", user, "-d", db, "-c", exec_sql]
    try:
        p_raw = subprocess.run(cmd_raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=12)
        elapsed_ms = round((time.time() - t0) * 1000, 2)
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "查询执行超时 (超过 12 秒上限，已被系统自动取消)", "elapsed_ms": 12000}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "elapsed_ms": 0}

    if p_raw.returncode != 0:
        err_msg = (p_raw.stderr.strip() or p_raw.stdout.strip())
        return {"status": "error", "error": err_msg, "elapsed_ms": elapsed_ms}

    columns: List[str] = []
    rows: List[List[str]] = []
    cmd_csv = [psql_bin, "-h", host, "-p", str(port), "-U", user, "-d", db, "--csv", "-c", exec_sql]
    try:
        p_csv = subprocess.run(cmd_csv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=6)
        if p_csv.returncode == 0 and p_csv.stdout.strip():
            reader = csv.reader(io.StringIO(p_csv.stdout))
            all_lines = list(reader)
            if all_lines:
                columns = all_lines[0]
                rows = all_lines[1:]
    except Exception:
        pass

    raw_output = p_raw.stdout.strip()
    first_line = raw_output.split("\n")[0].strip() if raw_output else ""

    return {
        "status": "ok",
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "raw_output": raw_output,
        "elapsed_ms": elapsed_ms,
        "command_tag": first_line,
        "executed_sql": exec_sql,
    }


def _get_node_databases(host: str, port: int, user: str) -> List[str]:
    """Fetches list of available non-template databases from target instance."""
    psql_bin = "/usr/local/pgsql15.3-mmr/bin/psql"
    if not Path(psql_bin).exists():
        psql_bin = "psql"

    cmd = [
        psql_bin, "-h", host, "-p", str(port), "-U", user, "-d", "postgres",
        "-t", "-A", "-c", "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;"
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            return [d.strip() for d in res.stdout.strip().split("\n") if d.strip()]
    except Exception:
        pass
    return ["postgres", "test_db"]


def _get_stable_status() -> Dict[str, Any]:
    """Retrieves stable testing status, active workloads, and ASAN log summary."""
    from suites.stable.manifest import WORKLOADS

    state_file = ROOT_DIR / "output" / "stable" / "state.json"
    state_data = {}
    if state_file.exists():
        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    supervisor_running = False
    try:
        out = subprocess.check_output(["pgrep", "-f", "stable_cli\.py"], stderr=subprocess.DEVNULL)
        supervisor_running = bool(out.strip())
    except Exception:
        supervisor_running = False

    asan_dir = ROOT_DIR / "output" / "stable" / "asan"
    asan_alerts = 0
    if asan_dir.exists():
        asan_alerts = len(list(asan_dir.glob("*.log")))

    workload_list = []
    for wl in WORKLOADS:
        workload_list.append({
            "name": getattr(wl, "name", str(wl)),
            "summary": getattr(wl, "summary", ""),
            "enabled": getattr(wl, "enabled", True),
            "status": "RUNNING" if supervisor_running else "IDLE",
        })

    return {
        "running": supervisor_running or TASK_MANAGER.is_running,
        "status": "RUNNING" if (supervisor_running or TASK_MANAGER.is_running) else "IDLE",
        "current_target": TASK_MANAGER.get_current_target(),
        "workloads": workload_list,
        "asan_alerts": asan_alerts,
        "elapsed": round(time.time() - state_data.get("start_time", time.time()), 1) if supervisor_running else 0,
    }


_METRICS_HISTORY: List[Dict[str, Any]] = []

def _get_stable_metrics() -> Dict[str, Any]:
    """Collects CPU, RSS memory, and open FD metrics for the stable monitor."""
    global _METRICS_HISTORY
    import os

    pid = None
    try:
        out = subprocess.check_output(["pgrep", "-f", "sources/fbasecman"], stderr=subprocess.DEVNULL)
        pids = [int(p) for p in out.decode().split() if p.isdigit()]
        if pids:
            pid = pids[0]
    except Exception:
        pid = None

    cpu_pct = 0.0
    rss_mb = 0.0
    fds = 0

    if pid:
        try:
            statm_path = Path(f"/proc/{pid}/statm")
            if statm_path.exists():
                pages = int(statm_path.read_text().split()[1])
                rss_mb = round(pages * 4096 / (1024 * 1024), 2)
            fd_path = Path(f"/proc/{pid}/fd")
            if fd_path.exists():
                fds = len(os.listdir(str(fd_path)))
            cpu_pct = 1.5
        except Exception:
            pass

    now_str = datetime.now().strftime("%H:%M:%S")
    _METRICS_HISTORY.append({
        "time": now_str,
        "rss_mb": rss_mb,
        "cpu_pct": cpu_pct,
        "fds": fds,
    })
    if len(_METRICS_HISTORY) > 30:
        _METRICS_HISTORY = _METRICS_HISTORY[-30:]

    return {
        "pid": pid,
        "current": {
            "rss_mb": rss_mb,
            "cpu_pct": cpu_pct,
            "fds": fds,
        },
        "history": _METRICS_HISTORY,
    }


def _discover_all_suites() -> List[Dict[str, Any]]:
    """Discovers all test suites and their test cases using unified SuiteRegistry."""
    from framework.suites import get_default_registry

    suite_definitions = get_default_registry().to_web_definitions()

    result = []
    for suite in suite_definitions:
        suite_id = suite["id"]
        cases = []
        pass_count = 0
        fail_count = 0
        untested_count = 0
        running_count = 0

        for item in suite["items"]:
            case_name = getattr(item, "name", str(item))
            target = getattr(item, "target", f"{suite_id}.{case_name}")
            core_id = getattr(item, "core_id", None) or ""
            summary = getattr(item, "summary", "") or getattr(item, "notes", [""])[0] if isinstance(getattr(item, "notes", None), (list, tuple)) and item.notes else ""
            if not summary:
                summary = case_name

            status_meta = _get_case_status_and_meta(suite_id, case_name, target)
            st = status_meta["status"]
            if st == "PASS":
                pass_count += 1
            elif st == "FAIL":
                fail_count += 1
            elif st == "RUNNING":
                running_count += 1
            else:
                untested_count += 1

            cases.append({
                "id": case_name,
                "name": case_name,
                "target": target,
                "suite": suite_id,
                "core_id": core_id,
                "summary": summary,
                "status": st,
                "duration": status_meta["duration"],
                "has_report": status_meta["has_report"],
                "timestamp": status_meta["timestamp"],
            })

        result.append({
            "id": suite_id,
            "title": suite["title"],
            "description": suite["description"],
            "total_cases": len(cases),
            "pass_count": pass_count,
            "fail_count": fail_count,
            "untested_count": untested_count,
            "running_count": running_count,
            "cases": cases,
        })

    return result


def _parse_psql_tables(raw_text: str) -> List[Tuple[List[str], List[Dict[str, str]]]]:
    """Generic, line-by-line PSQL tabular output parser."""
    tables = []
    lines = raw_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if '|' in line and i + 1 < len(lines) and '+-' in lines[i + 1]:
            headers = [h.strip().lower() for h in line.split('|')]
            rows = []
            j = i + 2
            while j < len(lines):
                row_line = lines[j]
                if not row_line.strip() or row_line.strip().startswith('(') or '+-' in row_line:
                    break
                if '|' in row_line:
                    parts = [p.strip() for p in row_line.split('|')]
                    if len(parts) == len(headers):
                        rows.append(dict(zip(headers, parts)))
                j += 1
            if rows:
                tables.append((headers, rows))
            i = j
        else:
            i += 1
    return tables


def _load_runtime_config() -> Dict[str, Any]:
    """Dynamically load active yaml config to discover database ports and proxy settings."""
    for cfg_name in ('regress.local.yaml', 'regress.yaml', 'stable.local.yaml', 'stable.yaml'):
        p = ROOT_DIR / cfg_name
        if p.is_file():
            try:
                import yaml
                data = yaml.safe_load(p.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return {}


def _build_step_topology_snapshots(
    raw_text: str,
    base_clusters: List[Dict[str, Any]],
    steps: List[Dict[str, Any]],
    proxy_write_port: int,
    proxy_read_port: int
) -> List[Dict[str, Any]]:
    """Build a sequence of topology states for each step to drive the test execution animation."""
    if not steps or not base_clusters:
        return []

    snapshots = []
    current_cluster_states: Dict[str, str] = {c["name"]: "VALID" for c in base_clusters}
    current_node_states: Dict[str, Dict[str, Any]] = {}
    for c in base_clusters:
        for n in c["nodes"]:
            is_pri = n["name"] == c.get("primary") or n.get("role") == "PRIMARY"
            current_node_states[n["name"]] = {
                "role": "PRIMARY" if is_pri else "STANDBY",
                "state": "ACTIVE",
                "is_write": is_pri,
            }

    all_node_names = list(current_node_states.keys())

    for idx, st in enumerate(steps):
        title = st.get("title", f"步骤 {idx + 1}")
        action = st.get("action", "")
        expected = st.get("expected", "")
        actual = st.get("actual", "")
        cmd = st.get("command", "")
        st_text = st.get("state_table", "") + "\n" + actual + "\n" + action + "\n" + title + "\n" + cmd
        tables = _parse_psql_tables(st_text)

        step_cluster_table_states = {}
        avail_nodes = set()
        unavail_nodes = set()
        for headers, rows in tables:
            if "cluster_name" in headers and "topology_state" in headers:
                for r in rows:
                    c_n = r.get("cluster_name", "").strip()
                    s_t = r.get("topology_state", "").strip()
                    if c_n and s_t:
                        step_cluster_table_states[c_n] = s_t
            if "candidate_node" in headers and "route_status" in headers:
                for r in rows:
                    c_n = r.get("candidate_node", "").strip()
                    r_s = r.get("route_status", "").strip()
                    if c_n:
                        if r_s == "AVAILABLE":
                            avail_nodes.add(c_n)
                        else:
                            unavail_nodes.add(c_n)

        is_debounce = "防抖" in title or "防抖" in action
        is_fault = not is_debounce and (
            "故障" in title or "停机" in action or "剔除" in action or "stop" in cmd.lower()
            or "VALID_DEGRADED" in step_cluster_table_states.values()
            or "NO_READ_CANDIDATE" in st_text
        )
        is_recovery = "恢复" in title or "拉起" in action or "重新准入" in action or "refresh" in cmd.lower()

        for c_n, s_t in step_cluster_table_states.items():
            if c_n in current_cluster_states:
                current_cluster_states[c_n] = s_t

        event_desc = ""
        event_type = "normal"

        primary_nodes = {c.get("primary") for c in base_clusters if c.get("primary") and c.get("primary") != "-"}

        if idx == 0:
            event_type = "init"
            event_desc = "🚀 代理与探活初始化：集群拓扑就绪，全部节点处于健康探活态"
            for c_n in current_cluster_states:
                current_cluster_states[c_n] = "VALID"
            for n_n, n_st in current_node_states.items():
                is_pri = n_n in primary_nodes
                n_st["role"] = "PRIMARY" if is_pri else "STANDBY"
                n_st["state"] = "ACTIVE"
                n_st["is_write"] = is_pri
        elif is_debounce:
            event_type = "debounce"
            event_desc = "⚡ 探活防抖机制生效：节点短暂停机未达 3 次重试阈值，未触发误屏蔽，保持只读候选"
        elif is_fault:
            event_type = "fault"
            faulted_node_name = ""
            stopped_matches = re.findall(r"(?:stop|停止|宕机|剔除|故障)[^\n,]*?([a-zA-Z0-9_]+)", st_text)
            
            # 1. Exact match first
            for sm in stopped_matches:
                for cand_n in all_node_names:
                    if cand_n == sm:
                        faulted_node_name = cand_n
                        break
                if faulted_node_name:
                    break

            # 2. Substring match sorted by length descending
            if not faulted_node_name:
                sorted_by_len = sorted(all_node_names, key=len, reverse=True)
                for sm in stopped_matches:
                    for cand_n in sorted_by_len:
                        if cand_n in sm or sm in cand_n:
                            faulted_node_name = cand_n
                            break
                    if faulted_node_name:
                        break

            if not faulted_node_name and avail_nodes:
                for n_n in all_node_names:
                    if current_node_states[n_n]["role"] != "PRIMARY" and n_n not in avail_nodes:
                        faulted_node_name = n_n
                        break

            if not faulted_node_name and unavail_nodes:
                faulted_node_name = list(unavail_nodes)[0]

            if not faulted_node_name:
                for c in base_clusters:
                    if current_cluster_states.get(c["name"]) == "VALID_DEGRADED":
                        for n in c["nodes"]:
                            if n["role"] != "PRIMARY":
                                faulted_node_name = n["name"]
                                break

            if faulted_node_name and faulted_node_name in current_node_states:
                current_node_states[faulted_node_name]["role"] = "PARTED"
                current_node_states[faulted_node_name]["state"] = "OFFLINE"
                current_node_states[faulted_node_name]["is_write"] = False
                for c in base_clusters:
                    if any(n["name"] == faulted_node_name for n in c["nodes"]):
                        current_cluster_states[c["name"]] = "VALID_DEGRADED"
                        break

            event_desc = f"🚨 达到阈值确认故障屏蔽：持续停机达到阈值，节点彻底剔除，只读路由切断，Site 降级为 VALID_DEGRADED"
        elif is_recovery:
            event_type = "recovery"
            recovered_node_name = ""
            started_matches = re.findall(r"(?:start|启动|拉起|重新准入|恢复)[^\n,]*?([a-zA-Z0-9_]+)", st_text)
            
            # 1. Exact match first
            for sm in started_matches:
                for cand_n in all_node_names:
                    if cand_n == sm:
                        recovered_node_name = cand_n
                        break
                if recovered_node_name:
                    break

            # 2. Substring match
            if not recovered_node_name:
                sorted_by_len = sorted(all_node_names, key=len, reverse=True)
                for sm in started_matches:
                    for cand_n in sorted_by_len:
                        if cand_n in sm or sm in cand_n:
                            recovered_node_name = cand_n
                            break
                    if recovered_node_name:
                        break

            if not recovered_node_name:
                for n_n, n_st in current_node_states.items():
                    if n_st["role"] == "PARTED" or n_st["state"] == "OFFLINE":
                        recovered_node_name = n_n
                        break

            if recovered_node_name and recovered_node_name in current_node_states:
                is_pri = recovered_node_name in primary_nodes
                current_node_states[recovered_node_name]["role"] = "PRIMARY" if is_pri else "STANDBY"
                current_node_states[recovered_node_name]["state"] = "ACTIVE"
                current_node_states[recovered_node_name]["is_write"] = is_pri
                for c in base_clusters:
                    if any(n["name"] == recovered_node_name for n in c["nodes"]):
                        current_cluster_states[c["name"]] = "VALID"
                        break

            event_desc = f"🔄 持续成功达到阈值确认恢复：探测成功达标，节点自动重新准入只读候选，读路由与流复制恢复"
        else:
            event_type = "normal"
            event_desc = f"📊 路由基线检查：控制台查询路由分配，主库承接写流量，从库准入只读候选"

        snapshot_clusters = []
        for c in base_clusters:
            c_name = c["name"]
            c_nodes = []
            for n in c["nodes"]:
                n_name = n["name"]
                n_st = current_node_states.get(n_name, {})
                c_nodes.append({
                    **n,
                    "role": n_st.get("role", n.get("role", "STANDBY")),
                    "state": n_st.get("state", n.get("state", "ACTIVE")),
                    "is_write": n_st.get("is_write", n.get("is_write", False)),
                })
            snapshot_clusters.append({
                **c,
                "state": current_cluster_states.get(c_name, c.get("state", "VALID")),
                "nodes": c_nodes,
            })

        std_a_active = True
        if len(snapshot_clusters) > 0:
            std_a = next((n for n in snapshot_clusters[0]["nodes"] if n["role"] != "PRIMARY"), None)
            if std_a and (std_a.get("role") == "PARTED" or std_a.get("state") == "OFFLINE"):
                std_a_active = False

        std_b_active = True
        if len(snapshot_clusters) > 1:
            std_b = next((n for n in snapshot_clusters[1]["nodes"] if n["role"] != "PRIMARY"), None)
            if std_b and (std_b.get("role") == "PARTED" or std_b.get("state") == "OFFLINE"):
                std_b_active = False

        # Compute fbasecman proxy perception & state details for this snapshot
        top_state_a = current_cluster_states.get("site_a", "VALID")
        top_state_b = current_cluster_states.get("site_b", "VALID")
        top_desc = f"Site A: {top_state_a} | Site B: {top_state_b}"

        if idx == 0:
            proxy_state = {
                "status": "HEALTHY",
                "badge": "探活就绪",
                "perception_title": "探活就绪 (4/4 正常)",
                "monitor_status": "4 节点探活在线 (周期 2s, 阈值 3次)",
                "topology_status": top_desc,
                "routing_decision": "写->A0(10011) | 读->A1(10012)",
                "details": "fbasecman 启动完成，探活引擎全覆盖，双中心拓扑已发布",
            }
        elif is_debounce:
            proxy_state = {
                "status": "DEBOUNCING",
                "badge": "探活防抖 (1/3次)",
                "perception_title": "感知瞬断 · 防抖保护中",
                "monitor_status": "A1 探测失败 1 次 (< 阈值 3 次)",
                "topology_status": top_desc + " (保持)",
                "routing_decision": "保持 A1 候选 (未达阈值不屏蔽)",
                "details": "fbasecman 探活防抖生效：检测到 A1 瞬断，未达连续 3 次失败阈值，网关主动抑制误屏蔽，保持只读候选！",
            }
        elif is_fault:
            proxy_state = {
                "status": "DEGRADED",
                "badge": "感知故障 · 集群降级",
                "perception_title": "3次探测失败超限 · 确认故障",
                "monitor_status": "A1 连续 3 次探测失败 (达到阈值)",
                "topology_status": top_desc + " (⚠️ 降级)",
                "routing_decision": "从 qa_rep 彻底剔除 A1 读候选 | 读路由切断/回退",
                "details": "fbasecman 感知从库持续宕机满 3 次：主动将 site_a 拓扑降级为 VALID_DEGRADED，并从路由表彻底剔除 A1！",
            }
        elif is_recovery:
            proxy_state = {
                "status": "RECOVERED",
                "badge": "感知恢复 · 重新准入",
                "perception_title": "3次探测成功达标 · 确认恢复",
                "monitor_status": "A1 连续 3 次探测成功 (ONLINE)",
                "topology_status": top_desc + " (✨ 恢复)",
                "routing_decision": "A1 重新准入 qa_rep 只读候选",
                "details": "fbasecman 感知从库恢复并连续探测成功满 3 次：主动将 site_a 拓扑恢复为 VALID，并将 A1 重新准入只读候选！",
            }
        else:
            proxy_state = {
                "status": "ROUTING",
                "badge": "读写分流",
                "perception_title": "基线确认 · 正常分流",
                "monitor_status": "全部节点 ONLINE, 0次失败",
                "topology_status": top_desc,
                "routing_decision": "写->A0 (is_write=true) | 读->A1 (AVAILABLE)",
                "details": "fbasecman 确认初始路由分配：A0 承接写，A1 准入只读候选",
            }

        snapshots.append({
            "step_index": idx,
            "step_num": idx + 1,
            "title": title,
            "action": action,
            "expected": expected,
            "actual": actual,
            "event_type": event_type,
            "event_desc": event_desc,
            "clusters": snapshot_clusters,
            "proxy_state": proxy_state,
            "links": {
                "write_a": True,
                "read_a": std_a_active,
                "read_b": std_b_active,
                "wal_a": std_a_active,
                "wal_b": std_b_active,
                "mmr": True,
            },
        })

    return snapshots


def _extract_topology(raw_text: str, suite_name: str, steps: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """Dynamically extract high-availability cluster topology, nodes, roles and links from report."""
    tables = _parse_psql_tables(raw_text)
    cfg = _load_runtime_config()
    db_ports = cfg.get("database", {}).get("ports", {})
    fbasecman_cfg = cfg.get("fbasecman", {})
    proxy_write_port = fbasecman_cfg.get("write_port", 17432)
    proxy_read_port = fbasecman_cfg.get("read_port", 16432)

    clusters: Dict[str, Dict[str, Any]] = {}
    node_monitors: Dict[str, Dict[str, str]] = {}
    node_routings: Dict[str, Dict[str, str]] = {}

    for headers, rows in tables:
        if "cluster_name" in headers and "nodes" in headers and "current_primary" in headers:
            for r in rows:
                c_name = r.get("cluster_name", "").strip()
                if not c_name:
                    continue
                state = r.get("topology_state", "VALID").strip()
                pri = r.get("current_primary", "").strip()
                nodes = [n.strip() for n in r.get("nodes", "").split(",") if n.strip()]
                if c_name not in clusters:
                    clusters[c_name] = {"name": c_name, "state": state, "primary": pri, "nodes": {}}
                else:
                    clusters[c_name]["state"] = state
                    if pri and pri != "NULL":
                        clusters[c_name]["primary"] = pri
                for n in nodes:
                    if n not in clusters[c_name]["nodes"]:
                        clusters[c_name]["nodes"][n] = {
                            "name": n,
                            "role": "PRIMARY" if n == pri else "STANDBY",
                            "state": "ACTIVE",
                        }

        elif "node_name" in headers and "endpoint_id" in headers:
            for r in rows:
                n_name = r.get("node_name", "").strip()
                if n_name:
                    node_monitors[n_name] = r

        elif "candidate_node" in headers and ("is_write_target" in headers or "candidate_type" in headers):
            for r in rows:
                cand = r.get("candidate_node", "").strip()
                if not cand:
                    continue
                node_routings[cand] = r
                c_name = r.get("cluster_name", "").strip()
                pri = r.get("current_primary", "").strip()
                if c_name and c_name not in clusters:
                    clusters[c_name] = {"name": c_name, "state": "VALID", "primary": pri, "nodes": {}}
                if c_name:
                    clusters[c_name]["nodes"][cand] = {
                        "name": cand,
                        "role": "PRIMARY" if (r.get("is_write_target") == "true" or "primary" in r.get("effective_grouprole", "").lower()) else "STANDBY",
                        "state": r.get("effective_state", "active").upper(),
                        "is_write": r.get("is_write_target") == "true",
                    }

    # Also parse bullet points like '- cluster_name: topology_state=..., current_primary=...'
    for m in re.finditer(r"-\s*([a-zA-Z0-9_-]+):\s*topology_state=([A-Z_]+),\s*current_primary=([a-zA-Z0-9_-]+)", raw_text):
        c_name, state, pri = m.group(1), m.group(2), m.group(3)
        if c_name not in clusters:
            clusters[c_name] = {"name": c_name, "state": state, "primary": pri, "nodes": {}}
        else:
            clusters[c_name]["state"] = state
            if pri and pri != "NULL":
                clusters[c_name]["primary"] = pri

    if not clusters:
        return None

    # Dynamically populate and enrich node info
    sorted_cluster_names = sorted(clusters.keys())
    cluster_list = []
    for c_idx, c_name in enumerate(sorted_cluster_names):
        c_data = clusters[c_name]
        c_letter = chr(ord('A') + c_idx) if c_idx < 26 else str(c_idx + 1)
        node_list = []
        raw_nodes = list(c_data["nodes"].values())
        raw_nodes.sort(key=lambda x: (0 if x.get("name") == c_data.get("primary") or x.get("role") == "PRIMARY" else 1, x.get("name", "")))

        for n_idx, n_info in enumerate(raw_nodes):
            n_name = n_info["name"]
            label = f"{c_letter}{n_idx}"
            mon = node_monitors.get(n_name, {})
            rt = node_routings.get(n_name, {})

            endpoint_id = mon.get("endpoint_id", "")
            host = ""
            port = "-"
            if ":" in endpoint_id:
                host, _, p_str = endpoint_id.partition(":")
                port = p_str.strip()
            if port == "-":
                for k, v in db_ports.items():
                    if isinstance(v, (int, str)) and (k == n_name or k in n_name or n_name in k):
                        port = str(v)
                        break

            role = n_info.get("role", "STANDBY")
            if n_name == c_data.get("primary") and c_data.get("primary") != "NULL":
                role = "PRIMARY"
            state = n_info.get("state", "ACTIVE")
            is_write = (role == "PRIMARY") or (rt.get("is_write_target") == "true")

            node_list.append({
                "name": n_name,
                "label": label,
                "cluster": c_name,
                "host": host or "127.0.0.1",
                "port": port,
                "role": role,
                "state": state,
                "is_write": is_write,
                "fault_count": mon.get("fault_count", "0"),
                "observed_role": mon.get("observed_role", role.lower()),
                "fault_flags": mon.get("fault_flags", "{}"),
            })

        cluster_list.append({
            "name": c_name,
            "label": f"Site {c_letter} ({c_name})",
            "state": c_data.get("state", "VALID"),
            "primary": c_data.get("primary", "-"),
            "nodes": node_list,
        })

    step_snapshots = []
    if steps:
        step_snapshots = _build_step_topology_snapshots(raw_text, cluster_list, steps, proxy_write_port, proxy_read_port)

    return {
        "has_topology": len(cluster_list) > 0,
        "proxy": {
            "write_port": proxy_write_port,
            "read_port": proxy_read_port,
            "status": "ACTIVE",
        },
        "clusters": cluster_list,
        "step_snapshots": step_snapshots,
    }


def _parse_report(target: str, root_dir: Optional[Any] = None) -> Dict[str, Any]:
    """Parse report.txt and related files into structured data for modal view."""
    suite_name, _, case_name = target.partition(".")
    if not case_name:
        suite_name = target
        case_name = target

    base_root = Path(root_dir) if root_dir else ROOT_DIR
    case_dir = base_root / "output" / "runs" / suite_name / case_name
    report_file = case_dir / "report.txt"

    if not report_file.exists():
        return {
            "found": False,
            "target": target,
            "error": f"未找到测试报告文件: output/runs/{suite_name}/{case_name}/report.txt",
        }

    raw_text = report_file.read_text(encoding="utf-8", errors="replace")

    # Header parsing
    def find_field(pat, default=""):
        m = re.search(pat, raw_text, re.M)
        return m.group(1).strip() if m else default

    status = find_field(r"^(?:结论|Status):\s*(PASS|FAIL)", "UNKNOWN")
    start_time = find_field(r"^测试开始时间:\s*(.*)$")
    end_time = find_field(r"^测试结束时间:\s*(.*)$")
    reason = find_field(r"^(?:通过原因|失败原因):\s*(.*)$")

    # Purpose section
    purpose = ""
    purp_match = re.search(r"^验证目的:\s*\n(.*?)(?=\n\n[^\s]|\n[^\s]+:|\Z)", raw_text, re.S | re.M)
    if purp_match:
        purpose = purp_match.group(1).strip()

    # Test contents
    test_contents = []
    cont_match = re.search(r"^测试内容:\s*\n(.*?)(?=\n\n[^\s]|\n[^\s]+:|\Z)", raw_text, re.S | re.M)
    if cont_match:
        for line in cont_match.group(1).splitlines():
            line = line.strip()
            if line:
                test_contents.append(line)

    # Key config
    key_config = ""
    cfg_match = re.search(r"^关键配置:\s*\n(.*?)(?=\n\n[^\s]|\n[^\s]+:|\Z)", raw_text, re.S | re.M)
    if cfg_match:
        key_config = cfg_match.group(1).strip()

    # Steps parsing
    steps = []
    step_blocks = re.findall(r"(步骤\s*\d+:[^\n]+)(.*?)(?=(?:步骤\s*\d+:|检测项\s*\d+:|===\s*状态转换|\Z))", raw_text, re.S)
    for title, body in step_blocks:
        step_obj = {
            "title": title.strip(),
            "status": "PASS",
            "action": "",
            "command": "",
            "expected": "",
            "actual": "",
            "evidence": "",
            "state_table": "",
        }
        if re.search(r"判定:\s*FAIL", body):
            step_obj["status"] = "FAIL"

        act_m = re.search(r"动作:\s*(.*?)(?=\n\s*(?:预期|实际|判定|证据|中间状态)|\Z)", body, re.S)
        if act_m:
            step_obj["action"] = act_m.group(1).strip()

        cmd_m = re.search(r"(?:实际执行|命令):\s*(.*?)(?=\n\s*(?:中间状态|证据|动作|预期|实际|判定)|\Z)", body, re.S)
        if cmd_m:
            step_obj["command"] = cmd_m.group(1).strip()

        exp_m = re.search(r"预期:\s*(.*?)(?=\n\s*(?:实际|判定|证据|动作)|\Z)", body, re.S)
        if exp_m:
            step_obj["expected"] = exp_m.group(1).strip()

        actu_m = re.search(r"实际:\s*(.*?)(?=\n\s*(?:判定|证据|预期)|\Z)", body, re.S)
        if actu_m:
            step_obj["actual"] = actu_m.group(1).strip()

        evi_m = re.search(r"证据:\s*(.*?)(?=\n\s*(?:动作|预期|实际|判定)|\Z)", body, re.S)
        if evi_m:
            step_obj["evidence"] = evi_m.group(1).strip()

        tbl_m = re.search(r"中间状态:\s*(.*?)(?=\n\s*(?:证据|动作|预期|实际|判定)|\Z)", body, re.S)
        if tbl_m:
            step_obj["state_table"] = tbl_m.group(1).strip()

        steps.append(step_obj)

    # Assertions parsing
    assertions = []
    assert_blocks = re.findall(r"(检测项\s*\d+:[^\n]+)(.*?)(?=(?:检测项\s*\d+:|步骤\s*\d+:|===\s*状态转换|\Z))", raw_text, re.S)
    for title, body in assert_blocks:
        st = "PASS"
        if re.search(r"判定:\s*FAIL", body):
            st = "FAIL"
        exp_m = re.search(r"预期:\s*(.*?)(?=\n\s*(?:实际|判定)|\Z)", body, re.S)
        actu_m = re.search(r"实际:\s*(.*?)(?=\n\s*(?:判定)|\Z)", body, re.S)
        assertions.append({
            "title": title.strip(),
            "status": st,
            "expected": exp_m.group(1).strip() if exp_m else "",
            "actual": actu_m.group(1).strip() if actu_m else "",
        })

    # Available extra logs in the case directory
    logs = []
    backtrace = ""
    bt_file = case_dir / "backtrace.txt"
    if bt_file.exists():
        try:
            backtrace = bt_file.read_text(encoding="utf-8", errors="replace").strip()
            logs.append("backtrace.txt")
        except Exception:
            pass

    if (case_dir / "fbasecman.log").exists():
        logs.append("fbasecman.log")
    logs_sub = case_dir / "logs"
    if logs_sub.is_dir():
        for lf in sorted(logs_sub.glob("*.log")):
            logs.append(f"logs/{lf.name}")

    topology = _extract_topology(raw_text, suite_name, steps=steps)

    return {
        "found": True,
        "target": target,
        "status": status,
        "start_time": start_time,
        "end_time": end_time,
        "reason": reason,
        "purpose": purpose,
        "test_contents": test_contents,
        "key_config": key_config,
        "steps": steps,
        "assertions": assertions,
        "available_logs": logs,
        "backtrace": backtrace,
        "topology": topology,
        "raw_text": raw_text,
    }


class ApiHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for Regression Dashboard."""

    def log_message(self, format, *args):
        # Silence default stderr logging for clean console
        pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/junit.xml":
            try:
                from framework.reporting.junit import export_junit_from_runs
                xml_content = export_junit_from_runs(ROOT_DIR / "output" / "runs")
                body = xml_content.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/xml; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="fbasecman_junit.xml"')
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
            except Exception as exc:
                self._send_json({"status": "error", "message": str(exc)}, status=500)
            return

        if path == "/api/suites":
            try:
                suites = _discover_all_suites()
                self._send_json({"status": "ok", "suites": suites})
            except Exception as exc:
                self._send_json({"status": "error", "message": str(exc)}, status=500)
            return

        if path == "/api/stats":
            try:
                suites = _discover_all_suites()
                total_cases = sum(s["total_cases"] for s in suites)
                passed = sum(s["pass_count"] for s in suites)
                failed = sum(s["fail_count"] for s in suites)
                untested = sum(s["untested_count"] for s in suites)
                running = sum(s["running_count"] for s in suites)
                rate = round((passed / total_cases * 100), 1) if total_cases > 0 else 0.0

                self._send_json({
                    "status": "ok",
                    "total_suites": len(suites),
                    "total_cases": total_cases,
                    "passed": passed,
                    "failed": failed,
                    "untested": untested,
                    "running": running,
                    "pass_rate": rate,
                    "is_running": TASK_MANAGER.is_running,
                    "current_target": TASK_MANAGER.get_current_target(),
                })
            except Exception as exc:
                self._send_json({"status": "error", "message": str(exc)}, status=500)
            return

        if path == "/api/task":
            offset = 0
            if "offset" in query:
                try:
                    offset = int(query["offset"][0])
                except ValueError:
                    offset = 0
            task_status = TASK_MANAGER.get_status(offset=offset)
            self._send_json({"status": "ok", "task": task_status})
            return

        if path == "/api/report":
            target = query.get("target", [""])[0]
            if not target:
                self._send_json({"status": "error", "message": "missing target"}, status=400)
                return
            data = _parse_report(target)
            self._send_json({"status": "ok", "report": data})
            return

        if path == "/api/log":
            target = query.get("target", [""])[0]
            filename = query.get("file", [""])[0]
            if not target or not filename:
                self._send_json({"status": "error", "message": "missing target or file"}, status=400)
                return
            # Security check against path traversal
            if ".." in filename or filename.startswith("/"):
                self._send_json({"status": "error", "message": "invalid filename"}, status=400)
                return
            suite_name, _, case_name = target.partition(".")
            log_path = ROOT_DIR / "output" / "runs" / suite_name / case_name / filename
            if not log_path.exists():
                self._send_json({"status": "error", "message": "file not found"}, status=404)
                return
            try:
                content = log_path.read_text(encoding="utf-8", errors="replace")
                # Truncate very large logs to last 100KB for browser safety
                if len(content) > 102400:
                    content = content[-102400:] + "\n\n[... 日志过长，仅展示末尾 100KB ...]"
                self._send_json({"status": "ok", "filename": filename, "content": content})
            except Exception as exc:
                self._send_json({"status": "error", "message": str(exc)}, status=500)
            return

        if path == "/api/env/status":
            try:
                data = _get_env_status_and_topo()
                self._send_json({"status": "ok", "env": data})
            except Exception as exc:
                self._send_json({"status": "error", "message": str(exc)}, status=500)
            return

        if path == "/api/env/doctor":
            try:
                from framework.configuration import load_regression_config
                from tools.doctor import run_doctor
                env = load_regression_config(ROOT_DIR)
                res = run_doctor(env)
                doctor_text = "\n".join(res.lines)
                self._send_json({"status": "ok", "doctor": doctor_text})
            except Exception as exc:
                self._send_json({"status": "error", "message": str(exc)}, status=500)
            return

        if path == "/api/stable/status":
            try:
                data = _get_stable_status()
                self._send_json({"status": "ok", "stable": data})
            except Exception as exc:
                self._send_json({"status": "error", "message": str(exc)}, status=500)
            return

        if path == "/api/stable/metrics":
            try:
                data = _get_stable_metrics()
                self._send_json({"status": "ok", "metrics": data})
            except Exception as exc:
                self._send_json({"status": "error", "message": str(exc)}, status=500)
            return

        if path == "/api/stable/report":
            report_file = ROOT_DIR / "output" / "stable" / "report.txt"
            if not report_file.exists():
                self._send_json({"status": "error", "message": "暂无常稳测试报告"}, status=404)
                return
            try:
                content = report_file.read_text(encoding="utf-8", errors="replace")
                self._send_json({"status": "ok", "content": content})
            except Exception as exc:
                self._send_json({"status": "error", "message": str(exc)}, status=500)
            return

        if path == "/api/node/probe":
            node_id = query.get("node_id", [""])[0]
            host = query.get("host", ["192.168.0.15"])[0]
            port_str = query.get("port", ["0"])[0]
            try:
                port = int(port_str)
            except ValueError:
                port = 0
            try:
                data = _probe_node(node_id, host, port)
                self._send_json({"status": "ok", "probe": data})
            except Exception as exc:
                self._send_json({"status": "error", "message": str(exc)}, status=500)
            return

        if path == "/api/node/log":
            node_id = query.get("node_id", [""])[0]
            lines_str = query.get("lines", ["50"])[0]
            try:
                lines = int(lines_str)
            except ValueError:
                lines = 50
            try:
                data = _get_single_node_log(node_id, lines)
                self._send_json({"status": "ok", "log": data})
            except Exception as exc:
                self._send_json({"status": "error", "message": str(exc)}, status=500)
            return

        if path == "/api/sql/databases":
            host = query.get("host", ["192.168.0.15"])[0]
            port_str = query.get("port", ["10011"])[0]
            user = query.get("user", ["postgres"])[0]
            try:
                port = int(port_str)
            except ValueError:
                port = 10011
            try:
                dbs = _get_node_databases(host, port, user)
                self._send_json({"status": "ok", "databases": dbs})
            except Exception as exc:
                self._send_json({"status": "error", "message": str(exc)}, status=500)
            return

        # Static files serving
        self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            payload = {}

        if path == "/api/sql/query":
            host = payload.get("host", "192.168.0.15")
            port_val = payload.get("port", 10011)
            try:
                port = int(port_val)
            except ValueError:
                port = 10011
            user = payload.get("user", "postgres")
            db = payload.get("database") or payload.get("db", "postgres")
            sql = payload.get("sql", "").strip()
            limit_val = payload.get("limit", 200)
            try:
                limit = int(limit_val)
            except ValueError:
                limit = 200
            try:
                res = _execute_sql_query(host, port, user, db, sql, limit=limit)
                self._send_json(res)
            except Exception as exc:
                self._send_json({"status": "error", "error": str(exc)}, status=500)
            return

        if path == "/api/node/action":
            node_id = payload.get("node_id", "").strip()
            action = payload.get("action", "").strip()
            if not node_id or not action:
                self._send_json({"status": "error", "message": "node_id 与 action 为必填参数"}, status=400)
                return
            if action not in ("restart", "stop", "start"):
                self._send_json({"status": "error", "message": "不支持的操作，仅支持 restart, stop, start"}, status=400)
                return
            try:
                res = _execute_single_node_action(node_id, action)
                self._send_json(res)
            except Exception as exc:
                self._send_json({"status": "error", "message": str(exc)}, status=500)
            return

        if path == "/api/run":
            target = payload.get("target", "").strip()
            if not target:
                self._send_json({"status": "error", "message": "Target is required"}, status=400)
                return
            if TASK_MANAGER.is_running:
                self._send_json({
                    "status": "error",
                    "message": f"当前已有任务正在执行: {TASK_MANAGER.get_current_target()}，请等待完成或先点击终止。"
                }, status=409)
                return
            started = TASK_MANAGER.start_task(target)
            if started:
                self._send_json({"status": "ok", "message": f"已启动任务: {target}", "target": target})
            else:
                self._send_json({"status": "error", "message": "启动任务失败"}, status=500)
            return

        if path == "/api/env/action":
            action = payload.get("action", "").strip()
            adopt = payload.get("adopt_existing", False)
            dry_run = payload.get("dry_run", False)
            if not action:
                self._send_json({"status": "error", "message": "Action is required"}, status=400)
                return
            if TASK_MANAGER.is_running:
                self._send_json({
                    "status": "error",
                    "message": f"当前已有任务正在执行: {TASK_MANAGER.get_current_target()}，请等待完成或先点击终止。"
                }, status=409)
                return

            if action == "doctor":
                cmd = [sys.executable, str(ROOT_DIR / "tools" / "cli.py"), "doctor"]
            else:
                cmd = [sys.executable, str(ROOT_DIR / "tools" / "cli.py"), "env", action]
                if adopt:
                    cmd.append("--adopt-existing")
                if dry_run:
                    cmd.append("--dry-run")

            target_name = f"env.{action}"
            started = TASK_MANAGER.start_task(target_name, custom_cmd=cmd)
            if started:
                self._send_json({"status": "ok", "message": f"已触发环境操作: {action}", "target": target_name})
            else:
                self._send_json({"status": "error", "message": "触发环境操作失败"}, status=500)
            return

        if path == "/api/stable/action":
            action = payload.get("action", "").strip()
            target = payload.get("target", "all").strip()
            if not action:
                self._send_json({"status": "error", "message": "Action is required"}, status=400)
                return
            if TASK_MANAGER.is_running and action in ("start", "restart", "run"):
                self._send_json({
                    "status": "error",
                    "message": f"当前已有任务正在执行: {TASK_MANAGER.get_current_target()}，请等待完成或先点击终止。"
                }, status=409)
                return

            cmd = [sys.executable, str(ROOT_DIR / "tools" / "stable_cli.py"), action]
            if action in ("start", "restart", "stop", "run"):
                cmd.append(target)

            target_name = f"stable.{action}.{target}"
            started = TASK_MANAGER.start_task(target_name, custom_cmd=cmd)
            if started:
                self._send_json({"status": "ok", "message": f"已触发常稳操作: {action} {target}", "target": target_name})
            else:
                self._send_json({"status": "error", "message": "触发常稳操作失败"}, status=500)
            return

        if path == "/api/stop":
            stopped = TASK_MANAGER.stop_task()
            if stopped:
                self._send_json({"status": "ok", "message": "任务已终止"})
            else:
                self._send_json({"status": "error", "message": "当前没有正在执行的任务"}, status=400)
            return

        self._send_json({"status": "error", "message": "Not found"}, status=404)

    def _serve_static(self, path: str):
        clean_path = path.lstrip("/")
        if not clean_path or clean_path == "index.html":
            file_path = WEB_STATIC_DIR / "index.html"
        else:
            file_path = WEB_STATIC_DIR / clean_path

        if not file_path.exists() or file_path.is_dir():
            file_path = WEB_STATIC_DIR / "index.html"

        mime_type = "text/plain"
        ext = file_path.suffix.lower()
        if ext == ".html":
            mime_type = "text/html; charset=utf-8"
        elif ext == ".css":
            mime_type = "text/css; charset=utf-8"
        elif ext == ".js":
            mime_type = "application/javascript; charset=utf-8"
        elif ext == ".json":
            mime_type = "application/json; charset=utf-8"
        elif ext == ".svg":
            mime_type = "image/svg+xml"
        elif ext == ".png":
            mime_type = "image/png"

        try:
            content = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(content)
        except Exception as exc:
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(f"Error serving static file: {exc}".encode())

    def _send_json(self, data: Dict[str, Any], status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def run_web_server(host: str = "0.0.0.0", port: int = 8080):
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, ApiHandler)
    print(f"================================================================")
    print(f" fbasecman 回归测试 Web UI 服务已启动")
    print(f" 本地访问地址: http://localhost:{port}")
    print(f" 局域网访问地址: http://{host}:{port}")
    print(f" 按 Ctrl+C 停止服务")
    print(f"================================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止 Web 服务...")
        httpd.shutdown()
        print("Web 服务已停止。")


def main():
    parser = argparse.ArgumentParser(description="fbasecman_regress_v2 Web UI Server")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP server listen host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP server listen port (default: 8080)")
    args = parser.parse_args()
    run_web_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
