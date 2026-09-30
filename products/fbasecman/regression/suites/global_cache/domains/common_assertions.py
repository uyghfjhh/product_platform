"""Extracted domain helpers for global_cache."""

import re

from platform_regress.evidence.log_checks import find_forbidden_log_patterns
from suites.global_cache.runtime import (
    GlobalCacheFailure,
    VerificationFailure,
)
from suites.global_cache.manifest import (
    NEGATIVE_LOG_PATTERNS,
)
def _assert_negative_logs(context, report_check=False):
    ops = context.ops
    paths = sorted(ops.logs_dir.glob("*.log")) + [ops.fbasecman_log]
    found = find_forbidden_log_patterns(paths, NEGATIVE_LOG_PATTERNS)
    if found:
        rendered = ", ".join("%s in %s" % (item["pattern"], item["path"]) for item in found)
        raise GlobalCacheFailure("negative log patterns found: %s" % rendered)
    ops.summary.setdefault("framework_checks", {})["negative_logs"] = {
        "checked_patterns": list(NEGATIVE_LOG_PATTERNS),
        "status": "PASS",
    }
    if report_check:
        ops.summary["verification_checks"] = [
            {
                "title": "负向日志模式未出现",
                "expected": "不出现已知 crash/stale/outstanding 等负向关键字",
                "actual": "checked=%s" % ", ".join(NEGATIVE_LOG_PATTERNS),
                "result": "PASS",
            }
        ]


def _assert_fbasecman_no_warning_or_error(context):
    ops = context.ops
    if not ops.fbasecman_log.exists():
        ops.summary["fbasecman_log_level_check"] = {
            "status": "missing",
            "checked_levels": ["warning", "error"],
            "log": str(ops.fbasecman_log),
        }
        return

    allowed = [pattern.lower() for pattern in ops.case.allowed_fbasecman_log_patterns]
    matched = []
    level_pattern = re.compile(
        r"^\s*\d+\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+(debug\d*|debug|info|warning|error)\b",
        re.IGNORECASE,
    )
    for raw_line in ops.fbasecman_log.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = level_pattern.match(line)
        if not m:
            continue
        level = m.group(1).lower()
        if level not in ("warning", "error"):
            continue
        lower_line = line.lower()
        if any(token in lower_line for token in allowed):
            continue
        matched.append(line)

    if matched:
        ops.summary["fbasecman_log_level_check"] = {
            "status": "non_clean",
            "checked_levels": ["warning", "error"],
            "matched": matched[:20],
        }
        return

    ops.summary["fbasecman_log_level_check"] = {
        "status": "clean",
        "checked_levels": ["warning", "error"],
    }


def _assert_verification_checks_clean(context):
    ops = context.ops
    checks = ops.summary.get("verification_checks", [])
    failed = [item for item in checks if str(item.get("result", "PASS")).upper() != "PASS"]
    if failed:
        check = failed[0]
        ops.record_step(
            "验证: %s" % check.get("title", "<unknown>"),
            output=check.get("evidence") or check.get("actual", ""),
            expected=check.get("expected", "<missing>"),
            actual=check.get("actual", "<missing>"),
            result="FAIL",
            phase=check.get("phase"),
        )
        ops.summary["failed_step"] = dict(ops.step_records[-1])
        ops.summary["failed_check"] = dict(check)
        raise VerificationFailure(check)


def _stats_change_text(delta, keys=None):
    labels = {
        "hits": "cache hit",
        "misses": "cache miss",
        "evictions": "淘汰",
        "total_entries": "总条目",
        "referenced_entries": "有引用条目",
        "unreferenced_entries": "无引用条目",
        "bypass_entries": "bypass 条目",
        "capacity": "容量",
    }
    selected = keys or (
        "misses", "hits", "evictions", "total_entries",
        "referenced_entries", "unreferenced_entries", "bypass_entries", "capacity",
    )
    observations = []
    for key in selected:
        value = delta.get(key, 0)
        if not isinstance(value, int) or value == 0:
            continue
        direction = "增加" if value > 0 else "减少"
        unit = " 次" if key in ("hits", "misses", "evictions") else " 条"
        if key == "capacity":
            unit = ""
        observations.append(
            "%s %s %s%s" % (labels.get(key, key), direction, abs(value), unit)
        )
    return "；".join(observations) if observations else "相关缓存统计没有发生变化"


