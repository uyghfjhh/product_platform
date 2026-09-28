"""零配置产品回归 CLI：`./cli/run.sh <command>`。

环境与上下文全部来自文件存储：bindings.yaml 选环境，环境 YAML 提供
主机/端口/库/用户，provider.command() 生成与 Web 任务完全一致的命令。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from platform_app.config import load_settings  # noqa: E402
from platform_app.filestore import FileStore  # noqa: E402
from platform_app.product_catalog import discover_products  # noqa: E402
from platform_app.providers import command_for, provider_for  # noqa: E402

TEST_ACTIONS = {
    "fbasecman": "tests.fbasecman",
    "fbase-database": "tests.fbase",
}


def _products(settings):
    return discover_products(settings.products_root)


def _catalog_targets(settings, product_id: str) -> set[str]:
    provider = provider_for(settings, product_id)
    targets = set()
    for case in provider.discover(settings):
        targets.add(case["target"])
        targets.add(case["target"].split(".", 1)[0])
    return targets


def _resolve_product(settings, store, target: str,
                     env_id: str | None, product_id: str | None) -> tuple[str, dict]:
    """按 --env/--product/bindings.yaml 选定产品与环境。"""
    if env_id:
        environment = store.get_environment(env_id)
        if environment is None:
            raise SystemExit(f"环境不存在: {env_id}")
        return environment["product_id"], environment
    if product_id:
        candidates = [product_id]
    elif target in {"all", "failed"}:
        candidates = [pid for pid in _products(settings) if pid in TEST_ACTIONS]
    else:
        candidates = [
            pid for pid in _products(settings)
            if pid in TEST_ACTIONS and target in _catalog_targets(settings, pid)
        ]
        if not candidates:
            candidates = [pid for pid in _products(settings) if pid in TEST_ACTIONS]
    bound = [
        pid for pid in candidates
        if any(b["product_id"] == pid for b in store.list_regression_bindings())
    ]
    if len(bound) > 1:
        # 多产品歧义：优先选中绑定环境当前可连通的产品
        reachable = [
            pid for pid in bound
            if any(
                (env := store.get_environment(b["environment_id"]))
                and env.get("host") and env.get("port")
                and _tcp_check(env["host"], env["port"], timeout=1.0)
                for b in store.list_regression_bindings()
                if b["product_id"] == pid
            )
        ]
        if len(reachable) == 1:
            bound = reachable
        else:
            raise SystemExit("多个产品可执行该目标，请用 --product 指定: %s"
                             % ", ".join(bound))
    if not bound:
        raise SystemExit("没有已绑定环境的产品可执行 %s，请先在 Web 或 "
                         "platform_app cli 中建立绑定" % target)
    product = bound[0]
    bindings = [b for b in store.list_regression_bindings()
                if b["product_id"] == product]
    environment = store.get_environment(bindings[0]["environment_id"])
    if environment is None:
        raise SystemExit("绑定指向的环境已不存在: %s" % bindings[0]["environment_id"])
    return product, environment


def _print_report_paths(output_dir: Path) -> None:
    for name in ("report.html", "junit.xml", "suite-result.json"):
        path = output_dir / name
        if path.is_file():
            print(f"[report] {path}")


def _spec_output_dir(command: list[str]) -> Path | None:
    if "--output-dir" in command:
        return Path(command[command.index("--output-dir") + 1])
    return None


def cmd_run(args) -> int:
    settings = load_settings()
    store = FileStore(settings.data_dir)
    product, environment = _resolve_product(
        settings, store, args.target, args.env, args.product)
    profile = args.profile
    if profile is None:
        bindings = [b for b in store.list_regression_bindings()
                    if b["product_id"] == product
                    and b["environment_id"] == environment["id"]]
        profile = bindings[0]["profile_id"] if bindings else "default"
    parameters = {"profile": profile}
    if args.cluster:
        parameters["cluster"] = args.cluster
    if args.watch:
        args.watch = False  # 首轮立即执行，然后进入监视循环
        code = cmd_run(args)
        args.watch = True
        settings = load_settings()
        return _watch_loop(settings, environment, args) or code
    action = TEST_ACTIONS[product]
    spec = command_for(settings, environment, action, args.target, parameters)
    command = list(spec.command)
    output_dir = _spec_output_dir(command)
    if output_dir is not None:
        # 统一在输出目录产出自包含报告，结束后打印绝对路径。
        command += ["--html", "report.html", "--junit", "junit.xml",
                    "--report-title", f"{product} 回归报告"]
    env = dict(os.environ)
    print(f"[run] product={product} env={environment['id']} "
          f"target={args.target}", flush=True)
    print(f"[run] {' '.join(command)}", flush=True)
    process = subprocess.run(command, cwd=spec.cwd, env=env)
    if output_dir is not None:
        _print_report_paths(output_dir)
        suite_result = output_dir / "suite-result.json"
        if suite_result.is_file():
            counts = json.loads(suite_result.read_text()).get("counts", {})
            print("[run] verdicts: " + " ".join(
                f"{key}={value}" for key, value in sorted(counts.items())
                if value), flush=True)
    return process.returncode


def _watch_fingerprint(settings, environment) -> dict[str, float]:
    """源码/配置/二进制 mtimes——任一变化即触发重跑。"""
    watched = [settings.product_regress_root(environment["product_id"])]
    env_yaml = settings.data_dir / "environments" / f"{environment['id']}.yaml"
    watched.append(env_yaml)
    profile_dir = (settings.environment_dir / "profiles" / environment["id"])
    if profile_dir.is_dir():
        watched.append(profile_dir)
    fingerprint = {}
    for root in watched:
        root = Path(root)
        if root.is_file():
            fingerprint[str(root)] = root.stat().st_mtime
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {
                    ".py", ".yaml", ".yml", ".json", ".sh", ".sql", ".java"}:
                try:
                    fingerprint[str(path)] = path.stat().st_mtime
                except OSError:
                    continue
    binary = os.environ.get("PRODUCT_PLATFORM_FBASECMAN_BIN")
    if binary and Path(binary).is_file():
        fingerprint[binary] = Path(binary).stat().st_mtime
    return fingerprint


def _watch_loop(settings, environment, args) -> int:
    print("[watch] 监视回归资源与配置变更，Ctrl-C 退出", flush=True)
    baseline = _watch_fingerprint(settings, environment)
    try:
        while True:
            time.sleep(2.0)
            current = _watch_fingerprint(settings, environment)
            if current != baseline:
                changed = sorted(set(current) ^ set(baseline)
                                 | {k for k in current
                                     if baseline.get(k) != current[k]})
                print(f"[watch] 检测到变更，重新运行: {changed[:3]}"
                      f"{' ...' if len(changed) > 3 else ''}", flush=True)
                baseline = current
                cmd_run(args)
    except KeyboardInterrupt:
        print("[watch] 已停止")
        return 0


def cmd_show(args) -> int:
    settings = load_settings()
    store = FileStore(settings.data_dir)
    product, environment = _resolve_product(
        settings, store, args.target, args.env, args.product)
    provider = provider_for(settings, product)
    cases = provider.discover(settings)
    matched = [c for c in cases
               if c["target"] == args.target
               or c["target"].startswith(args.target + ".")]
    if not matched:
        raise SystemExit(f"目录中没有目标: {args.target}")
    for case in matched:
        print(json.dumps(case, ensure_ascii=False))
    result = store.get_result(product, environment["id"], args.target)
    if result:
        print(json.dumps({"latest_result": result}, ensure_ascii=False, indent=2))
    evidence = (settings.environment_dir / "regression" / environment["id"]
                / args.target)
    if evidence.is_dir():
        print(f"[evidence] {evidence}")
    return 0


def _tcp_check(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def cmd_doctor(args) -> int:
    settings = load_settings()
    store = FileStore(settings.data_dir)
    failures = 0

    def report(ok: bool, label: str, detail: str = ""):
        nonlocal failures
        mark = "ok" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"[{mark}] {label}{(': ' + detail) if detail else ''}")

    report(settings.data_dir.is_dir(), "数据目录", str(settings.data_dir))
    for product_id in sorted(_products(settings)):
        root = settings.product_regress_root(product_id)
        report(root.is_dir(), f"{product_id} 回归资源", str(root))
    bindings = store.list_regression_bindings()
    report(bool(bindings), "回归绑定", f"{len(bindings)} 条")
    for binding in bindings:
        env = store.get_environment(binding["environment_id"])
        label = f"{binding['product_id']}:{binding['profile_id']} -> {binding['environment_id']}"
        if env is None:
            report(False, label, "环境记录缺失")
            continue
        report(True, label)
        if env.get("deployment_config"):
            report(Path(env["deployment_config"]).is_file(),
                   f"{env['id']} deployment_config", env["deployment_config"])
        if env.get("host") and env.get("port"):
            report(_tcp_check(env["host"], env["port"]),
                   f"{env['id']} 连通性", f"{env['host']}:{env['port']}")
    for task in store.unfinished_tasks():
        report(False, "未结束任务",
               f"{task['id']} {task['action']} {task['target']}")
    return 1 if failures else 0


def cmd_pack(args) -> int:
    """导出故障分析包（bug bundle）：环境/结果/证据/报告/历史打成 zip。"""
    settings = load_settings()
    store = FileStore(settings.data_dir)
    _, environment = _resolve_product(
        settings, store, args.target or "all", args.env, args.product)
    from platform_app import bundle
    target = args.target if args.target not in (None, "all") else None
    payload = bundle.build_bug_bundle(
        settings, store, environment["id"], target)
    suffix = target or "all"
    output = Path(args.output or f"bundle-{environment['id']}-{suffix}.zip")
    output.write_bytes(payload)
    print(f"[pack] {output.resolve()} ({len(payload)} bytes)")
    return 0


def cmd_reset(args) -> int:
    """重置环境：清理残留进程并重启集群，等待健康检查通过。"""
    settings = load_settings()
    store = FileStore(settings.data_dir)
    _, environment = _resolve_product(
        settings, store, "all", args.env, args.product)
    target = args.node or environment.get("deployment_target")
    if not target:
        raise SystemExit("环境未声明 deployment_target，请用 --node 指定")
    spec = command_for(settings, environment, "deployment.reset", target, {})
    print(f"[reset] env={environment['id']} target={target}", flush=True)
    return subprocess.run(list(spec.command), cwd=spec.cwd).returncode


def cmd_clean(args) -> int:
    """清理运行产物：回归输出目录、失败标记、遗留锁文件。"""
    settings = load_settings()
    store = FileStore(settings.data_dir)
    removed = []

    def remove_tree(path: Path):
        if path.exists():
            import shutil
            shutil.rmtree(path)
            removed.append(str(path))

    envs = ([store.get_environment(args.env)] if args.env
            else store.list_environments())
    for env in envs:
        if env is None:
            continue
        remove_tree(settings.environment_dir / "regression" / env["id"])
        legacy = (settings.environment_dir / "legacy_cman" / env["id"]
                  / "output" / "runs")
        remove_tree(legacy)
        remove_tree(legacy.parent / "last_failed.json")
    locks = settings.data_dir / "locks"
    if locks.is_dir():
        for lock in locks.glob("*.lock"):
            lock.unlink()
            removed.append(str(lock))
    for item in removed:
        print(f"[clean] removed {item}")
    if not removed:
        print("[clean] 无需清理")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run.sh", description="平台回归零配置 CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="运行回归用例/suite/failed/all")
    run.add_argument("target")
    run.add_argument("--env", help="环境 ID；缺省按 bindings.yaml 解析")
    run.add_argument("--product", help="产品 ID（多产品歧义时指定）")
    run.add_argument("--profile", help="绑定 profile（默认该产品的第一条绑定）")
    run.add_argument("--cluster", help="集群 profile 参数")
    run.add_argument("--watch", action="store_true",
                     help="监视源码/配置/二进制变化并自动重跑")
    run.set_defaults(func=cmd_run)
    show = sub.add_parser("show", help="查看用例元数据与最近结果")
    show.add_argument("target")
    show.add_argument("--env")
    show.add_argument("--product")
    show.set_defaults(func=cmd_show)
    doctor = sub.add_parser("doctor", help="环境/绑定/连通性体检")
    doctor.set_defaults(func=cmd_doctor)
    clean = sub.add_parser("clean", help="清理运行产物与遗留锁")
    clean.add_argument("--env", help="只清理指定环境")
    clean.set_defaults(func=cmd_clean)
    pack = sub.add_parser("pack", help="导出故障分析包（bug bundle zip）")
    pack.add_argument("target", nargs="?", help="用例目标；缺省打包整个环境")
    pack.add_argument("--env")
    pack.add_argument("--product")
    pack.add_argument("--output", "-o", help="输出 zip 路径")
    pack.set_defaults(func=cmd_pack)
    reset = sub.add_parser("reset", help="重置环境：清残留进程并重启集群")
    reset.add_argument("--env")
    reset.add_argument("--product")
    reset.add_argument("--node", help="集群目标（默认取环境 deployment_target）")
    reset.set_defaults(func=cmd_reset)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
