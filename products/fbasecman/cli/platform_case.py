"""fbasecman 命令行入口：``run``/``show`` 薄壳，自动注入环境上下文。

对齐旧 vendored CLI 用法::

    ./cli/run.sh run ha_commands          # 跑套件
    ./cli/run.sh run ha_commands.xxx      # 跑单条
    ./cli/run.sh run failed|all
    ./cli/run.sh show ha_commands         # 列用例
    ./cli/run.sh --environment <env> <suite.case>   # 兼容旧调用形态

环境解析顺序：``--environment`` > ``FBASECMAN_ENV`` > 回归绑定 >
唯一 fbasecman 环境记录。
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from platform_app.config import load_settings
from platform_app.filestore import FileStore
from platform_regress.cli import main as run_platform_case
from products.fbasecman.deployment.profile import evidence_root, profile_paths

PRODUCT = "fbasecman"
PRODUCT_DIR = Path(__file__).resolve().parents[1]


def _resolve_environment_id(store, requested):
    if requested:
        return requested
    env_var = os.environ.get("FBASECMAN_ENV")
    if env_var:
        return env_var
    bound = {row["environment_id"]
             for row in store.list_regression_bindings()
             if row["product_id"] == PRODUCT}
    if len(bound) == 1:
        return bound.pop()
    candidates = [row["id"] for row in store.list_environments()
                  if row.get("product_id") == PRODUCT]
    if len(candidates) == 1:
        return candidates[0]
    raise SystemExit(
        "无法确定环境：用 --environment 指定、设 FBASECMAN_ENV，"
        "或在 Web 上绑定回归环境")


def _run(args) -> int:
    settings = load_settings()
    store = FileStore(settings.data_dir)
    environment_id = _resolve_environment_id(store, args.environment)
    environment = store.get_environment(environment_id)
    if environment is None:
        raise SystemExit("环境 %s 不存在" % environment_id)
    _, override = profile_paths(settings, environment_id)
    context = {
        "regress_source": str(settings.product_regress_root(PRODUCT)),
        "regress_override": str(override),
        "regress_report_root": str(evidence_root(settings, environment_id)),
        "history_root": str(settings.environment_dir / "regression" / environment_id),
    }
    from products.fbasecman.provider import suite_case_context
    context.update(suite_case_context(settings, environment))

    target = args.target
    output_base = settings.environment_dir / "regression" / environment_id
    argv = ["--product-dir", str(PRODUCT_DIR),
            "--context-json", json.dumps(context)]
    if target in ("all", "failed"):
        argv += ["--output-dir", str(args.output_dir or output_base)]
        if target == "failed":
            argv.append("failed")
    elif "." in target:
        argv += ["--output-dir",
                 str(args.output_dir or output_base / target), target]
    else:
        argv += ["--output-dir", str(args.output_dir or output_base / target),
                 "--suite", target]
    for node in args.node:
        argv += ["--node", node]
    if args.user:
        argv += ["--user", args.user]
    if args.junit:
        argv += ["--junit", args.junit]
    if args.html:
        argv += ["--html", args.html]
    print("环境 %s；产物 → %s" % (environment_id, argv[argv.index("--output-dir") + 1]))
    return run_platform_case(argv)


def _show(args) -> int:
    catalog = json.loads(
        (PRODUCT_DIR / "regression" / "catalog.json").read_text(encoding="utf-8"))
    for item in catalog["cases"]:
        target = item["target"]
        if args.filter and not target.startswith(args.filter):
            continue
        suffix = "" if item.get("default_enabled", True) else "  (default off)"
        print("%s%s" % (target, suffix))
    return 0


def main() -> int:
    argv = sys.argv[1:]
    # 旧形态 `./run.sh --environment env suite.case`：补 run 动词
    if argv and argv[0] not in ("run", "show", "-h", "--help"):
        argv.insert(0, "run")
    parser = argparse.ArgumentParser(prog="run.sh",
                                     description="fbasecman 回归命令行")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="跑用例/套件")
    run.add_argument("target", help="suite、suite.case、failed 或 all")
    run.add_argument("--environment", "-e")
    run.add_argument("--output-dir", type=Path)
    run.add_argument("--node", action="append", default=[],
                     metavar="NAME=HOST:PORT")
    run.add_argument("--user")
    run.add_argument("--junit", metavar="PATH")
    run.add_argument("--html", metavar="PATH")
    show = commands.add_parser("show", help="列出用例目录")
    show.add_argument("filter", nargs="?", help="按 target 前缀过滤")
    args = parser.parse_args(argv)
    if args.command == "show":
        return _show(args)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", args.environment or "x"):
        parser.error("环境 ID 无效")
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
