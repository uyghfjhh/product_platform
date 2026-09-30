"""Product-neutral views of archived regression reports."""

import json
from pathlib import Path

from fastapi import HTTPException


def report_file(base: Path, name: str) -> Path:
    candidate = (base / name).resolve()
    if not candidate.is_relative_to(base.resolve()):
        raise HTTPException(status_code=404, detail="报告文件不存在")
    return candidate


def describe_report(base: Path, payload: dict) -> dict:
    steps_path = report_file(base, "steps.json")
    steps = []
    warning = None
    if steps_path.is_file():
        try:
            journal = json.loads(steps_path.read_text(encoding="utf-8"))
            steps = journal.get("steps", []) if isinstance(journal, dict) else journal
            if not isinstance(steps, list) or any(
                not isinstance(step, dict) for step in steps
            ):
                raise ValueError("invalid steps")
            if (
                isinstance(journal, dict)
                and journal.get("target", payload["target"]) != payload["target"]
            ):
                raise ValueError("target mismatch")
        except (OSError, ValueError):
            steps = []
            warning = "步骤文件损坏，仍可查看执行结论和原始报告"
    text_path = report_file(base, "report.txt")
    text = (
        text_path.read_text(encoding="utf-8", errors="replace")
        if text_path.is_file()
        else None
    )
    return {
        "target": payload["target"],
        "execution_id": payload.get("execution_id"),
        "verdict": payload.get("verdict", "ERROR"),
        "reason": payload.get("reason"),
        "duration_seconds": payload.get("duration_seconds"),
        "cleanup": payload.get("cleanup"),
        "steps": steps,
        "report": text,
        "warning": warning,
    }
