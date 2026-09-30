"""Publish regression facts identically for every installed product."""

import json
from pathlib import Path

from platform_regress.sdk import Verdict


def _read(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def publish_regression_results(
    store,
    settings,
    environment,
    task,
    terminal,
    reason,
    *,
    case_targets,
    output_root=None,
    profile="default",
):
    root = Path(output_root or settings.output_dir / "regression" / environment["id"])
    targets = set(case_targets)
    target = task["target"]
    directory = root / target
    if target in targets:
        candidates = [(directory / "result.json", _read(directory / "result.json"))]
    else:
        # all/failed can write an aggregate at the environment root. Suite
        # invocations write under their requested target; both are explicit.
        aggregate_paths = [directory / "suite-result.json", root / "suite-result.json"]
        candidates = []
        for aggregate_path in aggregate_paths:
            payload = _read(aggregate_path)
            for row in (
                payload.get("results", [])
                if isinstance(payload.get("results"), list)
                else []
            ):
                if isinstance(row, dict) and row.get("target") in targets:
                    case_dir = aggregate_path.parent / row["target"]
                    if not (case_dir / "result.json").is_file():
                        direct = _read(aggregate_path.parent / "result.json")
                        if (
                            direct.get("target") == row["target"]
                            and direct.get("operation_id") == task["id"]
                        ):
                            case_dir = aggregate_path.parent
                    candidates.append((case_dir / "result.json", row))
        # Merge current per-case facts even when a stale or partial aggregate
        # exists. Later direct facts take precedence over aggregate rows.
        candidates.extend((path, _read(path)) for path in root.rglob("result.json"))
    rows = {}
    for path, row in candidates:
        case = row.get("target")
        if case not in targets or row.get("operation_id") != task["id"]:
            continue
        if (
            target not in {"all", "failed"}
            and case != target
            and not case.startswith(target + ".")
        ):
            continue
        try:
            verdict = Verdict(row["verdict"])
        except (KeyError, ValueError):
            continue
        if not path.resolve().is_relative_to(root.resolve()):
            continue
        rows[case] = (verdict, row.get("reason"), path.parent)
    if not rows:
        # A no-op failed rerun is a successful operation, not a passing case.
        if target == "failed" and terminal == "SUCCEEDED":
            return terminal, reason
        reason = "本次没有生成可归因到本次执行的回归结果"
        store.put_result(
            environment["product_id"],
            environment["id"],
            target,
            profile,
            "ERROR",
            reason,
            str(directory),
        )
        return ("FAILED" if terminal == "SUCCEEDED" else terminal), reason
    for case, (verdict, case_reason, artifact) in rows.items():
        store.put_result(
            environment["product_id"],
            environment["id"],
            case,
            profile,
            verdict.value,
            case_reason,
            str(artifact),
        )
    if any(verdict in {Verdict.FAIL, Verdict.ERROR} for verdict, _, _ in rows.values()):
        terminal = "FAILED" if terminal == "SUCCEEDED" else terminal
    if target in rows:
        reason = rows[target][1] or reason
    return terminal, reason
