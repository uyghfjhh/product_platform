"""Isolated CLI entrypoint for product cases using the platform test SDK."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import threading
from pathlib import Path

from .engine import CaseContext, RegressionEngine


EXIT_CODES = {"PASS": 0, "FAIL": 1, "BLOCKED": 3, "ERROR": 2, "CANCELLED": 130}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one product regression case")
    parser.add_argument("--product-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--context-json", default="{}")
    parser.add_argument("--node", action="append", default=[], metavar="NAME=HOST:PORT")
    parser.add_argument("--user", default=None)
    parser.add_argument("target", nargs="?")
    parser.add_argument("--suite", help="只运行指定 suite 的全部用例")
    args = parser.parse_args(argv)
    product_dir = args.product_dir.resolve()
    module_path = product_dir / "cases.py"
    if not (product_dir / "product.yaml").is_file() or not module_path.is_file():
        parser.error("产品包缺少 product.yaml 或 cases.py")

    # Product code is trusted installed code, but the module path is derived
    # solely from the selected package and never from a client-supplied import.
    spec = importlib.util.spec_from_file_location("_platform_regression_cases", module_path)
    if spec is None or spec.loader is None:
        parser.error("无法加载产品用例")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = getattr(module, "CASES", None)
    if not isinstance(cases, dict):
        parser.error("产品用例注册表无效")
    targets = [args.target] if args.target else sorted(
        target for target in cases if not args.suite or target.startswith(args.suite + ".")
    )
    if not targets or any(target not in cases for target in targets):
        parser.error("未知测试目标或 suite")

    cancelled = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: cancelled.set())
    signal.signal(signal.SIGINT, lambda *_: cancelled.set())
    try:
        environment = json.loads(args.context_json)
    except ValueError as exc:
        parser.error(f"节点上下文不是有效 JSON: {exc}")
    if not isinstance(environment, dict):
        parser.error("节点上下文必须是 JSON 对象")
    if args.node:
        nodes = dict(environment.get("nodes") or {})
        for item in args.node:
            name, separator, endpoint = item.partition("=")
            host, port_separator, port_text = endpoint.rpartition(":")
            if not separator or not name or not port_separator or not host or not port_text.isdigit():
                parser.error(f"节点格式无效: {item}")
            port = int(port_text)
            if not 1 <= port <= 65535 or name in nodes:
                parser.error(f"节点端口无效或重复: {item}")
            nodes[name] = {"host": host, "port": port}
        environment["nodes"] = nodes
    if args.user:
        environment["user"] = args.user
    results = []
    for target in targets:
        context = CaseContext(
            target, (args.output_dir / target if len(targets) > 1 else args.output_dir).resolve(),
            cancelled.is_set, environment, os.environ.get("PRODUCT_PLATFORM_TASK_ID"),
        )
        result = RegressionEngine().run(cases[target], context)
        results.append(result)
        print(json.dumps(result.to_dict(), ensure_ascii=False), flush=True)
        if result.verdict == "CANCELLED":
            break
    if len(results) == 1:
        return EXIT_CODES[results[0].verdict]
    counts = {status: sum(item.verdict == status for item in results) for status in EXIT_CODES}
    (args.output_dir / "suite-result.json").parent.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "suite-result.json").write_text(
        json.dumps({"targets": targets, "counts": counts,
                    "results": [item.to_dict() for item in results]},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return 0 if counts["PASS"] == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
