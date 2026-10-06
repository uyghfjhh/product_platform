"""Render missing human-readable artifacts next to authoritative case results."""

from __future__ import annotations

import json
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any

from platform_regress.commands import display_command
from platform_regress.persistence.atomic import atomic_write_text
from platform_regress.reporting.model import ReportDocument, ReportStep
from platform_regress.reporting.renderer import render_report

_STATUS_MAP = {
    "PASS": "PASS", "FAIL": "FAIL", "ERROR": "FAIL",
    "BLOCKED": "FAIL", "CANCELLED": "FAIL",
}


_UNWRAPPABLE = {"sh", "bash", "dash", "ksh", "zsh", "ssh", "env", "script"}


def _displayable_command(text: str) -> str:
    """Re-render archived ``command`` text for display: unwrap shell/psql quoting."""
    stripped = (text or "").strip()
    if not stripped:
        return stripped
    try:
        argv = shlex.split(stripped)
    except ValueError:
        return text
    if not argv:
        return text
    name = Path(argv[0]).name
    if name in _UNWRAPPABLE or name == "psql":
        return display_command(argv)
    return text


def _event_steps(output_dir: Path) -> list[ReportStep]:
    """Fold step/sql/command finished events into report steps."""
    path = output_dir / "events.jsonl"
    if not path.is_file():
        return []
    semantic: list[ReportStep] = []
    transport: list[ReportStep] = []
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
            details = payload.get("details") or {}
            evidence = details.get("evidence")
            if not details.get("analysis") and isinstance(details.get("required"), (list, tuple)):
                required = details["required"]
                output = str(details.get("output") or "")
                missing = [str(marker) for marker in required if str(marker) not in output]
                details = {**details, "analysis": "根据本次保存的输出核对声明标记：" +
                           ("缺少 " + ", ".join(missing) if missing else "全部标记均存在") +
                           "；端口归属及事务恢复细节以客户端程序和原始证据为准"}
            labels = {
                "analysis": "结果分析", "node": "执行节点", "example_code": "关键代码",
                "action": "执行动作", "reason": "原因",
            }
            semantic.append(ReportStep(
                title=payload.get("title") or payload.get("step_key") or "",
                intent=details.get("intent"),
                expected=details.get("expected") if details.get("expected") is not None else
                         ("程序输出必须同时包含以下标记：\n" + "\n".join(map(str, details["required"]))
                          if isinstance(details.get("required"), (list, tuple)) else None),
                actual=details.get("output") or details.get("actual"),
                result=payload.get("status"),
                assertion=details.get("assertion"),
                raw_output=details.get("output"),
                actual_summary=details.get("actual"),
                execution=([{"label": "执行内容", "text": _displayable_command(details["command"])}]
                           if details.get("command") else []),
                evidence=([{"label": "证据", "text": evidence}]
                          if evidence else []),
                details=[(labels.get(key, key), json.dumps(value, ensure_ascii=False)
                          if isinstance(value, (dict, list)) else str(value))
                         for key, value in details.items()
                         if key not in ("expected", "actual", "command", "evidence",
                                        "assertion", "output")
                         and value not in (None, "")],
            ))
        elif kind == "sql.finished":
            evidence = _read_evidence(output_dir, payload.get("evidence"))
            sql_text = evidence.get("sql", "")
            rows = evidence.get("rows") or []
            transport.append(ReportStep(
                intent="action",
                expected="SQL 执行成功；该操作不包含业务结果断言",
                details=[("结果分析", "只记录查询执行及返回值，业务验证以独立断言为准")],
                title="执行 SQL%s" % (
                    "（%s/%s）" % (payload.get("node"), evidence.get("database"))
                    if payload.get("node") else ""),
                execution=[{"label": "执行内容", "text": sql_text}],
                actual="%d 行%s" % (
                    len(rows), "：%s" % _rows_preview(rows) if rows else ""),
                result="PASS",
                evidence=[{"label": "证据", "text": payload.get("evidence") or ""}],
            ))
        elif kind == "command.finished":
            evidence = _read_evidence(output_dir, payload.get("evidence"))
            transport.append(ReportStep(
                intent="action",
                expected="命令退出码为 0；该操作不包含业务结果断言",
                details=[("结果分析", "退出码=%s；业务验证以独立断言为准" % payload.get("returncode"))],
                title="执行命令",
                execution=([{"label": "执行内容", "text": evidence["command"]}]
                           if evidence.get("command") else []),
                actual="returncode=%s%s" % (
                    payload.get("returncode"),
                    "\n" + str(evidence.get("stdout") or "")[:600]
                    if evidence.get("stdout") else ""),
                result="PASS" if payload.get("returncode") == 0 else "FAIL",
                evidence=[{"label": "证据", "text": payload.get("evidence") or ""}],
            ))
    return semantic or transport


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
                "order": index, "title": step.title,"intent":step.intent,
                "status": "finished", "result": step.result or "UNKNOWN",
                "expected": step.expected, "actual": step.actual_summary if step.actual_summary is not None else step.actual,
                "critical": step.result not in (None, "PASS"),
                "command": next((item.get("text") for item in step.execution
                                 if item.get("label") in ("执行内容", "实际执行")), None),
                "execution": [item.get("text") for item in step.execution],
                "evidence": [item.get("text") for item in step.evidence],
                "assertion": step.assertion,
                "analysis": next((value for label, value in step.details
                                  if label in ("结果分析", "判定依据")), None),
                "node": next((value for label, value in step.details
                              if label == "执行节点"), None),
                "output": step.raw_output,
                "example_code": next((value for label, value in step.details if label == "关键代码"), None),
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
    from .description import read_description
    description=read_description(output_dir,context.execution_id)
    steps = _event_steps(output_dir)
    started, finished = _event_bounds(output_dir)
    status = _STATUS_MAP.get(result.verdict, "FAIL")
    document = ReportDocument(
        target=result.target, status=status,
        started_at=started or finished,
        finished_at=finished or started,
        purpose=(description or {}).get('purpose') or purpose or result.target,
        overview_steps=[s['title']+'：'+str(s.get('expected') or '未声明期望') for s in (description or {}).get('steps',[])],
        pass_reason=((description or {}).get('final_state') or '各步骤已满足其声明期望，具体行为见逐步证据'
                     if result.verdict == "PASS" and steps else
                     "业务断言全部通过" if result.verdict == "PASS" else None),
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
