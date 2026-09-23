"""读取 fbasecman 回归产物；原始内容只做显示，不从文本猜测成功。"""

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .config import Settings
from .fbasecman_profile import legacy_root
from .storage import Store


TARGET = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


def report_root(settings: Settings, environment_id: str | None) -> Path:
    if environment_id:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", environment_id):
            raise ValueError("环境 ID 无效")
        return legacy_root(settings, environment_id)
    return settings.fbasecman_regress_root


def case_directory(settings: Settings, target: str, environment_id: str | None = None) -> Path:
    if not TARGET.fullmatch(target):
        raise ValueError("需要完整用例目标 suite.case")
    suite, name = target.split(".", 1)
    return report_root(settings, environment_id) / "output" / "runs" / suite / name


def case_artifacts(settings: Settings, target: str, environment_id: str | None = None) -> dict:
    directory = case_directory(settings, target, environment_id)
    summary_path = directory / "summary.json"
    steps_path = directory / "steps.json"
    report_path = directory / "report.txt"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else None
    journal = json.loads(steps_path.read_text(encoding="utf-8")) if steps_path.is_file() else None
    logs = []
    if directory.is_dir():
        for path in directory.rglob("*.log"):
            if path.is_file() and len(path.relative_to(directory).parts) <= 3:
                logs.append({"name": path.relative_to(directory).as_posix(), "size": path.stat().st_size})
    parsed = None
    if report_path.is_file():
        # 旧报告解析器运行在独立进程，避免与另一个 framework 包冲突。
        script = (
            "import json,sys; from tools.web_reports import parse_report; "
            "print(json.dumps(parse_report(sys.argv[1],sys.argv[2]),ensure_ascii=False))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, target, str(report_root(settings, environment_id))],
            cwd=settings.fbasecman_regress_root,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode == 0:
            parsed = json.loads(result.stdout)
    return {
        "target": target,
        "available": directory.is_dir(),
        "summary": summary,
        "steps": journal.get("steps", []) if isinstance(journal, dict) else [],
        "report": report_path.read_text(encoding="utf-8", errors="replace") if report_path.is_file() else None,
        "parsed": parsed,
        "logs": sorted(logs, key=lambda item: item["name"]),
    }


def recent_case_statuses(settings: Settings, environment_id: str | None = None) -> dict[str, dict]:
    root = report_root(settings, environment_id) / "output" / "runs"
    found: dict[str, dict] = {}
    if not root.is_dir():
        return found
    for summary in root.glob("*/*/summary.json"):
        try:
            value = json.loads(summary.read_text(encoding="utf-8"))
            status = value.get("status")
            if status in {"PASS", "FAIL", "RUNNING"}:
                found[summary.parent.parent.name + "." + summary.parent.name] = {
                    "status": status, "modified_at": summary.stat().st_mtime,
                }
        except (OSError, ValueError):
            continue
    for report in root.glob("*/*/report.txt"):
        target = report.parent.parent.name + "." + report.parent.name
        if target in found:
            continue
        try:
            head = report.read_text(encoding="utf-8", errors="replace")[:400]
            match = re.search(r"(?m)^(?:结论|Status):\s*(PASS|FAIL|RUNNING)", head)
            if match:
                found[target] = {"status": match.group(1), "modified_at": report.stat().st_mtime}
        except OSError:
            continue
    return found


class CaseProgressObserver:
    """把当前运行写出的步骤日志转换为平台事件。"""

    def __init__(self, settings: Settings, environment_id: str, target: str,
                 started_at: float):
        self.root = report_root(settings, environment_id) / "output" / "runs"
        self.target = target
        self.started_at = started_at - 1
        self.seen: dict[tuple[str, int], str] = {}

    def poll(self, store: Store, task_id: str) -> None:
        if not self.root.is_dir():
            return
        for path in self.root.glob("*/*/steps.json"):
            case = path.parent.parent.name + "." + path.parent.name
            if self.target not in {"failed", case, case.split(".", 1)[0]}:
                continue
            try:
                if path.stat().st_mtime < self.started_at:
                    continue
                steps = json.loads(path.read_text(encoding="utf-8")).get("steps", [])
            except (OSError, ValueError, AttributeError):
                continue
            for index, step in enumerate(steps):
                key = (case, index)
                result = str(step.get("result") or step.get("status") or "RUNNING")
                previous = self.seen.get(key)
                if previous is None:
                    store.add_event(task_id, "step.started", {
                        "title": step.get("title") or f"步骤 {index + 1}",
                        "target": case, "step_index": index,
                        "expected": step.get("expected"),
                        "artifact": str(path),
                    })
                if result in {"PASS", "FAIL"} and previous != result:
                    store.add_event(task_id, "assertion.checked", {
                        "title": step.get("title") or f"步骤 {index + 1}",
                        "target": case, "step_index": index,
                        "expected": step.get("expected"),
                        "actual": step.get("actual"),
                        "result": result,
                        "artifact": str(path),
                    })
                self.seen[key] = result


def case_log(settings: Settings, target: str, filename: str, *,
             environment_id: str | None = None, last_lines: int = 500) -> dict:
    directory = case_directory(settings, target, environment_id).resolve()
    if not filename or Path(filename).is_absolute():
        raise ValueError("日志文件名无效")
    path = (directory / filename).resolve()
    if not path.is_relative_to(directory) or path.suffix != ".log" or not path.is_file():
        raise FileNotFoundError("日志文件不存在")
    from collections import deque

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = list(deque(handle, maxlen=last_lines))
    return {"target": target, "name": filename, "lines": [line.rstrip("\n") for line in lines]}


def sync_current_results(store: Store, settings: Settings, environment: dict,
                         target: str, started_at: str) -> int:
    """只同步本次更新的产物，避免把此前 PASS 当成本次结论。"""
    started = datetime.fromisoformat(started_at).timestamp() - 1
    count = 0
    for case, info in recent_case_statuses(settings, environment["id"]).items():
        if info["modified_at"] < started:
            continue
        if target not in {"failed", case, case.split(".", 1)[0]}:
            continue
        status = info["status"] if info["status"] in {"PASS", "FAIL"} else "ERROR"
        directory = case_directory(settings, case, environment["id"])
        reason = None
        summary = directory / "summary.json"
        if summary.is_file():
            try:
                reason = json.loads(summary.read_text(encoding="utf-8")).get("reason")
            except (OSError, ValueError):
                pass
        store.put_result(environment["product_id"], environment["id"], case,
                         "default", status, reason, str(directory))
        count += 1
    return count


def export_source_report(settings: Settings, environment_id: str, format_name: str) -> str:
    if format_name not in {"junit", "html"}:
        raise ValueError("未知报告格式")
    root = report_root(settings, environment_id) / "output" / "runs"
    if not root.is_dir():
        raise FileNotFoundError("当前环境尚无测试报告")
    if format_name == "junit":
        script = (
            "import sys; from pathlib import Path; "
            "from framework.reporting.junit import export_junit_from_runs; "
            "print(export_junit_from_runs(Path(sys.argv[1])))"
        )
    else:
        script = (
            "import sys; from pathlib import Path; "
            "from framework.reporting.html import export_html_from_runs; "
            "print(export_html_from_runs(Path(sys.argv[1])))"
        )
    process = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        cwd=settings.fbasecman_regress_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or "报告生成失败")
    return process.stdout
