"""故障分析包（bug bundle）导出。

把一次回归失败需要的全部现场打包成单个 zip，供 issue 附件或跨机器
分析：环境记录与 profile、平台归档结果与证据、legacy 报告树、运行报告
（report.html/junit.xml）、 flaky 历史与最近任务日志。  平台 API 与根
CLI ``pack`` 共用同一构建器，保证两种入口产物一致。
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

_MAX_FILE_BYTES = 5 * 1024 * 1024
_MAX_TOTAL_BYTES = 200 * 1024 * 1024


def _add_tree(archive: zipfile.ZipFile, root: Path, prefix: str,
              state: dict) -> None:
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_FILE_BYTES or state["bytes"] + size > _MAX_TOTAL_BYTES:
            state["skipped"] += 1
            continue
        state["bytes"] += size
        archive.write(path, prefix + "/" + str(path.relative_to(root)))


def _legacy_run_dirs(legacy_root: Path, target: str) -> list[Path]:
    """Locate ``runs/<suite>/<case>/`` directories matching a dotted target."""
    suite, _, case = target.partition(".")
    runs = legacy_root / "output" / "runs"
    if not suite or not case or not runs.is_dir():
        return []
    matched = [directory for directory in (runs / suite).glob(case + "*")
               if directory.is_dir()] if (runs / suite).is_dir() else []
    return matched


def build_bug_bundle(settings, store, environment_id: str,
                     target: str | None = None) -> bytes:
    """Build the bug-bundle zip payload for one environment (and target)."""
    environment = store.get_environment(environment_id)
    if environment is None:
        raise KeyError(environment_id)
    regression_root = settings.environment_dir / "regression" / environment_id
    legacy_root = settings.environment_dir / "legacy_cman" / environment_id
    profile_root = settings.environment_dir / "profiles" / environment_id
    buffer = io.BytesIO()
    state = {"bytes": 0, "skipped": 0}
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "environment": environment,
            "target": target,
        }
        archive.writestr("bundle.json",
                         json.dumps(manifest, ensure_ascii=False, indent=2))
        environment_file = (settings.data_dir / "environments"
                            / (environment_id + ".yaml"))
        if environment_file.is_file():
            archive.write(environment_file, "environment.yaml")
        _add_tree(archive, profile_root, "profile", state)
        if target:
            _add_tree(archive, regression_root / target,
                      "regression/" + target, state)
            for index, directory in enumerate(
                    _legacy_run_dirs(legacy_root, target)):
                _add_tree(archive, directory,
                          "legacy/runs/%d" % index, state)
        else:
            _add_tree(archive, regression_root, "regression", state)
            _add_tree(archive, legacy_root / "output" / "runs",
                      "legacy/runs", state)
        for name in ("report.html", "junit.xml", "last_failed.json",
                     "history.jsonl", "suite-result.json"):
            candidate = regression_root / name
            if candidate.is_file():
                archive.write(candidate, "reports/" + name)
        history = _read_history(regression_root / "history.jsonl", target)
        if history:
            archive.writestr("reports/case-history.json",
                             json.dumps(history, ensure_ascii=False, indent=2))
        # Recent task events involving this environment help diagnose queue
        # or lifecycle issues without access to the live store.
        events = []
        for task in store.list_tasks()[-50:]:
            if task.get("environment_id") != environment_id:
                continue
            events.append({"task": task.get("id"),
                           "action": task.get("action"),
                           "status": task.get("status")})
        archive.writestr("reports/tasks.json",
                         json.dumps(events, ensure_ascii=False, indent=2))
    return buffer.getvalue()


def _read_history(path: Path, target: str | None) -> dict:
    """Aggregate the flaky-tracking history lines into per-target stats."""
    stats: dict = {}
    if not path.is_file():
        return stats
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return stats
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        case = entry.get("target")
        if not case or (target and case != target):
            continue
        record = stats.setdefault(case, {"runs": [], "pass": 0, "fail": 0,
                                         "error": 0, "other": 0})
        verdict = str(entry.get("verdict") or "").upper()
        record["runs"].append(entry)
        key = {"PASS": "pass", "FAIL": "fail", "ERROR": "error"}.get(
            verdict, "other")
        record[key] += 1
    for record in stats.values():
        record["runs"] = record["runs"][-20:]
    return stats


def flaky_summary(settings, environment_id: str,
                  window: int = 5) -> dict:
    """Recent per-target verdict history for flaky detection (last N runs)."""
    path = (settings.environment_dir / "regression" / environment_id
            / "history.jsonl")
    stats = _read_history(path, None)
    summary = {}
    for case, record in stats.items():
        recent = record["runs"][-window:]
        verdicts = [str(item.get("verdict") or "").upper() for item in recent]
        summary[case] = {
            "runs": len(record["runs"]),
            "recent": verdicts,
            "pass": record["pass"],
            "fail": record["fail"],
            "error": record["error"],
            "flaky": len(set(verdicts)) > 1,
        }
    return summary
