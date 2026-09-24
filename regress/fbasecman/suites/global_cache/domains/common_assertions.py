"""Extracted domain helpers for global_cache."""

import os
import re
import shutil
from copy import copy
from contextlib import contextmanager

from framework.evidence.assertions import stats_delta as _stats_delta
from framework.execution.phased_process import PhaseAction, observe_phases
from framework.evidence.log_checks import find_forbidden_log_patterns
from products.fbasecman.console import parse_pipe_rows
from framework.reporting import render_psql_table_from_pipe_text
from framework.configuration.reload import (
    config_lines_by_keys as _conf_lines_by_keys,
    install_reload_config as _install_reload_config,
    record_config_transition as _record_reload_conf_steps,
)
from suites.global_cache.runtime import (
    CaseRuntime,
    GlobalCacheFailure,
    VerificationFailure,
    _find_case,
    _load_env,
    _safe_name,
    _validate_report_levels,
    case_items,
    show,
)
from products.fbasecman.config import extract_config_lines as _extract_conf_lines
from suites.global_cache.reporting import (
    _collect_capacity_shrink_failure_context,
    _summary_set_log_window,
    _summary_set_pg_log_verify,
)
from suites.global_cache.result import set_report_blocks as _summary_set_report_blocks
from suites.global_cache.manifest import (
    BACKEND_PS_LIMIT_KEY,
    GLOBAL_PS_LIMIT_KEY,
    NEGATIVE_LOG_PATTERNS,
    NOISE_PATTERNS,
    formal_case_items,
)
from suites.global_cache.drivers import (
    compile_java as _compile_java,
    build_libpq_asset as _build_libpq_asset,
    jdbc_url as _jdbc_url,
    libpq_source as _driver_libpq_source,
    stage_libpq_source as _driver_stage_libpq_source,
    start_phased_libpq as _start_phased_libpq,
    run_libpq_asset as _run_libpq_asset,
    run_jdbc_asset as _run_jdbc_asset,
    run_jdbc_asset_phased as _run_jdbc_asset_phased,
    jdbc_source_file as _driver_jdbc_source_file,
    run_case_jdbc as _driver_run_case_jdbc,
    record_driver_api_calls as _record_driver_api_calls,
    libpq_prepared_operations as _libpq_prepared_operations,
)
from suites.global_cache.paths import asset_path as _global_cache_asset_path

def _assert_negative_logs(rt, report_check=False):
    paths = sorted(rt.logs_dir.glob("*.log")) + [rt.fbasecman_log]
    found = find_forbidden_log_patterns(paths, NEGATIVE_LOG_PATTERNS)
    if found:
        rendered = ", ".join("%s in %s" % (item["pattern"], item["path"]) for item in found)
        raise GlobalCacheFailure("negative log patterns found: %s" % rendered)
    rt.summary.setdefault("framework_checks", {})["negative_logs"] = {
        "checked_patterns": list(NEGATIVE_LOG_PATTERNS),
        "status": "PASS",
    }
    if report_check:
        rt.summary["verification_checks"] = [
            {
                "title": "负向日志模式未出现",
                "expected": "不出现已知 crash/stale/outstanding 等负向关键字",
                "actual": "checked=%s" % ", ".join(NEGATIVE_LOG_PATTERNS),
                "result": "PASS",
            }
        ]


def _assert_fbasecman_no_warning_or_error(rt):
    if not rt.fbasecman_log.exists():
        rt.summary["fbasecman_log_level_check"] = {
            "status": "missing",
            "checked_levels": ["warning", "error"],
            "log": str(rt.fbasecman_log),
        }
        return

    allowed = [pattern.lower() for pattern in rt.case.allowed_fbasecman_log_patterns]
    matched = []
    level_pattern = re.compile(
        r"^\s*\d+\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+(debug\d*|debug|info|warning|error)\b",
        re.IGNORECASE,
    )
    for raw_line in rt.fbasecman_log.read_text(encoding="utf-8", errors="replace").splitlines():
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
        rt.summary["fbasecman_log_level_check"] = {
            "status": "non_clean",
            "checked_levels": ["warning", "error"],
            "matched": matched[:20],
        }
        return

    rt.summary["fbasecman_log_level_check"] = {
        "status": "clean",
        "checked_levels": ["warning", "error"],
    }


def _assert_verification_checks_clean(rt):
    checks = rt.summary.get("verification_checks", [])
    failed = [item for item in checks if str(item.get("result", "PASS")).upper() != "PASS"]
    if failed:
        check = failed[0]
        rt.record_step(
            "验证: %s" % check.get("title", "<unknown>"),
            output=check.get("evidence") or check.get("actual", ""),
            expected=check.get("expected", "<missing>"),
            actual=check.get("actual", "<missing>"),
            result="FAIL",
            phase=check.get("phase"),
        )
        rt.summary["failed_step"] = dict(rt.step_records[-1])
        rt.summary["failed_check"] = dict(check)
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


