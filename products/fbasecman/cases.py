"""fbasecman regression targets registered with the platform engine."""

import json
import sys
from pathlib import Path

from platform_regress import Blocked
from products.fbasecman.native import (HeartbeatBindCase, SavepointRecoveryCase,
                                        SqlParseExtendedProtocolCase,
                                        JdbcConsoleHaCommandsCase)
from products.fbasecman.native import SetNodeWriteIdempotentCase


PRODUCT_ROOT = Path(__file__).parent
CATALOG = json.loads((PRODUCT_ROOT / "regression" / "catalog.json").read_text(encoding="utf-8"))
if CATALOG.get("schema_version") != 1:
    raise ValueError("fbasecman 用例目录版本无效")
CASE_METADATA = CATALOG["cases"]


class LegacyCmanCase:
    """Temporary product executor with platform-owned verdict publication.

    The old runtime still owns fixtures and business checks. A fresh summary
    for this exact target is mandatory; command exit code alone is insufficient.
    """

    def __init__(self, target):
        self.target = target

    def run(self, context):
        source = Path(context.environment.get("legacy_source") or
                      PRODUCT_ROOT / "regression" / "legacy").resolve()
        override_value = context.environment.get("legacy_override")
        report_value = context.environment.get("legacy_report_root")
        if not override_value or not report_value:
            raise Blocked("缺少当前环境的 fbasecman 测试配置或报告目录")
        override = Path(override_value).resolve()
        report_root = Path(report_value).resolve()
        if not (source / "suites" / "registry.py").is_file() or not override.is_file():
            raise Blocked("fbasecman 用例来源或环境覆盖配置不存在")
        suite, name = self.target.split(".", 1)
        summary_path = report_root / "output" / "runs" / suite / name / "summary.json"
        previous_mtime = summary_path.stat().st_mtime_ns if summary_path.is_file() else -1
        command = [
            sys.executable, str(PRODUCT_ROOT / "regression" / "run.py"),
            "--source", str(source), "--override", str(override), self.target,
        ]
        result = context.command(command, cwd=source, timeout_seconds=7200)
        try:
            stat = summary_path.stat()
            if stat.st_mtime_ns <= previous_mtime:
                raise RuntimeError("本次未更新 fbasecman 用例报告")
            summary_text = summary_path.read_text(encoding="utf-8")
            summary = json.loads(summary_text)
        except (OSError, ValueError) as exc:
            raise RuntimeError("本次未生成可核对的 fbasecman 用例报告") from exc
        status = summary.get("status")
        if status not in {"PASS", "FAIL"}:
            raise RuntimeError(f"fbasecman 报告状态无效: {status}")
        context.attach_text("legacy-summary.json", summary_text)
        case_directory = summary_path.parent.resolve()
        report = case_directory / "report.txt"
        if not report.is_file():
            raise RuntimeError("本次旧用例缺少文本报告")
        context.attach_file("legacy-report.txt", report)
        for index, source in enumerate(sorted(case_directory.rglob("*.log")), 1):
            resolved = source.resolve()
            if not resolved.is_relative_to(case_directory) or not resolved.is_file():
                raise RuntimeError(f"旧用例日志路径无效: {source}")
            context.attach_file(f"legacy-log-{index}.log", resolved)
        context.step("legacy-verdict", "核对旧用例原始判定", status=status,
                     details={"legacy_status": status})
        if status == "PASS" and result.returncode == 0:
            return True
        if status == "FAIL" and result.returncode != 0:
            raise AssertionError(summary.get("reason") or "旧用例业务断言失败")
        raise RuntimeError(f"旧用例结果与退出码不一致: {status}/{result.returncode}")


NATIVE_CASES = {
    "sql_parse.savepoint_recovery_after_local_25p02": SavepointRecoveryCase(),
    "sql_parse.heartbeat_bind_normal": HeartbeatBindCase("normal"),
    "sql_parse.heartbeat_bind_invalid": HeartbeatBindCase("malformed"),
    "sql_parse.heartbeat_bind_unsupported": HeartbeatBindCase("binary"),
    "ha_commands.sql_parse_extended_protocol": SqlParseExtendedProtocolCase(),
    "ha_commands.jdbc_console_ha_commands": JdbcConsoleHaCommandsCase(),
    "ha_commands.set_node_write_idempotent": SetNodeWriteIdempotentCase(),
}
CASES = {item["target"]: NATIVE_CASES.get(
    item["target"], LegacyCmanCase(item["target"])) for item in CASE_METADATA}
