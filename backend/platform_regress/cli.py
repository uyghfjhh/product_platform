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
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--context-json", default="{}")
    parser.add_argument("--node", action="append", default=[], metavar="NAME=HOST:PORT")
    parser.add_argument("--user", default=None)
    parser.add_argument("target", nargs="?")
    parser.add_argument("--suite", help="只运行指定 suite 的全部用例")
    parser.add_argument("--junit", metavar="PATH",
                        help="运行结束后导出 JUnit XML 报告（相对路径落在 --output-dir 下）")
    parser.add_argument("--html", metavar="PATH",
                        help="运行结束后导出自包含 HTML 报告（相对路径落在 --output-dir 下）")
    parser.add_argument("--report-title", default="回归测试执行报告",
                        help="HTML 报告标题")
    parser.add_argument("--suite-name", default="regression",
                        help="JUnit testsuites 的 name 属性")
    parser.add_argument("--result-json", action="store_true",
                        help="每条用例结束后在 stdout 追加一行 JSON verdict（默认关闭，"
                             "机器消费请用此开关；人类阅读默认只看进度行）")
    args = parser.parse_args(argv)
    product_dir = args.product_dir.resolve()
    module_path = product_dir / "cases.py"
    if not (product_dir / "product.yaml").is_file() or not module_path.is_file():
        parser.error("产品包缺少 product.yaml 或 cases.py")

    import yaml

    from .sdk import SDK_VERSION
    manifest = yaml.safe_load((product_dir / "product.yaml").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("regression_sdk") != SDK_VERSION:
        parser.error(f"product requires regression_sdk={SDK_VERSION}")

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
    catalog_order = getattr(module, "CASE_ORDER", None) or []
    catalog_index = {target: index for index, target in enumerate(catalog_order)}
    if args.target in ("failed", "faild"):
        from .suites import failed as failed_bookkeeping
        recorded = failed_bookkeeping.read_last_failed(args.state_dir)
        # Suites each record their own last_failed.json; a run-level `failed`
        # merges the freshest per-suite records so `run failed` reruns every
        # target the most recent suite runs left behind.
        targets = [target for target in recorded if target in cases]
        if args.suite:
            targets = [target for target in targets if target.startswith(args.suite + ".")]
        if not targets:
            case_dirs = list((args.output_dir / "cases").glob("*/result.json")) if (args.output_dir / "cases").exists() else []
            discovered = []
            for cp in case_dirs:
                t_name = cp.parent.name
                if args.suite and not t_name.startswith(args.suite + "."):
                    continue
                try:
                    cdata = json.loads(cp.read_text(encoding="utf-8"))
                    if cdata.get("verdict") != "PASS" and t_name in cases:
                        discovered.append(t_name)
                except Exception:
                    pass
            targets = discovered
        if not targets:
            if not args.suite:
                failed_bookkeeping.write_last_failed(args.state_dir, [])
            print("No failed cases recorded from the previous run.")
            return 0
    elif args.target:
        targets = [args.target]
    else:
        # Suites run in catalog order like the legacy executor, and cases the
        # product marked default_enabled=False are skipped unless targeted
        # explicitly.
        selected = [
            target for target in cases
            if not args.suite or target.startswith(args.suite + ".")
        ]
        targets = sorted(selected,
                         key=lambda target: catalog_index.get(target, len(catalog_order)))
        targets = [
            target for target in targets
            if getattr(cases[target], "default_enabled", True)
        ]
    if not targets or any(target not in cases for target in targets):
        parser.error("未知测试目标或 suite")

    # Session members run contiguously at their first member's position,
    # ordered by the declared session order — legacy _group_session_cases.
    by_key = {}
    for index, target in enumerate(targets):
        key = getattr(cases[target], "session_key", None)
        if key:
            by_key.setdefault(key, []).append(
                (getattr(cases[target], "session_order", index), index, target))
    if by_key:
        emitted, ordered = set(), []
        for index, target in enumerate(targets):
            key = getattr(cases[target], "session_key", None)
            if not key:
                ordered.append(target)
            elif key not in emitted:
                ordered.extend(item[2] for item in sorted(by_key[key]))
                emitted.add(key)
        targets = ordered

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

    environment["state_root"] = str(args.state_dir.resolve())
    environment.setdefault("ledger_root", str(args.state_dir.resolve() / "resources"))
    # Run-level prepare: products may start stopped managed nodes before any
    # case evaluates requirements (legacy _ensure_environment_started).
    prepare = getattr(module, "prepare_run", None)
    if callable(prepare):
        prepare(environment)

    # One run shares a run_id so session fixtures and member cases expand
    # identical {run_id} placeholders.
    import uuid
    from datetime import datetime
    run_id = "run_%s_%s" % (datetime.now().strftime("%Y%m%d_%H%M%S"),
                            uuid.uuid4().hex[:12])
    operation_id = os.environ.get("PRODUCT_PLATFORM_TASK_ID")
    args.output_dir = args.output_dir.resolve() / "runs" / run_id
    args.output_dir.mkdir(parents=True, exist_ok=False)
    environment["state_root"] = str(args.state_dir.resolve())
    environment.setdefault("ledger_root", str(args.state_dir.resolve() / "resources"))
    environment["run_id"] = run_id
    (args.output_dir / "run.json").write_text(json.dumps({
        "run_id": run_id, "product_id": manifest.get("id", product_dir.name),
        "environment_id": environment.get("id") or environment.get("environment_id"),
        "operation_id": operation_id,
    }, ensure_ascii=False))
    engine = RegressionEngine()
    sessions: dict = {}
    suite_errors: list = []

    def cleanup_sessions(keep_key=None):
        for key in [key for key in sessions if key != keep_key]:
            session = sessions.pop(key)
            try:
                session["context"].cleanup_fixtures()
            except Exception as exc:  # noqa: BLE001 - session errors surface at suite level
                suite_errors.append("%s: %s" % (key, exc))

    results = []
    for target in targets:
        case = cases[target]
        session_key = getattr(case, "session_key", None)
        # Legacy _cleanup_inactive_sessions: a different session key (or a
        # non-session case) ends the previous session before this case runs.
        cleanup_sessions(keep_key=session_key)
        session_values = {}
        session_error = ""
        if session_key:
            session = sessions.get(session_key)
            if session is None:
                session_context = CaseContext(
                    "_session.%s" % session_key,
                    (args.output_dir / "_sessions" / session_key).resolve(),
                    cancelled.is_set, environment, operation_id, run_id)
                try:
                    case.setup_session(session_context)
                except Exception as exc:  # noqa: BLE001 - member cases inherit the blocker
                    session_error = "共享会话 %s 初始化失败: %s" % (session_key, exc)
                session = {
                    "context": session_context,
                    "error": session_error,
                    "values": {
                        "isolated_mmr_port_mapping": dict(
                            session_context.values.get(
                                "isolated_mmr_port_mapping") or {}),
                    },
                }
                sessions[session_key] = session
            session_error = session["error"]
            session_values = session["values"]
        context = CaseContext(
            target,
            (args.output_dir / "cases" / target).resolve(),
            cancelled.is_set, environment, operation_id, run_id)
        context.values.update(session_values)
        if session_error:
            context.values["session_error"] = session_error
        result = engine.run(case, context)
        results.append(result)
        # Old-style per-case progress line (like the legacy suites printed):
        # `[12/76] suite.case                          PASS      3.412s`.
        line = "[%d/%d] %-58s %-8s %8.3fs" % (
            len(results), len(targets), result.target, result.verdict,
            result.duration_seconds)
        if result.verdict != "PASS" and result.reason:
            line += ": %s" % (result.reason.splitlines() or [""])[0][:160]
        print(line, flush=True)
        # Render the per-case human report (report.txt/steps.json/
        # summary.json) from the event stream — the legacy suite format,
        # generated for every case so native cases report no thinner than
        # executor-hosted ones.  Mirrors into the legacy run tree when the
        # product injected regress_report_root and no legacy report exists.
        try:
            from .reporting.case_report import write_case_artifacts
            write_case_artifacts(context, result, environment,
                                 purpose=str(getattr(case, "title", "") or ""))
        except Exception:  # noqa: BLE001 - report generation never fails a verdict
            pass
        # Flaky tracking: append one verdict line per executed case to the
        # environment-level history so UI/API can flag unstable targets.
        state_root = environment["state_root"]
        if state_root:
            try:
                history_dir = Path(state_root)
                history_dir.mkdir(parents=True, exist_ok=True)
                with (history_dir / "history.jsonl").open(
                        "a", encoding="utf-8") as handle:
                    handle.write(json.dumps({
                        "target": result.target, "verdict": result.verdict,
                        "duration": result.duration_seconds,
                        "run_id": run_id, "at": datetime.now().isoformat(),
                    }, ensure_ascii=False) + "\n")
            except OSError:
                pass
        if args.result_json:
            print(json.dumps(result.to_dict(), ensure_ascii=False), flush=True)
        if result.verdict == "CANCELLED":
            break
    cleanup_sessions()
    # Bookkeep non-PASS targets so a later `run failed` reruns exactly them.
    # A CANCELLED run leaves untried targets out — they never produced a
    # verdict, so only executed non-PASS results are recorded.
    from .suites import failed as failed_bookkeeping
    args.output_dir.mkdir(parents=True, exist_ok=True)
    current_passed = {item.target for item in results if item.verdict == "PASS"}
    current_failed = {item.target for item in results if item.verdict != "PASS"}
    try:
        prev_recorded = failed_bookkeeping.read_last_failed(args.state_dir)
    except Exception:
        prev_recorded = []
    updated_failed = [t for t in prev_recorded if t not in current_passed and t not in current_failed]
    updated_failed.extend(current_failed)
    failed_bookkeeping.write_last_failed(args.state_dir, updated_failed)
    if args.junit or args.html:
        # Reports render from the in-memory CaseResult fact model — the same
        # data result.json/suite-result.json persist — never from report text.
        from .reporting.export import export_run_reports

        def _resolve(path_text):
            path = Path(path_text)
            return path if path.is_absolute() else args.output_dir / path

        # Attach per-case step journals (expected/actual pairs) so the
        # offline HTML report can render the step-level diff inspector.
        payloads = []
        for item in results:
            payload = item.to_dict()
            case_dir = args.output_dir / "cases" / item.target
            steps = _load_steps(case_dir)
            if steps:
                payload["steps"] = steps
            payloads.append(payload)

        export_run_reports(
            payloads,
            junit_path=_resolve(args.junit) if args.junit else None,
            html_path=_resolve(args.html) if args.html else None,
            title=args.report_title, suite_name=args.suite_name)
    if len(results) == 1 and not suite_errors:
        return EXIT_CODES[results[0].verdict]
    counts = {status: sum(item.verdict == status for item in results) for status in EXIT_CODES}
    # Old-style suite summary line, e.g. `Total: PASS:60 FAIL:2 ERROR:1`.
    print("Total: " + " ".join(
        "%s:%d" % (status, counts[status]) for status in EXIT_CODES
        if counts[status]) + ("  (targets:%d)" % len(targets)
                              if len(targets) != len(results) else ""),
        flush=True)
    (args.output_dir / "suite-result.json").write_text(
        json.dumps({"targets": targets, "counts": counts,
                    "session_cleanup_errors": suite_errors,
                    "results": [item.to_dict() for item in results]},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return 0 if counts["PASS"] == len(results) and not suite_errors else 1


def _load_steps(case_dir: Path) -> list:
    """Newest mirrored step journal under a case output dir, if present."""
    artifacts = case_dir / "artifacts"
    candidates = sorted(artifacts.glob("*/steps.json")) if artifacts.is_dir() else []
    if not candidates and (case_dir / "steps.json").is_file():
        candidates = [case_dir / "steps.json"]
    if not candidates:
        return []
    try:
        data = json.loads(candidates[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list):
        return []
    return [
        {"title": step.get("title"), "result": step.get("result"),
         "expected": step.get("expected"), "actual": step.get("actual")}
        for step in steps if isinstance(step, dict)
    ]


if __name__ == "__main__":
    raise SystemExit(main())
