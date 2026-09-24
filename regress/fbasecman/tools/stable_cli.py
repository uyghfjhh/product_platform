#!/usr/bin/env python3
"""Independent command line for the stable suite."""

import argparse
import json
import shutil
import shlex
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from suites.stable.config import StableConfig, render_fbasecman_config
from suites.stable.manifest import WORKLOADS, enabled_workloads, find_workload
from suites.stable.runtime import (
    StableFailure, StableRuntime, archive_run, choose_ports, core_files, displayed_pid,
    managed_pid, monitor_loop, run_progress,
    workload_progress, workload_status_text,
    refresh_status, runtime_for_state, scan_logs, stop_runtime, stop_target, write_report_from_state,
)
from suites.stable.state import StateStore
from suites.stable.supervisor import launch_supervisor
from tools.stable_top import run_top, run_tui
from products.fbasecman.environment.provider import FbasecmanEnvironmentProvider
from tools.doctor import run_doctor


TARGETS = ("all", "fbasecman", "monitor", "pgbench", "jdbc", "jdbc:prepared_leak")


def parser():
    result = argparse.ArgumentParser(prog="./stable.sh", description="fbasecman 常稳测试")
    result.add_argument("--config", action="append", default=[], help="追加 YAML 配置")
    commands = result.add_subparsers(dest="command")
    env = commands.add_parser("env", help="管理独立 stable PostgreSQL 环境")
    env_sub = env.add_subparsers(dest="env_command")
    setup = env_sub.add_parser("setup")
    setup.add_argument("--adopt-existing", action="store_true", help="认领已检查的旧 PGDATA 后重建")
    clean = env_sub.add_parser("clean")
    clean.add_argument("--dry-run", action="store_true", help="只显示清理计划")
    clean.add_argument(
        "--adopt-existing", action="store_true",
        help="认领已检查的残留 PGDATA 后清理",
    )
    for name in ("status", "start", "restart", "stop"):
        env_sub.add_parser(name)
    commands.add_parser("show", help="展示正式 workload")
    commands.add_parser("doctor", help="检查依赖、配置和 stable/regression 隔离")
    run = commands.add_parser("run", help="前台运行完整常稳或单个 workload")
    run.add_argument("workload", nargs="?")
    commands.add_parser("render-conf", help="渲染新配置模型的 fbasecman 配置")
    commands.add_parser("reload", help="重渲染当前配置并通过 console 重载 fbasecman")
    for name in ("start", "restart", "stop"):
        command = commands.add_parser(name, help="%s 后台常稳" % name)
        command.add_argument("target", nargs="?", choices=TARGETS, default="all")
    for name in ("status", "inspect", "recover", "diagnose", "archive", "pg-log-check", "report", "clean"):
        commands.add_parser(name)
    top = commands.add_parser("top", help="实时查看 stable 运行状态和资源趋势")
    top.add_argument("--refresh", type=int, default=2, help="刷新间隔秒数，默认 2")
    top.add_argument("--once", action="store_true", help="输出一次状态快照后退出")
    top.add_argument("--files", action="store_true", help="显示 fbasecman 打开的常规文件")
    top.add_argument("--text", action="store_true", help="文本模式兼容别名；top 默认就是文本模式")
    tui = commands.add_parser("tui", help="启动 stable Textual 全屏仪表盘")
    tui.add_argument("--refresh", type=int, default=2, help="刷新间隔秒数，默认 2")
    memory = commands.add_parser("memory-plot")
    memory.add_argument("--x-interval", default="1m")
    internal = commands.add_parser("internal-monitor")
    internal.add_argument("--state-file", required=True)
    return result


def config(args):
    return StableConfig(ROOT, [Path(value).resolve() for value in args.config])


def do_env(args):
    cfg = config(args)
    provider = FbasecmanEnvironmentProvider(cfg.runtime_config, verbose=args.env_command != "status")
    if args.env_command == "setup":
        provider.setup(adopt_existing=args.adopt_existing); print("stable env setup complete"); return 0
    if args.env_command == "clean":
        plan = provider.clean(
            dry_run=args.dry_run, adopt_existing=args.adopt_existing,
        )
        if args.dry_run: print(plan.render())
        else: print("stable env clean complete")
        return 0
    if args.env_command == "status":
        print(provider.status_text()); return 0
    if args.env_command == "start":
        provider.start(); print("stable env start complete"); return 0
    if args.env_command == "restart":
        provider.restart(); print("stable env restart complete"); return 0
    if args.env_command == "stop":
        provider.stop(); print("stable env stop complete"); return 0
    raise StableFailure("stable env command is required")


def selected(target, cfg):
    workloads = enabled_workloads(cfg)
    if not target or target == "all":
        return list(workloads)
    if target == "pgbench":
        return [item for item in workloads if item.kind == "pgbench"]
    if target in ("jdbc", "jdbc:prepared_leak"):
        return [item for item in workloads if item.kind == "jdbc"]
    if target in ("fbasecman", "monitor"):
        return []
    workload = find_workload(target)
    if workload not in workloads:
        raise StableFailure("workload is disabled by stable.yaml: %s" % target)
    return [workload]


def format_state(state, cfg=None):
    active_runtime = state.get("status") in ("running", "degraded", "finalizing", "stopping")
    failed = [name for name, item in state.get("workloads", {}).items() if item.get("status") == "failed"]
    lines = ["Stable runtime:", "  run_id: %s" % (state.get("run_id") or "<none>"),
             "  status: %s" % state.get("status"), "  run_dir: %s" % (state.get("run_dir") or "<none>"),
             "  fbasecman_log: %s" % (state.get("product_log") or "<none>"),
             "  fbasecman_pid: %s" % (state.get("fbasecman_pid") if active_runtime else "-"),
             "  monitor_pid: %s" % (state.get("monitor_pid") if active_runtime else "-"),
             "  supervisor_pid: %s" % (state.get("supervisor_pid") if active_runtime else "-")]
    if active_runtime:
        supervisor_alive = managed_pid(state.get("supervisor_pid"), str(cfg.state_file)) if cfg else False
        lines.append("  supervisor_alive: %s" % ("yes" if supervisor_alive else "no"))
        if cfg:
            main_port = state.get("ports", {}).get("main")
            postgres = cfg.runtime_config.config["local"]["postgres_dir"]
            if main_port:
                console = "%s -h 127.0.0.1 -p %s -U admin -d console" % (
                    shlex.quote(str(Path(postgres) / "bin" / "psql")), main_port)
                lines.append("  console_connect: %s" % console)
    if failed:
        lines.append("  failed_workloads: %s" % ", ".join(failed))
    if cfg and state.get("started_at"):
        progress = run_progress(cfg, state)
        lines.append("  run: elapsed=%s remaining=%s planned_end=%s" % (
            progress["elapsed_text"], progress["remaining_text"], progress["ends_at_text"]))
    for name, value in sorted(state.get("workloads", {}).items()):
        line = "  %-32s %-30s pid=%s" % (name, workload_status_text(value), displayed_pid(state, value))
        if cfg:
            progress = workload_progress(cfg, state, name, value)
            line += " duration=%s elapsed=%s remaining=%s planned_end=%s" % (
                progress["duration_text"], progress["elapsed_text"], progress["remaining_text"], progress["ends_at_text"])
        lines.append(line)
    return "\n".join(lines)


def show(cfg):
    print("stable - fbasecman 正式常稳 workload")
    for item in enabled_workloads(cfg):
        duration = "40m" if item.kind == "pgbench" else "20m"
        print("  - %-38s type=%-7s duration=%-4s %s" % (item.target, item.kind, duration, item.summary))


def do_run(args):
    cfg = config(args)
    items = selected(args.workload or "all", cfg)
    runtime = StableRuntime(ROOT, [Path(value).resolve() for value in args.config])
    ok = runtime.foreground(items)
    print("Stable report: %s" % (runtime.run_dir / "report.txt"))
    return 0 if ok else 1


def do_start(args):
    cfg = config(args)
    if args.command == "restart":
        stop_target(cfg, args.target)
    current = refresh_status(cfg)
    if args.target in ("monitor", "pgbench", "jdbc", "jdbc:prepared_leak") and current.get("status") in ("running", "degraded"):
        if not managed_pid(current.get("supervisor_pid"), str(cfg.state_file)):
            raise StableFailure("stable supervisor is not running; use './stable.sh recover' first")
        runtime = runtime_for_state(cfg, current)
        if args.target == "monitor":
            pid, _ = runtime.start_monitor()
            def record_monitor(state):
                state["monitor_pid"] = pid
                state.setdefault("commands", {})[str(pid)] = "internal-monitor"
            current = runtime.store.update(record_monitor)
        else:
            launched = {}
            for item in selected(args.target, cfg):
                process, log, command = runtime.launch_workload(item); process.close_output()
                launched[item.name] = {"pid": process.pid, "status": "running", "returncode": None,
                                       "log": str(log), "command": command,
                                       "started_at": int(time.time()),
                                       "duration_seconds": cfg.duration(item.kind),
                                       "fingerprint": runtime.workload_fingerprint(item)}
            def record_workloads(state):
                for name, item_state in launched.items():
                    state.setdefault("workloads", {})[name] = item_state
                    state.setdefault("commands", {})[str(item_state["pid"])] = str(runtime.run_dir)
            current = runtime.store.update(record_workloads)
        print(format_state(current, cfg)); return 0
    runtime = StableRuntime(ROOT, [Path(value).resolve() for value in args.config])
    state = runtime.background(selected(args.target, cfg))
    print(format_state(state, cfg)); return 0


def latest_run(cfg):
    state = refresh_status(cfg)
    path = Path(state.get("run_dir", ""))
    if not state.get("run_dir") or not path.exists():
        raise StableFailure("no stable run is recorded")
    return state, path


def do_recover(cfg):
    store = StateStore(cfg.state_file)
    state = store.load()
    if state.get("status") not in ("running", "degraded"):
        raise StableFailure("only a running or degraded run can be recovered")
    if managed_pid(state.get("supervisor_pid"), str(cfg.state_file)):
        print("stable supervisor is already running")
        return 0
    run_dir = Path(state["run_dir"])
    pid, command = launch_supervisor(
        ROOT, cfg.state_file, state.get("config_files", []),
        output_path=run_dir / "logs" / "supervisor.log",
    )
    def record(current):
        current["supervisor_pid"] = pid
        current.setdefault("commands", {})[str(pid)] = " ".join(command)
    state = store.update(record)
    print(format_state(state, cfg))
    return 0


def do_diagnose(cfg):
    state, path = latest_run(cfg)
    findings = scan_logs(path)
    pg_findings = state.get("pg_log_findings", [])
    cores = core_files(ROOT, state.get("started_at", 0))
    text = "Stable diagnosis\nstatus: %s\nlog_findings: %d\npg_log_findings: %d\ncore_files: %d\n%s\n%s\n%s\n" % (
        state.get("status"), len(findings), len(pg_findings), len(cores),
        "\n".join(findings[:100]), "\n".join(pg_findings[:100]),
        "\n".join(str(item) for item in cores))
    target = path / "diagnostics" / "diagnose.txt"; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(text, encoding="utf-8")
    print(text.rstrip()); return 1 if findings or pg_findings or cores else 0


def do_pg_log_check(cfg):
    state, path = latest_run(cfg)
    runtime = runtime_for_state(cfg, state)
    captured = runtime.capture_pg_log_windows(state.get("started_at", 0))
    archived = runtime.compress_pg_log_windows(state.get("started_at", 0))
    def record(current):
        current["pg_log_directory"] = captured["directory"]
        current["pg_log_findings"] = captured["findings"]
        current["pg_log_archive_directory"] = archived["directory"]
        current["pg_log_archives"] = archived["archives"]
    runtime.store.update(record)
    print("PG log window: %s" % captured["directory"])
    print("PG business findings: %d" % len(captured["findings"]))
    print("PG closed-log archives: %d" % len(archived["archives"]))
    for item in captured["findings"][:100]:
        print(item)
    return 1 if captured["findings"] else 0


def do_reload(cfg):
    state, _ = latest_run(cfg)
    runtime = runtime_for_state(cfg, state)
    existing_count = len(state.get("reloads", []))
    entry = runtime.reload_product(state)
    def record(current):
        reloads = current.setdefault("reloads", [])
        if len(reloads) == existing_count:
            reloads.append(entry)
    runtime.store.update(record)
    print("RELOAD")
    print(entry["output"])
    return 0


def do_memory_plot(cfg, interval):
    _, path = latest_run(cfg); source = path / "monitor" / "resources.csv"
    if not source.exists(): raise StableFailure("monitor data missing: %s" % source)
    import csv
    rows = list(csv.DictReader(source.open(encoding="utf-8")))
    values = [int(float(row["rss_kb"])) for row in rows]
    if not values:
        raise StableFailure("monitor data is empty: %s" % source)
    output = path / "monitor" / "fbasecman_memory.svg"
    width, height, margin = 960, 420, 55
    low, high = min(values), max(values)
    span = max(1, high - low)
    points = []
    for index, value in enumerate(values):
        x = margin + (width - margin * 2) * index / max(1, len(values) - 1)
        y = height - margin - (height - margin * 2) * (value - low) / span
        points.append("%.1f,%.1f" % (x, y))
    svg = """<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{w}\" height=\"{h}\" viewBox=\"0 0 {w} {h}\">
<rect width=\"100%\" height=\"100%\" fill=\"white\"/>
<text x=\"{m}\" y=\"28\" font-family=\"sans-serif\" font-size=\"18\">fbasecman RSS</text>
<text x=\"{m}\" y=\"48\" font-family=\"sans-serif\" font-size=\"12\">samples={count}, interval={interval}, min={low} KB, max={high} KB</text>
<line x1=\"{m}\" y1=\"{base}\" x2=\"{right}\" y2=\"{base}\" stroke=\"#444\"/>
<line x1=\"{m}\" y1=\"{m}\" x2=\"{m}\" y2=\"{base}\" stroke=\"#444\"/>
<polyline points=\"{points}\" fill=\"none\" stroke=\"#157f3d\" stroke-width=\"2\"/>
<text x=\"8\" y=\"{m}\" font-family=\"sans-serif\" font-size=\"12\">{high}</text>
<text x=\"8\" y=\"{base}\" font-family=\"sans-serif\" font-size=\"12\">{low}</text>
</svg>
""".format(w=width, h=height, m=margin, base=height-margin, right=width-margin,
             count=len(values), interval=interval, low=low, high=high, points=" ".join(points))
    output.write_text(svg, encoding="utf-8")
    print(output); return 0


def main():
    args = parser().parse_args()
    if not args.command: parser().print_help(); return 2
    try:
        if args.command == "env": return do_env(args)
        if args.command == "doctor":
            result = run_doctor(config(args).runtime_config)
            print("\n".join(result.lines)); return result.exit_code()
        if args.command == "show": show(config(args)); return 0
        if args.command == "run": return do_run(args)
        if args.command in ("start", "restart"): return do_start(args)
        cfg = config(args)
        if args.command == "render-conf":
            rendered_dir = cfg.output_dir / "rendered"
            rendered_dir.mkdir(parents=True, exist_ok=True)
            fbasecman = cfg.runtime_config.config["fbasecman"]
            main_port, write_port, _ = choose_ports(
                fbasecman.get("read_port"), fbasecman.get("write_port")
            )
            target = rendered_dir / "fbasecman.conf"
            target.write_text(render_fbasecman_config(cfg, rendered_dir, main_port, write_port), encoding="utf-8")
            print(target); return 0
        if args.command == "reload": return do_reload(cfg)
        if args.command == "stop": stop_target(cfg, args.target); print("stable %s stopped" % args.target); return 0
        if args.command in ("status", "inspect"):
            state = refresh_status(cfg); print(format_state(state, cfg));
            if args.command == "inspect" and state.get("run_dir"): print("\n" + json.dumps(state, indent=2, sort_keys=True))
            return 0
        if args.command == "recover": return do_recover(cfg)
        if args.command == "diagnose": return do_diagnose(cfg)
        if args.command == "pg-log-check": return do_pg_log_check(cfg)
        if args.command == "archive": _, path = latest_run(cfg); print(archive_run(path)); return 0
        if args.command == "report": print(write_report_from_state(cfg)); return 0
        if args.command == "memory-plot": return do_memory_plot(cfg, args.x_interval)
        if args.command == "top": return run_top(cfg, args.refresh, args.once, args.files)
        if args.command == "tui": return run_tui(cfg, args.refresh)
        if args.command == "clean":
            stop_runtime(cfg)
            if cfg.output_dir.exists(): shutil.rmtree(str(cfg.output_dir))
            print("stable output cleaned"); return 0
        if args.command == "internal-monitor": monitor_loop(Path(args.state_file), int(cfg.values["interval_seconds"])); return 0
    except (StableFailure, KeyError, ValueError, RuntimeError) as exc:
        print("stable error: %s" % exc, file=sys.stderr); return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
