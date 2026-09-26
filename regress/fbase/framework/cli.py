#!/usr/bin/env python3
import argparse
from contextlib import ExitStack
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from framework.config import load_config
from framework.artifacts import ClusterRunLock
from framework.catalog import render_case_details, render_long_time_cases, render_tree
from framework.environment import EnvironmentManager, format_doctor
from framework.errors import ConfigError, RegressError
from framework.runner import SuiteRunner
from framework.state import StateStore
from suites import SUITES


def cluster_lock(config, cluster, identity=""):
    if identity:
        return ClusterRunLock(config.root, cluster, identity)
    return ClusterRunLock(config.root, cluster)


def cluster_lock_identity(config, cluster):
    if not hasattr(config, "cluster"):
        return ""
    return EnvironmentManager(config, cluster).lock_identity()


def build_parser():
    parser = argparse.ArgumentParser(prog="./run.sh")
    commands = parser.add_subparsers(dest="command")

    doctor = commands.add_parser("doctor")
    doctor.add_argument("--cluster", required=True)
    doctor.add_argument("target", nargs="?")

    env = commands.add_parser("env")
    env_commands = env.add_subparsers(dest="env_command")
    env_commands.add_parser("list")
    for name in ("show", "setup", "start", "stop", "reload", "restart", "clean"):
        command = env_commands.add_parser(name)
        command.add_argument("cluster")
    status = env_commands.add_parser("status")
    status.add_argument("cluster", nargs="?")

    show = commands.add_parser("show")
    show.add_argument("target", nargs="?")
    run = commands.add_parser("run")
    run.add_argument("cluster")
    run.add_argument("target", nargs="?")
    selection = run.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true",
                           help="执行当前 cluster 兼容 suite 的全部用例，包括 [LONG-TIME] 用例")
    selection.add_argument("--longtime", action="store_true",
                           help="只执行当前 cluster 兼容 suite 的 [LONG-TIME] 用例")
    run.add_argument("--enable-run-id", action="store_true",
                     help="启用唯一 run-id 和历史输出目录")
    report = commands.add_parser("report")
    output = commands.add_parser("output")
    output_commands = output.add_subparsers(dest="output_command")
    clean_output = output_commands.add_parser("clean")
    clean_output.add_argument("cluster", nargs="?")
    return parser


def print_env_list(config):
    print("CLUSTER  PLUGINS                  GROUPS               CREATED  ENV_ID                     STATE")
    for name in sorted(config.clusters):
        cluster = config.clusters[name]
        state = StateStore(config.root, name).load()
        plugins = ",".join(sorted((cluster.get("plugins") or {}).keys())) or "-"
        groups = ",".join(sorted((cluster.get("groups") or {}).keys())) or "-"
        created = "yes" if state.get("state") != "-" else "no"
        print("%-8s %-24s %-20s %-8s %-26s %s" %
              (name, plugins, groups, created, state.get("env_id", "-"), state.get("state", "-")))


def print_env_show(manager):
    state = manager.store.load()
    print("cluster: %s" % manager.cluster_name)
    print("state: %s" % state.get("state", "-"))
    print("env_id: %s" % state.get("env_id", "-"))
    print("postgres_home: %s" % manager.pg_home)
    print("license_file: %s" % manager.config.license_file)
    print("plugins: %s" % (", ".join(sorted(manager.plugins)) or "-"))
    print("groups: %s" % (", ".join(sorted(manager.groups)) or "-"))
    print("nodes:")
    roles = manager.roles()
    for name, node in manager.nodes.items():
        print("  %s: role=%s host=%s port=%s data_dir=%s" %
              (name, roles.get(name, "standalone"), node["host"], node["port"], node["data_dir"]))
        settings = manager.effective_settings(name)
        rendered = ", ".join("%s=%s" % (key, settings[key]) for key in sorted(settings))
        print("    settings: %s" % rendered)


def print_status(manager):
    state, rows = manager.status_rows()
    print("CLUSTER STATUS:")
    print("CLUSTER     GROUP                    HEALTH       DETAIL")
    for row in manager.cluster_status_rows(state):
        print("%-11s %-24s %-12s %s" % row)
    print("")
    print("NODE STATUS:")
    print("NODE                ROLE                HOST             PORT   PROCESS      RECOVERY  HEALTH      CHECK       PGDATA")
    for row in rows:
        print("%-19s %-19s %-16s %-6s %-12s %-9s %-11s %-11s %s" % row)


def do_env(config, args):
    if args.env_command == "list":
        print_env_list(config)
        return 0
    if args.env_command == "status" and args.cluster is None:
        for index, name in enumerate(sorted(config.clusters)):
            if index:
                print("")
            print_status(EnvironmentManager(config, name))
        return 0
    manager = EnvironmentManager(config, args.cluster)
    if args.env_command == "show":
        print_env_show(manager)
    elif args.env_command == "status":
        print_status(manager)
    elif args.env_command in {"setup", "start", "stop", "reload", "restart", "clean"}:
        identity = manager.lock_identity() if hasattr(manager, "lock_identity") else ""
        with cluster_lock(config, args.cluster, identity):
            getattr(manager, args.env_command)()
    else:
        raise ConfigError("未知 env 命令: %s" % args.env_command)
    return 0


def do_show(target):
    if not target:
        print(render_tree())
        return 0
    if target in {"longtime", "long-time"}:
        print(render_long_time_cases())
        return 0
    print(render_case_details(target))
    return 0


def do_report():
    output_root = ROOT / "output"
    summaries = list((output_root / "runs").glob("*/*/summary.json"))
    summaries.extend(path for path in output_root.glob("*/summary.json")
                     if path.parent.name != "envs")
    summaries.sort()
    if not summaries:
        print("暂无运行报告")
        return 0
    for summary_path in summaries:
        with summary_path.open("r", encoding="utf-8") as stream:
            summary = json.load(stream)
        run_id = summary.get("run_id", summary_path.parent.name)
        for case in summary.get("cases", []):
            print("%-9s | %-25s | %s" % (
                case.get("status", "-"), run_id, case.get("report", "-")))
    return 0


def do_output(config, args):
    output_root = config.root / "output"
    targets = []
    if args.cluster:
        targets.append(output_root / "runs" / args.cluster)
        if args.cluster in SUITES:
            targets.append(output_root / args.cluster)
    else:
        targets.append(output_root / "runs")
        targets.extend(output_root / suite_id for suite_id in sorted(SUITES))
    lock_clusters = ([args.cluster] if args.cluster else
                     sorted(getattr(config, "clusters", {})))
    removed = []
    with ExitStack() as stack:
        for cluster in lock_clusters:
            stack.enter_context(cluster_lock(
                config, cluster, cluster_lock_identity(config, cluster)))
        for path in targets:
            if path.is_dir():
                shutil.rmtree(str(path))
                removed.append(path.relative_to(config.root))
    if removed:
        for path in removed:
            print("[output clean] %s" % path)
    else:
        print("没有可清理的历史测试输出")
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    try:
        config = load_config(ROOT)
        if args.command == "doctor":
            manager = EnvironmentManager(config, args.cluster)
            checks = manager.doctor(args.target)
            print(format_doctor(checks))
            return 1 if any(item[0] != "PASS" for item in checks) else 0
        if args.command == "env":
            if not args.env_command:
                parser.parse_args(["env", "--help"])
                return 2
            return do_env(config, args)
        if args.command == "show":
            return do_show(args.target)
        if args.command == "run":
            return SuiteRunner(config, args.cluster).run(
                args.target, enable_run_id=args.enable_run_id,
                include_all=args.all, longtime_only=args.longtime)
        if args.command == "report":
            return do_report()
        if args.command == "output":
            if args.output_command != "clean":
                parser.parse_args(["output", "--help"])
                return 2
            return do_output(config, args)
        raise ConfigError("未知命令: %s" % args.command)
    except RegressError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("ERROR: 用户中断", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
