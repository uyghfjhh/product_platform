"""Global-cache report document routing and evidence assembly."""

from pathlib import Path

from suites.global_cache.reports.documents import (
    _build_test_step_overview,
    _record_or_text,
    build_structured_report_document,
)


def _extract_key_log_lines(text, patterns):
    matched = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line and any(pattern.lower() in line.lower() for pattern in patterns):
            matched.append(line)
    return matched


def _summary_set_log_window(rt, text, patterns, title=None):
    key_lines = _extract_key_log_lines(text, patterns)
    rt.summary["key_log_window"] = {
        "title": title or "关键日志窗口",
        "patterns": list(patterns),
        "lines": key_lines,
    }
    return key_lines


def _summary_set_pg_log_verify(rt, before_count, after_count, paths):
    rt.summary["pg_log_verify"] = {
        "before_count": before_count,
        "after_count": after_count,
        "delta": after_count - before_count,
        "paths": list(paths),
    }
    return rt.summary["pg_log_verify"]


def _collect_capacity_shrink_failure_context(rt):
    context = {}
    live_conf = rt.summary.get("capacity_reload_live_conf")
    if live_conf and Path(live_conf).exists():
        context["runtime_conf"] = Path(live_conf).read_text(encoding="utf-8", errors="replace")
    reload_log = rt.logs_dir / "console_reload.log"
    if reload_log.exists():
        context["reload_log"] = reload_log.read_text(encoding="utf-8", errors="replace")
    wait_logs = {}
    for name in ("wait_console_ready_01.log", "wait_console_ready_02.log", "wait_console_ready_03.log"):
        path = rt.logs_dir / name
        if path.exists():
            wait_logs[name] = path.read_text(encoding="utf-8", errors="replace")
    if wait_logs:
        context["wait_logs"] = wait_logs
    if rt.fbasecman_log.exists():
        context["fbasecman_log_tail"] = "\n".join(
            rt.fbasecman_log.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        )
    rt.summary["shrink_failure_context"] = context
    return context
