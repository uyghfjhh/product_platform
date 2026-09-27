"""Dependency-free terminal dashboard for an active stable run."""

import csv
import os
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

from suites.stable.state import StateStore
from suites.stable.runtime import displayed_pid, run_progress, workload_progress, workload_status_text


def _is_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _format_number(value, suffix=""):
    value = float(value or 0)
    for unit, factor in (("M", 1000000.0), ("K", 1000.0)):
        if abs(value) >= factor:
            return "%.1f%s%s" % (value / factor, unit, suffix)
    return ("%d" if value == int(value) else "%.1f") % value + suffix


def _format_bytes(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (value, unit)
        value /= 1024.0


def _resource_rows(path, limit=60):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(deque(csv.DictReader(handle), maxlen=limit))


def _numeric(rows, key):
    values = []
    for row in rows:
        try:
            values.append(float(row.get(key, 0) or 0))
        except ValueError:
            pass
    return values


def _sparkline(values, width=44):
    if not values:
        return "-"
    if len(values) > width:
        step = (len(values) - 1) / float(width - 1)
        values = [values[int(round(index * step))] for index in range(width)]
    low, high = min(values), max(values)
    symbols = ".:-=+*#%@"
    if high == low:
        return symbols[len(symbols) // 2] * len(values)
    return "".join(symbols[int((value - low) * (len(symbols) - 1) / (high - low))] for value in values)


def _regular_files(pid, limit=20):
    directory = Path("/proc") / str(pid) / "fd"
    if not pid or not directory.exists():
        return []
    files = []
    for entry in directory.iterdir():
        try:
            target = Path(os.readlink(str(entry)).replace(" (deleted)", ""))
            if target.is_file():
                files.append((entry.name, target, target.stat().st_size))
        except OSError:
            continue
    return sorted(files, key=lambda item: item[2], reverse=True)[:limit]


def _tail(path, limit=6):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8", errors="replace") as handle:
        return list(deque((line.rstrip() for line in handle), maxlen=limit))


def dashboard(cfg, show_files=False):
    """Return one complete, read-only stable status frame."""
    state = StateStore(cfg.state_file).load()
    run_dir = Path(state.get("run_dir") or cfg.output_dir)
    rows = _resource_rows(run_dir / "monitor" / "resources.csv")
    latest = rows[-1] if rows else {}
    pid = state.get("fbasecman_pid", 0)
    monitor_pid = state.get("monitor_pid", 0)
    active_runtime = state.get("status") in ("running", "degraded")
    rss = _numeric(rows, "rss_kb")
    cpu = _numeric(rows, "cpu_percent")
    write_bytes = _numeric(rows, "write_bytes")
    io_delta = write_bytes[-1] - write_bytes[-2] if len(write_bytes) > 1 else 0
    lines = [
        "fbasecman stable top  refresh data is read-only  Ctrl-C exits",
        "time=%s run_id=%s status=%s" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), state.get("run_id") or "<none>", state.get("status")),
        "run_dir=%s" % run_dir,
        "fbasecman pid=%s alive=%s  monitor pid=%s alive=%s" % (
            pid if active_runtime else "-", "yes" if active_runtime and _is_alive(pid) else "no",
            monitor_pid if active_runtime else "-", "yes" if active_runtime and _is_alive(monitor_pid) else "no"),
    ]
    if state.get("started_at"):
        progress = run_progress(cfg, state)
        lines.append("run elapsed=%s remaining=%s planned_end=%s" % (
            progress["elapsed_text"], progress["remaining_text"], progress["ends_at_text"]))
    if latest:
        lines.extend((
            "sample=%s rss=%s vsz=%s cpu=%s%% threads=%s fd=%s" % (
                latest.get("timestamp", "-"), _format_number(latest.get("rss_kb"), " KB"),
                _format_number(latest.get("vsz_kb"), " KB"), _format_number(latest.get("cpu_percent")),
                latest.get("threads", "-"), latest.get("fd_count", "-")),
            "io read=%s write=%s write_delta=%s" % (
                _format_bytes(latest.get("read_bytes")), _format_bytes(latest.get("write_bytes")),
                _format_bytes(io_delta)),
            "rss  %s  min=%s max=%s" % (
                _sparkline(rss), _format_number(min(rss), " KB"), _format_number(max(rss), " KB")),
            "cpu  %s  min=%s%% max=%s%%" % (
                _sparkline(cpu), _format_number(min(cpu)), _format_number(max(cpu))),
        ))
    else:
        lines.append("resources: no samples yet")
    lines.append("workloads:")
    workloads = state.get("workloads", {})
    if not workloads:
        lines.append("  <none>")
    for name, item in sorted(workloads.items()):
        workload_pid = item.get("pid", 0)
        progress = workload_progress(cfg, state, name, item)
        displayed = displayed_pid(state, item)
        lines.append("  %-32s %-30s pid=%s alive=%s duration=%s elapsed=%s remaining=%s end=%s" % (
            name, workload_status_text(item), displayed,
            "yes" if displayed != "-" and _is_alive(workload_pid) else "no",
            progress["duration_text"], progress["elapsed_text"], progress["remaining_text"], progress["ends_at_text"]))
    if show_files:
        lines.append("open regular files:")
        files = _regular_files(pid)
        if not files:
            lines.append("  <none>")
        for descriptor, path, size in files:
            lines.append("  fd=%-4s size=%-12s %s" % (descriptor, _format_bytes(size), path))
    else:
        lines.extend(("recent fbasecman log:",))
        tail = _tail(run_dir / "logs" / "fbasecman.log")
        lines.extend("  " + line for line in tail) if tail else lines.append("  <no log>")
    return "\n".join(lines)


def run_text_top(cfg, refresh_seconds=2, once=False, show_files=False, stream=None):
    """Run the dependency-free stable dashboard."""
    stream = stream or sys.stdout
    refresh_seconds = max(1, int(refresh_seconds))
    try:
        while True:
            if stream.isatty():
                stream.write("\033[2J\033[H")
            stream.write(dashboard(cfg, show_files=show_files) + "\n")
            stream.flush()
            if once:
                return 0
            time.sleep(refresh_seconds)
    except KeyboardInterrupt:
        return 0


def _tui_python():
    candidates = []
    if sys.version_info >= (3, 8):
        candidates.append(sys.executable)
    for name in ("python3.12", "python3.11", "python3.10", "python3.9", "python3.8"):
        candidate = shutil.which(name)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        check = subprocess.run(
            [candidate, "-c", "import textual, textual_plotext, plotext"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if check.returncode == 0:
            return candidate
    return None


def _tui_hint(stream):
    stream.write(
        "Textual TUI requires Python 3.8+ plus its optional dependencies.\\n"
        "Install with: PYTHON_BIN=/path/to/python3.8 ./install_stable_top_deps.sh\\n"
        "Use './stable.sh top' for the dependency-free text dashboard.\\n"
    )
    stream.flush()


def run_top(cfg, refresh_seconds=2, once=False, show_files=False, stream=None):
    """Run the stable text dashboard with workload and log details."""
    return run_text_top(cfg, refresh_seconds, once, show_files, stream)


def run_tui(cfg, refresh_seconds=2, stream=None):
    """Run the optional full-screen Textual dashboard without text fallback."""
    python = _tui_python()
    if not python:
        _tui_hint(stream or sys.stderr)
        return 1
    script = Path(__file__).with_name("stable_top_textual.py")
    return subprocess.call([
        python, str(script), "--state-file", str(cfg.state_file),
        "--refresh", str(max(1, int(refresh_seconds))),
    ])
