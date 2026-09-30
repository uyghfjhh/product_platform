"""读取 fbasecman 回归产物；原始内容只做显示，不从文本猜测成功。"""

import re
from pathlib import Path

from platform_app.config import Settings

from products.fbasecman.deployment.profile import evidence_root
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
    if environment_id:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", environment_id):
            raise ValueError("环境 ID 无效")
        return evidence_root(settings, environment_id)
    return settings.product_regress_root("fbasecman")


def case_directory(settings: Settings, target: str, environment_id: str | None = None) -> Path:
    if not TARGET.fullmatch(target):
        raise ValueError("需要完整用例目标 suite.case")
    suite, name = target.split(".", 1)
    return report_root(settings, environment_id) / "output" / "runs" / suite / name


def case_artifacts(settings: Settings, target: str, environment_id: str | None = None) -> dict:
    from platform_regress.evidence.artifacts import ArtifactRepository
    directory = case_directory(settings, target, environment_id)
    config_dirs = [settings.product_regress_root("fbasecman")]
    if environment_id:
        config_dirs.insert(0, settings.data_dir / "profiles" / environment_id)
    return ArtifactRepository(directory).describe(target, parsed=lambda: parse_report(
        target, report_root(settings, environment_id), config_dirs=config_dirs))


def recent_case_statuses(settings: Settings, environment_id: str | None = None) -> dict[str, dict]:
    root = report_root(settings, environment_id) / "output" / "runs"
    from platform_regress.evidence.artifacts import scan_run_summaries
    found = scan_run_summaries(root)
    for report in root.glob("*/*/report.txt"):
        target = report.parent.parent.name + "." + report.parent.name
        if target in found:
            continue
        try:
            head = report.read_text(encoding="utf-8", errors="replace")[:400]
            match = re.search(r"(?m)^(?:结论|Status):\s*(PASS|FAIL|RUNNING)", head)
            if match:
                found[target] = {
                    "status": match.group(1),
                    "duration": "-",
                    "has_report": True,
                    "modified_at": report.stat().st_mtime,
                }
        except OSError:
            continue
    return found


class CaseProgressObserver:
    """Product observation rules; platform owns journal polling and events."""
    def __init__(self, settings, environment_id, target, started_at):
        from platform_app.artifact_progress import ArtifactProgressObserver
        self.observer = ArtifactProgressObserver(
            report_root(settings, environment_id) / "output" / "runs",
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
    root = report_root(settings, environment_id) / "output" / "runs"
    if not root.is_dir():
        raise FileNotFoundError("当前环境尚无测试报告")
    if format_name == "junit":
        from platform_regress.reporting.junit import export_junit_from_runs
        return export_junit_from_runs(root)
    from platform_regress.reporting.html import export_html_from_runs
    return export_html_from_runs(root)
