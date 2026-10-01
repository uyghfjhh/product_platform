"""Render missing human-readable artifacts next to authoritative case results."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from platform_regress.persistence.atomic import atomic_write_text
from platform_regress.reporting.model import ReportDocument, ReportStep
from platform_regress.reporting.renderer import render_report

_STATUS_MAP = {
    "PASS": "PASS", "FAIL": "FAIL", "ERROR": "FAIL",
    "BLOCKED": "FAIL", "CANCELLED": "FAIL",
}


def _event_steps(output_dir: Path) -> list[ReportStep]:
    """Fold step/sql/command finished events into report steps."""
    path = output_dir / "events.jsonl"
    if not path.is_file():
        return []
    steps: list[ReportStep] = []
    order = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("kind") or ""
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if kind == "step.finished":
            order += 1
            details = payload.get("details") or {}
            steps.append(ReportStep(
                title=payload.get("title") or payload.get("step_key") or "",
                expected=details.get("expected"),
                actual=details.get("actual"),
                result=payload.get("status"),
                details=[(key, str(value)) for key, value in details.items()
                         if key not in ("expected", "actual")],
            ))
        elif kind == "sql.finished":
            order += 1
            evidence = _read_evidence(output_dir, payload.get("evidence"))
            sql_text = evidence.get("sql", "")
            rows = evidence.get("rows") or []
            steps.append(ReportStep(
                title="执行 SQL%s" % (
                    "（%s/%s）" % (payload.get("node"), evidence.get("database"))
                    if payload.get("node") else ""),
                execution=[{"label": "实际执行", "text": sql_text}],
                actual="%d 行%s" % (
                    len(rows),
                    "：%s" % _rows_preview(rows) if rows else ""),
                result="PASS",
                evidence=[{"label": "证据", "text": payload.get("evidence") or ""}],
            ))
        elif kind == "command.finished":
            order += 1
            evidence = _read_evidence(output_dir, payload.get("evidence"))
            steps.append(ReportStep(
                title="执行命令",
                actual="returncode=%s%s" % (
                    payload.get("returncode"),
                    "\n" + str(evidence.get("stdout") or "")[:600]
                    if evidence.get("stdout") else ""),
                result="PASS" if payload.get("returncode") == 0 else "FAIL",
                evidence=[{"label": "证据", "text": payload.get("evidence") or ""}],
            ))
    return steps


def _read_evidence(output_dir: Path, reference: str | None) -> dict[str, Any]:
    if not reference:
        return {}
    candidate = (output_dir / reference).resolve()
    if not candidate.is_relative_to(output_dir.resolve()) \
            or not candidate.is_file():
        return {}
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _rows_preview(rows: list, limit: int = 5) -> str:
    preview = []
    for row in rows[:limit]:
        preview.append("|".join(str(col) for col in row)
                       if isinstance(row, list) else str(row))
    text = "; ".join(preview)
    return text + (" ..." if len(rows) > limit else "")


def _event_bounds(output_dir: Path) -> tuple[str, str]:
    path = output_dir / "events.jsonl"
    started = finished = ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            stamp = str(event.get("recorded_at") or "")[:19].replace("T", " ")
            if not stamp:
                continue
            if not started:
                started = stamp
            finished = stamp
    except OSError:
        pass
    return started, finished


def _run_start_epoch(output_dir: Path) -> float:
    """Epoch seconds of this run's first event (0 when unreadable)."""
    path = output_dir / "events.jsonl"
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                stamp = str(event.get("recorded_at") or "")
                if stamp:
                    try:
                        return datetime.fromisoformat(stamp).timestamp()
                    except ValueError:
                        return 0.0
    except OSError:
        pass
    return 0.0


def _steps_payload(target: str, steps: list[ReportStep]) -> dict:
    return {
        "target": target,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "steps": [
            {
                "order": index, "title": step.title,
                "status": "finished", "result": step.result or "PASS",
                "expected": step.expected, "actual": step.actual,
                "critical": step.result not in (None, "PASS"),
                "execution": [item.get("text") for item in step.execution],
                "evidence": [item.get("text") for item in step.evidence],
            }
            for index, step in enumerate(steps, 1)
        ],
    }


def write_case_artifacts(context, result, environment: dict,
                         purpose: str = "") -> list[str]:
    """Write report sidecars in the canonical case directory; preserve product reports."""
    output_dir = Path(context.output_dir)
    if (output_dir / "report.txt").is_file():
        return []
    steps = _event_steps(output_dir)
    started, finished = _event_bounds(output_dir)
    status = _STATUS_MAP.get(result.verdict, "FAIL")
    document = ReportDocument(
        target=result.target, status=status,
        started_at=started or finished,
        finished_at=finished or started,
        purpose=purpose or result.target,
        pass_reason="各检查项全部通过" if result.verdict == "PASS" else None,
        failure_reason=result.reason if result.verdict != "PASS" else None,
        steps=steps)
    report_text = render_report(document)
    summary = {
        "target": result.target,
        "status": status,
        "reason": result.reason,
        "verdict": result.verdict,
        "execution_id": result.execution_id,
        "duration_seconds": result.duration_seconds,
        "started_at": started, "finished_at": finished,
    }
    files = {
        "report.txt": report_text,
        "summary.json": json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        "steps.json": json.dumps(_steps_payload(result.target, steps), ensure_ascii=False, indent=2) + "\n",
    }
    for name, content in files.items():
        atomic_write_text(output_dir / name, content)
    return list(files)
