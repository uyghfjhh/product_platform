"""读取 fbasecman 回归产物；原始内容只做显示，不从文本猜测成功。"""

import re
from pathlib import Path

from platform_app.config import Settings

from products.fbasecman.observations import (
    parse_group_members,
    parse_group_routing,
    parse_groups,
    parse_monitor_config,
    parse_node_monitor,
    parse_node_status,
    parse_nodes,
)
from products.fbasecman.reports.parser import parse_report

TARGET = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


def report_root(settings: Settings, environment_id: str | None) -> Path:
    if not environment_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", environment_id):
        raise ValueError("需要已登记的环境 ID")
    return settings.artifact_dir("fbasecman", environment_id)


def case_directory(settings: Settings, target: str, environment_id: str | None = None) -> Path:
    if not TARGET.fullmatch(target):
        raise ValueError("需要完整用例目标 suite.case")
    root = report_root(settings, environment_id)
    candidates = [p for p in root.glob("runs/*/cases/" + target) if p.is_dir()]
    if not candidates:
        raise FileNotFoundError("当前环境没有该用例报告")
    return max(candidates, key=lambda path: (path / "result.json").stat().st_mtime if (path / "result.json").is_file() else path.stat().st_mtime)


def case_artifacts(settings: Settings, target: str, environment_id: str | None = None) -> dict:
    from platform_regress.evidence.artifacts import ArtifactRepository
    directory = case_directory(settings, target, environment_id)
    config_dirs = [settings.product_regress_root("fbasecman")]
    if environment_id:
        config_dirs.insert(0, settings.data_dir / "profiles" / environment_id)
    return ArtifactRepository(directory).describe(target, parsed=lambda: parse_report(
        target, directory, config_dirs=config_dirs))


def recent_case_statuses(settings: Settings, environment_id: str | None = None) -> dict[str, dict]:
    if not environment_id:
        return {}
    root = report_root(settings, environment_id)
    import json
    found = {}
    for path in root.glob("runs/*/cases/*/result.json"):
        payload = json.loads(path.read_text())
        target = payload["target"]
        mtime = path.stat().st_mtime
        if target not in found or mtime > found[target]["modified_at"]:
            found[target] = {"status": payload["verdict"], "duration": str(payload.get("duration_seconds", "-")),
                             "has_report": (path.parent / "report.txt").is_file(), "modified_at": mtime}
    return found


class CaseProgressObserver:
    """Product observation rules; platform owns journal polling and events."""
    def __init__(self, settings, environment_id, target, started_at):
        from platform_app.artifact_progress import ArtifactProgressObserver
        self.observer = ArtifactProgressObserver(
            report_root(settings, environment_id) / "runs",
            target, started_at, parse_observations=self.parse_observations)

    @staticmethod
    def parse_observations(output):
        command = output.splitlines()[0] if output else ""
        for marker, parser in (
            ("SHOW NODE_STATUS;", parse_node_status),
            ("SHOW GROUP_ROUTING ", parse_group_routing),
            ("SHOW NODE_MONITOR", parse_node_monitor),
            ("SHOW MONITOR_CONFIG", parse_monitor_config),
            ("SHOW GROUP_MEMBERS", parse_group_members),
            ("SHOW NODES;", parse_nodes), ("SHOW GROUPS;", parse_groups),
        ):
            if marker in command:
                return parser(output)
        return []

    def poll(self, store, task_id):
        return self.observer.poll(store, task_id)


def case_log(settings: Settings, target: str, filename: str, *,
             environment_id: str | None = None, last_lines: int = 500) -> dict:
    from platform_regress.evidence.artifacts import ArtifactRepository
    result = ArtifactRepository(case_directory(settings, target, environment_id)).log(filename, last_lines=last_lines)
    result["target"] = target
    return result




def export_source_report(settings: Settings, environment_id: str, format_name: str) -> str:
    if format_name not in {"junit", "html"}:
        raise ValueError("未知报告格式")
    root = report_root(settings, environment_id) / "runs"
    if not root.is_dir():
        raise FileNotFoundError("当前环境尚无测试报告")
    import json

    from platform_regress.reporting.export import export_html, export_junit
    newest = {}
    for path in root.glob("*/cases/*/result.json"):
        item = json.loads(path.read_text())
        target = item["target"]
        if target not in newest or path.stat().st_mtime > newest[target][0]:
            newest[target] = (path.stat().st_mtime, item)
    if not newest:
        raise FileNotFoundError("当前环境尚无测试报告")
    rows = [item for _, item in newest.values()]
    return export_junit(rows) if format_name == "junit" else export_html(rows)
