#!/usr/bin/env python3
"""Optional Textual dashboard for the isolated stable runtime."""

import argparse
import csv
import json
import os
import signal
import sys
import time
from collections import deque
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Grid, Vertical
from textual.widgets import Static
from textual_plotext import PlotextPlot


def load_state(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def is_alive(pid):
    try:
        os.kill(int(pid or 0), 0)
        return True
    except OSError:
        return False


def resource_rows(run_dir, limit=360):
    path = Path(run_dir) / "monitor" / "resources.csv"
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(deque(csv.DictReader(handle), maxlen=limit))
    except OSError:
        return []


def numeric_series(rows, key, delta=False):
    labels = []
    values = []
    previous = None
    for row in rows:
        try:
            value = float(row.get(key, 0) or 0)
        except ValueError:
            continue
        labels.append(str(row.get("timestamp", "-")))
        if delta:
            values.append(0 if previous is None else max(0, value - previous))
        else:
            values.append(value)
        previous = value
    return labels, values


def short_time(value):
    value = str(value).replace("T", " ")
    return value[11:19] if len(value) >= 19 else value


def format_value(value, suffix=""):
    value = float(value or 0)
    for unit, factor in (("G", 1000000000.0), ("M", 1000000.0), ("K", 1000.0)):
        if abs(value) >= factor:
            return "%.1f%s%s" % (value / factor, unit, suffix)
    return ("%d" if value == int(value) else "%.1f") % value + suffix


def format_bytes(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (value, unit)
        value /= 1024.0


def summary(values, suffix=""):
    if not values:
        return "no samples"
    delta = values[-1] - values[0]
    return "latest=%s min=%s max=%s delta=%+s" % (
        format_value(values[-1], suffix), format_value(min(values), suffix),
        format_value(max(values), suffix), format_value(delta, suffix),
    )


def open_regular_files(pid, limit=30):
    directory = Path("/proc") / str(pid or 0) / "fd"
    if not directory.exists():
        return []
    rows = []
    for entry in directory.iterdir():
        try:
            path = Path(os.readlink(str(entry)).replace(" (deleted)", ""))
            if path.is_file():
                rows.append((entry.name, path, path.stat().st_size))
        except OSError:
            continue
    return sorted(rows, key=lambda item: item[2], reverse=True)[:limit]


class ChartPanel(Vertical):
    def __init__(self, title, label, suffix, color, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title = title
        self.label = label
        self.suffix = suffix
        self.color = color
        self.header = Static(classes="chart_title")
        self.plot = PlotextPlot(classes="chart_plot")

    def compose(self):
        yield self.header
        yield self.plot

    def set_series(self, labels, values):
        self.header.update("%s | %s" % (self.title, summary(values, self.suffix)))
        plot = self.plot.plt
        plot.clear_figure()
        plot.theme("pro")
        plot.grid(False)
        plot.xlabel("time")
        plot.ylabel(self.label)
        if values:
            x_values = list(range(len(values)))
            tick_count = min(4, len(values))
            indexes = [int(round(index * (len(values) - 1) / max(1, tick_count - 1)))
                       for index in range(tick_count)]
            plot.xticks(indexes, [short_time(labels[index]) for index in indexes])
            plot.plot(x_values, values, color=self.color, marker="braille")
        self.plot.refresh()


class StableTopApp(App):
    CSS = """
    Screen { background: #101417; color: #d8dee9; }
    #status { height: 3; padding: 0 1; background: #202830; }
    #grid { layout: grid; grid-size: 2 2; grid-gutter: 1 2; padding: 1; }
    ChartPanel { height: 1fr; }
    .chart_title { height: 1; text-style: bold; }
    .chart_plot { height: 1fr; }
    #files { padding: 1 2; }
    #footer { height: 1; padding: 0 1; background: #202830; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"), ("ctrl+c", "quit", "Quit"),
        ("r", "refresh_now", "Refresh"), ("f", "show_files", "Files"),
        ("g", "show_graphs", "Graphs"),
    ]

    def __init__(self, state_file, refresh_seconds):
        super().__init__()
        self.state_file = Path(state_file)
        self.refresh_seconds = max(1, int(refresh_seconds))
        self.rss = ChartPanel("RSS", "KB", " KB", "cyan+", id="rss")
        self.vsz = ChartPanel("VSZ", "KB", " KB", "green+", id="vsz")
        self.cpu = ChartPanel("CPU", "%", "%", "yellow+", id="cpu")
        self.io = ChartPanel("Write delta", "bytes/sample", " B", "magenta+", id="io")
        self.files = Static(id="files")
        self.file_sizes = {}
        self.mode = "graphs"

    def compose(self):
        yield Static(id="status")
        with Grid(id="grid"):
            yield self.rss
            yield self.vsz
            yield self.cpu
            yield self.io
        yield self.files
        yield Static("q quit | r refresh | f files | g graphs | refresh=%ss" % self.refresh_seconds, id="footer")

    def on_mount(self):
        self.files.display = False
        self.refresh_data()
        self.set_interval(self.refresh_seconds, self.refresh_data)

    def action_quit(self):
        self.exit()

    def action_refresh_now(self):
        self.refresh_data()

    def action_show_files(self):
        self.mode = "files"
        self.query_one("#grid", Grid).display = False
        self.files.display = True
        self.refresh_data()

    def action_show_graphs(self):
        self.mode = "graphs"
        self.files.display = False
        self.query_one("#grid", Grid).display = True
        self.refresh_data()

    def render_files(self, pid):
        lines = ["Open regular files (size delta between refreshes)", "",
                 "FD      delta         size  path"]
        current = {}
        rows = []
        for descriptor, path, size in open_regular_files(pid):
            current[str(path)] = size
            delta = max(0, size - self.file_sizes.get(str(path), size))
            rows.append((delta, descriptor, path, size))
        self.file_sizes = current
        if not rows:
            lines.append("No regular files found.")
        for delta, descriptor, path, size in sorted(rows, reverse=True)[:30]:
            lines.append("%-4s %12s %12s  %s" % (
                descriptor, format_bytes(delta), format_bytes(size), path))
        return "\n".join(lines)

    def refresh_data(self):
        state = load_state(self.state_file)
        run_dir = state.get("run_dir", "")
        rows = resource_rows(run_dir)
        labels, rss = numeric_series(rows, "rss_kb")
        _, vsz = numeric_series(rows, "vsz_kb")
        _, cpu = numeric_series(rows, "cpu_percent")
        io_labels, io = numeric_series(rows, "write_bytes", delta=True)
        fbasecman_pid = state.get("fbasecman_pid", 0)
        monitor_pid = state.get("monitor_pid", 0)
        ports = state.get("ports", {})
        status = state.get("status", "stopped")
        if self.mode == "graphs":
            self.rss.set_series(labels, rss)
            self.vsz.set_series(labels, vsz)
            self.cpu.set_series(labels, cpu)
            self.io.set_series(io_labels, io)
        else:
            self.files.update(self.render_files(fbasecman_pid))
        latest = labels[-1] if labels else "-"
        latest_row = rows[-1] if rows else {}
        self.query_one("#status", Static).update(
            "stable TUI | status=%s | fbasecman=%s pid=%s | monitor=%s pid=%s | "
            "ports=%s/%s | fd=%s | sample=%s | run=%s" % (
                status, "up" if is_alive(fbasecman_pid) else "down", fbasecman_pid or "-",
                "up" if is_alive(monitor_pid) else "down", monitor_pid or "-",
                ports.get("write", "-"), ports.get("main", "-"), latest_row.get("fd_count", "-"),
                latest, run_dir or "-"))


def main():
    parser = argparse.ArgumentParser(description="Textual stable dashboard")
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--refresh", type=int, default=2)
    args = parser.parse_args()
    app = StableTopApp(args.state_file, args.refresh)
    signal.signal(signal.SIGINT, lambda _signum, _frame: app.exit())
    try:
        app.run()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
