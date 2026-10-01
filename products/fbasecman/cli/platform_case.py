"""fbasecman 命令行入口——对齐旧 fbasecman_regress ./run.sh 用法::

    ./cli/run.sh [--config x.yaml] run [suite|suite.case|failed|all] [--junit [P]] [--html [P]]
    ./cli/run.sh show [suite]
    ./cli/run.sh env setup|clean|status|start|restart|stop|heal   # 转平台 deployment.*
    ./cli/run.sh doctor                                            # 转平台 deployment.doctor
    ./cli/run.sh clean [--output] [--prune-logs] [--max-mb N]
    ./cli/run.sh outout clean
    ./cli/run.sh web | test [-v]

环境解析顺序：``--environment``/``-e`` > ``FBASECMAN_ENV`` > 回归绑定 >
唯一 fbasecman 环境记录。部署/体检等环境动作统一走平台
``command_for`` 生成的命令，不在产品侧另起实现。
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from platform_app.config import load_settings
from platform_app.filestore import FileStore
from platform_regress.cli import main as run_platform_case

from products.fbasecman.deployment.profile import evidence_root, profile_paths

PRODUCT = "fbasecman"
PRODUCT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PRODUCT_DIR.parents[1]
VERBS = ("run", "show", "env", "doctor", "clean", "outout", "test")


def _resolve_environment_id(store, requested):
    if requested:
        return requested
    env_var = os.environ.get("FBASECMAN_ENV")
    if env_var:
        return env_var
    bound = {row["environment_id"]
             for row in store.bindings.list_regression_bindings()
             if row["product_id"] == PRODUCT}
    if len(bound) == 1:
        return bound.pop()
    candidates = [row["id"] for row in store.environments.list_environments()
                  if row.get("product_id") == PRODUCT]
    if len(candidates) == 1:
        return candidates[0]
    raise SystemExit(
        "无法确定环境：用 --environment 指定、设 FBASECMAN_ENV，"
        "或在 Web 上绑定回归环境")


def _environment(store, requested):
    environment_id = _resolve_environment_id(store, requested)
    environment = store.environments.get_environment(environment_id)
    if environment is None:
        raise SystemExit("环境 %s 不存在" % environment_id)
    return environment


def _catalog():
    return json.loads((PRODUCT_DIR / "regression" / "catalog.json")
                      .read_text(encoding="utf-8"))["cases"]


def _suites():
    suites = {}
    for item in _catalog():
        suites.setdefault(item["suite"],
                          {"title": item.get("suite_title") or item["suite"],
                           "cases": []})["cases"].append(item)
    return suites


def _deployment_action(environment_id, action_id, dry_run=False):
    """经平台 command_for 同步执行 deployment.* 命令（与 Web/队列同一实现）。"""
    from platform_app.local_execution import run_local_operation
    from platform_app.operations import OperationRequest
    from platform_app.providers import command_for

    settings = load_settings()
    store = FileStore(settings.data_dir, runtime_dir=settings.runtime_dir, logs_dir=settings.logs_dir)
    environment = _environment(store, environment_id)
    target = environment.get("deployment_target") or ""
    spec = command_for(settings, environment, action_id, target, {})
    if dry_run:
        print(" ".join(str(part) for part in spec.command))
        return 0
    task = run_local_operation(settings, store, OperationRequest(
        environment_id=environment["id"], action=action_id, target=target,
        acknowledge_change=True, parameters={"stream_output": True}))
    return 0 if task["status"] == "SUCCEEDED" else 1


def _env(args) -> int:
    verb = args.env_command
    if verb == "setup":
        if args.adopt_existing:
            print("提示：--adopt-existing 已由平台 .pgcluster-managed 边界取代，忽略")
        code = _deployment_action(args.environment, "deployment.create")
        if code != 0:
            return code
        return _deployment_action(args.environment, "tests.prepare_fbasecman")
    if verb == "clean":
        return _deployment_action(args.environment, "deployment.clean",
                                  dry_run=args.dry_run)
    return _deployment_action(args.environment, "deployment.%s" % verb)


def _doctor(args) -> int:
    return _deployment_action(args.environment, "deployment.doctor")


def _clean(args, output_only=False) -> int:
    from products.fbasecman.cli.clean import run_clean

    settings = load_settings()
    store = FileStore(settings.data_dir, runtime_dir=settings.runtime_dir, logs_dir=settings.logs_dir)
    environment = _environment(store, args.environment)
    result = run_clean(
        settings.product_regress_root(PRODUCT),
        evidence_root(settings, environment["id"]),
        include_output=getattr(args, "output", False), output_only=output_only,
        prune_logs=getattr(args, "prune_logs", False),
        max_log_size_mb=getattr(args, "max_mb", 10.0))
    print(("Output clean" if output_only else "Clean") + " removed:")
    for item in result.removed or ["  - nothing"]:
        print("  - %s" % item)
    if result.pruned:
        print("\n日志瘦身清理完成: 共释放 %.2f MB (涉及 %d 个工件)"
              % (result.reclaimed_bytes / (1024 * 1024), len(result.pruned)))
        for entry in result.pruned[:10]:
            print("  - [%s] %s (%.2f MB freed)"
                  % (entry["action"], entry["path"],
                     entry["reclaimed_bytes"] / (1024 * 1024)))
        if len(result.pruned) > 10:
            print("  ... 以及其他 %d 个日志工件" % (len(result.pruned) - 10))
    if not output_only and not args.output and not args.prune_logs:
        print("")
        print("Hint: use './run.sh clean --output' to remove output/ reports and runtime artifacts.")
        print("      use './run.sh clean --prune-logs' to prune large log files and backup dumps.")
    return 0


def _run(args, configs) -> int:
    if args.target is None:
        print("Usage:")
        print("  ./run.sh run ha_commands")
        print("  ./run.sh run handover")
        print("  ./run.sh run ha_commands.<case>")
        print("  ./run.sh run failed")
        print("")
        print("Available targets:")
        for suite_id, suite in _suites().items():
            print("  %-16s %s" % (suite_id, suite["title"]))
        print("")
        print("Use './run.sh show <suite>' to list cases.")
        return 0
    settings = load_settings()
    store = FileStore(settings.data_dir, runtime_dir=settings.runtime_dir, logs_dir=settings.logs_dir)
    environment = _environment(store, args.environment)
    environment_id = environment["id"]
    _, override = profile_paths(settings, environment_id)
    context = {
        "regress_source": str(settings.product_regress_root(PRODUCT)),
        "regress_override": str(override),
        "regress_report_root": str(evidence_root(settings, environment_id)),
        "state_root": str(settings.artifact_dir(PRODUCT, environment_id)),
    }
    context.update(ledger_root=str(settings.resource_dir(PRODUCT, environment_id)),
                   runtime_root=str(settings.runtime_dir / "products" / PRODUCT / environment_id))
    if configs:
        context["regress_extra_configs"] = [
            str(Path(path).resolve()) for path in configs]
    from products.fbasecman.provider import suite_case_context
    context.update(suite_case_context(settings, environment))

    target = "failed" if args.target == "faild" else args.target
    output_base = settings.artifact_dir(PRODUCT, environment_id)
    report_root = evidence_root(settings, environment_id)
    argv = ["--product-dir", str(PRODUCT_DIR),
            "--context-json", json.dumps(context)]
    if target in ("all", "failed"):
        argv += ["--output-dir", str(args.output_dir or output_base)]
        if target == "failed":
            argv.append("failed")
    elif "." in target:
        argv += ["--output-dir",
                 str(args.output_dir or output_base), target]
    else:
        argv += ["--output-dir", str(args.output_dir or output_base),
                 "--suite", target]
    argv += ["--state-dir", str(settings.regression_state_dir(PRODUCT, environment_id))]
    for node in args.node:
        argv += ["--node", node]
    if args.user:
        argv += ["--user", args.user]
    # 旧默认：相对路径落在证据根下（ROOT_DIR/output/junit.xml 语义）
    for flag, value in (("--junit", args.junit), ("--html", args.html)):
        if value is None:
            continue
        path = Path(value)
        argv += [flag, str(path if path.is_absolute() else report_root / path)]
    print("环境 %s；产物 → %s" % (environment_id, argv[argv.index("--output-dir") + 1]))
    from platform_app.resources import resource_lock
    with resource_lock(settings, environment):
        return run_platform_case(argv)


def _show(args) -> int:
    suites = _suites()
    if args.target and args.target != "all" and "." not in args.target:
        suites = {args.target: suites[args.target]} if args.target in suites else {}
        if not suites:
            print("show target not implemented: %s" % args.target, file=sys.stderr)
            return 2
    parts = []
    for suite_id, suite in suites.items():
        lines = ["%s - %s" % (suite_id, suite["title"])]
        lines.extend("  - %s" % item["target"] for item in suite["cases"])
        parts.append("\n".join(lines))
    print("\n\n".join(parts))
    return 0


def _test(args) -> int:
    command = [str(REPO_DIR / ".venv/bin/python"), "-m", "pytest", "tests/"]
    command.append("-v" if args.verbose else "-q")
    print("Running framework unit tests:", " ".join(command), flush=True)
    return subprocess.run(command, cwd=str(REPO_DIR)).returncode


def _extract_configs(argv):
    """把全局 --config FILE（可出现在动词前后）抽出，避免 argparse 层级坑。"""
    configs, rest, index = [], [], 0
    while index < len(argv):
        token = argv[index]
        if token == "--config" and index + 1 < len(argv):
            configs.append(argv[index + 1])
            index += 2
        elif token.startswith("--config="):
            configs.append(token.partition("=")[2])
            index += 1
        else:
            rest.append(token)
            index += 1
    return rest, configs


def main() -> int:
    argv = sys.argv[1:]
    argv, configs = _extract_configs(argv)
    # 旧兼容：无动词形态 `--environment env suite.case` → run
    if argv and argv[0] not in VERBS and argv[0] not in ("-h", "--help"):
        argv.insert(0, "run")
    parser = argparse.ArgumentParser(prog="./run.sh")
    commands = parser.add_subparsers(dest="command")

    run = commands.add_parser("run")
    run.add_argument("target", nargs="?")
    run.add_argument("--environment", "-e")
    run.add_argument("--output-dir", type=Path)
    run.add_argument("--node", action="append", default=[],
                     metavar="NAME=HOST:PORT")
    run.add_argument("--user")
    run.add_argument("--junit", nargs="?", const="output/junit.xml",
                     help="生成标准 JUnit XML 报告 (默认: output/junit.xml)")
    run.add_argument("--html", nargs="?", const="output/report.html",
                     help="生成自包含 HTML 报告 (默认: output/report.html)")

    show = commands.add_parser("show")
    show.add_argument("target", nargs="?")

    env_parser = commands.add_parser("env")
    env_parser.add_argument("--environment", "-e")
    env_sub = env_parser.add_subparsers(dest="env_command")
    setup = env_sub.add_parser("setup")
    setup.add_argument("--adopt-existing", action="store_true")
    env_clean = env_sub.add_parser("clean")
    env_clean.add_argument("--dry-run", action="store_true")
    for name in ("status", "start", "restart", "stop", "heal"):
        env_sub.add_parser(name)

    doctor = commands.add_parser("doctor")
    doctor.add_argument("--environment", "-e")

    clean = commands.add_parser("clean")
    clean.add_argument("--environment", "-e")
    clean.add_argument("--output", action="store_true",
                       help="also remove regression runtime artifacts")
    clean.add_argument("--prune-logs", "--prune", action="store_true",
                       help="清理 runs 中过期日志备份并截断超大日志 (保留报告与摘要)")
    clean.add_argument("--max-mb", type=float, default=10.0,
                       help="日志截断阈值 (MB, 默认: 10.0)")

    outout = commands.add_parser("outout")
    outout.add_argument("--environment", "-e")
    outout_sub = outout.add_subparsers(dest="output_command")
    outout_sub.add_parser("clean")

    test = commands.add_parser("test")
    test.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    if getattr(args, "environment", None) and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", args.environment):
        parser.error("环境 ID 无效")
    if args.command == "env":
        if args.env_command is None:
            parser.parse_args(["env", "--help"])
            return 2
        return _env(args)
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "clean":
        return _clean(args)
    if args.command == "outout":
        if args.output_command != "clean":
            print("Usage: ./run.sh outout clean")
            return 2
        return _clean(args, output_only=True)
    if args.command == "run":
        return _run(args, configs)
    if args.command == "show":
        return _show(args)
    if args.command == "test":
        return _test(args)
    raise AssertionError("unknown command: %s" % args.command)


if __name__ == "__main__":
    raise SystemExit(main())
