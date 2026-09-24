#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
PLATFORM_ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT_DIR, PLATFORM_ROOT, PLATFORM_ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from framework.execution.shell import ShellCommandError
from framework.environment import create_environment_provider
from framework.configuration import load_regression_config, validate_profile_isolation
from framework.persistence import atomic_write_text
import products.fbasecman.environment  # Registers the product environment provider.
from framework.suites import get_default_registry
from tools.clean import run_clean
from tools.doctor import run_doctor


LAST_FAILED_PATH = ROOT_DIR / "output" / "last_failed.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="./run.sh")
    parser.add_argument("--config", action="append", default=[], help="extra config file")

    subparsers = parser.add_subparsers(dest="command")

    env_parser = subparsers.add_parser("env")
    env_sub = env_parser.add_subparsers(dest="env_command")
    setup = env_sub.add_parser("setup")
    setup.add_argument(
        "--adopt-existing", action="store_true",
        help="create ownership markers for inspected legacy PGDATA before rebuilding",
    )
    clean = env_sub.add_parser("clean")
    clean.add_argument("--dry-run", action="store_true", help="print the cleanup plan only")
    for name in ("status", "start", "restart", "stop", "heal"):
        env_sub.add_parser(name)

    subparsers.add_parser("doctor")
    clean_parser = subparsers.add_parser("clean")
    clean_parser.add_argument(
        "--output",
        action="store_true",
        help="also remove regression runtime artifacts (preserves output/stable/)",
    )
    clean_parser.add_argument(
        "--prune-logs",
        "--prune",
        action="store_true",
        help="清理 output/runs 中的过期日志备份并截断超大日志文件 (保留测试报告与摘要)",
    )
    clean_parser.add_argument(
        "--max-mb",
        type=float,
        default=10.0,
        help="日志截断阈值大小 (MB, 默认: 10.0)",
    )
    output_parser = subparsers.add_parser(
        "outout", help="manage regression output artifacts only"
    )
    output_sub = output_parser.add_subparsers(dest="output_command")
    output_sub.add_parser(
        "clean", help="remove output/env, output/runs and output/handover.lock"
    )

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("target", nargs="?")
    run_parser.add_argument(
        "--junit",
        nargs="?",
        const="output/junit.xml",
        default=None,
        help="生成标准 JUnit XML 报告文件 (默认路径: output/junit.xml)",
    )
    run_parser.add_argument(
        "--html",
        nargs="?",
        const="output/report.html",
        default=None,
        help="生成自包含单文件交互式 HTML 测试报告 (默认路径: output/report.html)",
    )

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("target", nargs="?")

    web_parser = subparsers.add_parser("web", help="启动回归测试 Web UI 交互控制台")
    web_parser.add_argument("--host", default="0.0.0.0", help="HTTP 监听地址 (默认: 0.0.0.0)")
    web_parser.add_argument("--port", type=int, default=8080, help="HTTP 监听端口 (默认: 8080)")

    test_parser = subparsers.add_parser("test", help="运行框架单元测试 (unit_tests)")
    test_parser.add_argument("-v", "--verbose", action="store_true", help="输出详细单测信息")

    return parser


def do_env(args: argparse.Namespace) -> int:
    extra_configs = [Path(path).resolve() for path in args.config]
    env = load_regression_config(ROOT_DIR, extra_configs=extra_configs)
    provider = create_environment_provider(
        "fbasecman", env, verbose=args.env_command != "status"
    )

    try:
        if args.env_command == "setup":
            provider.setup(adopt_existing=args.adopt_existing)
            print("env setup complete")
            return 0
        if args.env_command == "clean":
            plan = provider.clean(dry_run=args.dry_run)
            if args.dry_run:
                print(plan.render())
                return 0
            print("env clean complete")
            return 0
    except ShellCommandError as error:
        detail = error.result.stderr.strip() or error.result.stdout.strip()
        print("env %s failed: %s" % (args.env_command, detail or error),
              file=sys.stderr)
        if error.result.returncode in (73, 74):
            print("The test PGDATA ownership marker is missing or does not match.",
                  file=sys.stderr)
            print("Inspect targets with './run.sh env clean --dry-run', then run "
                  "'./run.sh env setup --adopt-existing'.", file=sys.stderr)
        print("Details: %s" % error.result.command, file=sys.stderr)
        return error.result.returncode or 1
    if args.env_command == "status":
        print(provider.status_text())
        return 0
    if args.env_command == "start":
        provider.start()
        print("env start complete")
        return 0
    if args.env_command == "restart":
        provider.restart()
        print("env restart complete")
        return 0
    if args.env_command == "stop":
        provider.stop()
        print("env stop complete")
        return 0
    if args.env_command == "heal":
        res = provider.heal()
        print("env heal complete: %s" % res)
        return 0
    raise AssertionError(f"unknown env command: {args.env_command}")


def do_doctor(args: argparse.Namespace) -> int:
    extra_configs = [Path(path).resolve() for path in args.config]
    env = load_regression_config(ROOT_DIR, extra_configs=extra_configs)
    result = run_doctor(env)
    print("\n".join(result.lines))
    return result.exit_code()


def do_clean(args: argparse.Namespace) -> int:
    result = run_clean(
        ROOT_DIR,
        include_output=args.output,
        prune_logs=getattr(args, "prune_logs", False),
        max_log_size_mb=getattr(args, "max_mb", 10.0),
    )
    print("Clean removed:")
    if result.removed:
        for item in result.removed:
            print("  - %s" % item)
    else:
        print("  - nothing")
    if result.pruned:
        reclaimed_mb = result.reclaimed_bytes / (1024 * 1024)
        print(f"\n日志瘦身清理完成: 共释放 {reclaimed_mb:.2f} MB 磁盘空间 (涉及 {len(result.pruned)} 个工件)")
        for p in result.pruned[:10]:
            print(f"  - [{p['action']}] {p['path']} ({p['reclaimed_bytes'] / (1024*1024):.2f} MB freed)")
        if len(result.pruned) > 10:
            print(f"  ... 以及其他 {len(result.pruned) - 10} 个日志工件")
    if not args.output and not getattr(args, "prune_logs", False):
        print("")
        print("Hint: use './run.sh clean --output' to remove output/ reports and runtime artifacts.")
        print("      use './run.sh clean --prune-logs' to prune large log files and backup dumps.")
    return 0


def do_outout(args: argparse.Namespace) -> int:
    if args.output_command != "clean":
        print("Usage: ./run.sh outout clean")
        return 2
    result = run_clean(ROOT_DIR, output_only=True)
    print("Output clean removed:")
    if result.removed:
        for item in result.removed:
            print("  - %s" % item)
    else:
        print("  - nothing")
    return 0


def do_run(args: argparse.Namespace) -> int:
    if args.target is None:
        print("Usage:")
        print("  ./run.sh run rw_toggle")
        print("  ./run.sh run global_cache")
        print("  ./run.sh run handover")
        print("  ./run.sh run rw_toggle.<case>")
        print("  ./run.sh run global_cache.<case>")
        print("  ./run.sh run failed")
        print("")
        print("Available targets:")
        registry = get_default_registry()
        for s in registry.all_suites():
            print(f"  {s.id:<12} {s.title}")
        print("")
        sample_ids = ", ".join(f"'./run.sh show {s.id}'" for s in registry.all_suites()[:3])
        print(f"Use {sample_ids} to list cases.")
        return 0
    validate_profile_isolation(load_regression_config(ROOT_DIR))
    if args.target in ("failed", "faild"):
        targets = _read_last_failed()
        if not targets:
            _write_last_failed([])
            print("No failed cases recorded from the previous run.")
            return 0
        failures = []
        for target in targets:
            try:
                result = _run_target(target)
            except Exception as exc:
                print("%s rerun failed: %s" % (target, exc), file=sys.stderr)
                result = 1
            if result != 0 or _case_status(target) != "PASS":
                failures.append(target)
        _write_last_failed(failures)
        print("Failed-case rerun: SUCCESS:%d FAIL:%d" %
              (len(targets) - len(failures), len(failures)))
        if getattr(args, "junit", None):
            _export_junit(args.junit)
        if getattr(args, "html", None):
            _export_html(args.html)
        return 1 if failures else 0

    expected = _selected_targets(args.target)
    try:
        result = _run_target(args.target)
    except Exception:
        _write_last_failed(
            target for target in expected if _case_status(target) != "PASS")
        if getattr(args, "junit", None):
            _export_junit(args.junit)
        if getattr(args, "html", None):
            _export_html(args.html)
        raise
    _write_last_failed(target for target in expected if _case_status(target) != "PASS")
    if getattr(args, "junit", None):
        _export_junit(args.junit)
    if getattr(args, "html", None):
        _export_html(args.html)
    return result


def _run_target(target: str) -> int:
    registry = get_default_registry()
    return registry.run_target(ROOT_DIR, target)


def _selected_targets(target: str):
    registry = get_default_registry()
    return registry.selected_targets(target)


def _suite_targets(target: str):
    registry = get_default_registry()
    return registry.suite_targets(target)


def _case_status(target: str):
    suite_name, separator, case_name = target.partition(".")
    if not separator:
        return None
    run_root = ROOT_DIR / "output" / "runs" / suite_name / case_name
    summary = run_root / "summary.json"
    if summary.exists():
        try:
            return json.loads(summary.read_text(encoding="utf-8")).get("status")
        except (OSError, ValueError):
            return None
    report = run_root / "report.txt"
    if report.exists():
        match = re.search(r"^(?:结论|Status):\s*(PASS|FAIL)\s*$",
                          report.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
        return match.group(1) if match else None
    return None


def _read_last_failed():
    try:
        value = json.loads(LAST_FAILED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    targets = value.get("targets", []) if isinstance(value, dict) else []
    return [target for target in targets if _selected_targets(target) == [target]]


def _write_last_failed(targets):
    values = list(dict.fromkeys(targets))
    atomic_write_text(LAST_FAILED_PATH, json.dumps(
        {"targets": values}, ensure_ascii=False, indent=2) + "\n")


def _export_junit(junit_arg):
    try:
        from framework.reporting.junit import export_junit_from_runs
        junit_path = Path(junit_arg)
        if not junit_path.is_absolute():
            junit_path = ROOT_DIR / junit_path
        export_junit_from_runs(ROOT_DIR / "output" / "runs", output_file=junit_path)
        print(f"[JUnit] 已生成 JUnit XML 测试报告: {junit_path}")
    except Exception as exc:
        print(f"[JUnit] 生成 JUnit 报告失败: {exc}", file=sys.stderr)


def _export_html(html_arg):
    try:
        from framework.reporting.html import export_html_from_runs
        html_path = Path(html_arg)
        if not html_path.is_absolute():
            html_path = ROOT_DIR / html_path
        export_html_from_runs(ROOT_DIR / "output" / "runs", output_file=html_path)
        print(f"[HTML] 已生成交互式 HTML 测试报告: {html_path}")
    except Exception as exc:
        print(f"[HTML] 生成 HTML 报告失败: {exc}", file=sys.stderr)


def do_show(args: argparse.Namespace) -> int:
    registry = get_default_registry()
    output = registry.show_target(args.target)
    if output is not None:
        print(output)
        return 0
    print(f"show target not implemented: {args.target or 'all'}", file=sys.stderr)
    return 2


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 2

    if args.command == "env":
        if args.env_command is None:
            parser.parse_args(["env", "--help"])
            return 2
        return do_env(args)
    if args.command == "doctor":
        return do_doctor(args)
    if args.command == "clean":
        return do_clean(args)
    if args.command == "outout":
        return do_outout(args)
    if args.command == "run":
        return do_run(args)
    if args.command == "show":
        return do_show(args)
    if args.command == "web":
        return do_web(args)
    if args.command == "test":
        return do_test(args)
    raise AssertionError(f"unknown command: {args.command}")


def do_web(args: argparse.Namespace) -> int:
    print("[提示] Web 服务管理已独立封装为 ./web.sh 脚本:")
    print("  启动后台服务: ./web.sh start [端口, 默认: 8080]")
    print("  查看服务状态: ./web.sh status")
    print("  停止后台服务: ./web.sh stop")
    print("  实时跟踪日志: ./web.sh logs")
    return 0


def do_test(args: argparse.Namespace) -> int:
    import subprocess
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", "unit_tests", "-t", "."]
    if getattr(args, "verbose", False):
        cmd.append("-v")
    print("Running framework unit tests:", " ".join(cmd), flush=True)
    res = subprocess.run(cmd, cwd=str(ROOT_DIR))
    return res.returncode


if __name__ == "__main__":
    raise SystemExit(main())
