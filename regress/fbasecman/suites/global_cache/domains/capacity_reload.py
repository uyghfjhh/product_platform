"""Capacity reload scenarios and checks."""

from pathlib import Path

from framework.evidence.assertions import stats_delta
from framework.configuration.reload import has_config_value, install_reload_config, record_config_transition
from suites.global_cache.domains.capacity import (
    ps_limit_conf_keys,
    ps_limit_replacements,
    ps_limit_required_items,
    seed_capacity_entries,
)
from suites.global_cache.manifest import BACKEND_PS_LIMIT_KEY, GLOBAL_PS_LIMIT_KEY
from suites.global_cache.result import set_report_blocks as _summary_set_report_blocks
from suites.global_cache.errors import GlobalCacheFailure
from suites.global_cache.waits import wait_capacity_entries_unref


def run_capacity_reload_shrink_case(rt):
    before_limit = int(rt.case.reload.get("before_limit", 10))
    after_limit = int(rt.case.reload.get("after_limit", 5))
    seed_count = int(rt.case.reload.get("seed_count", before_limit))
    server_lifetime = int(rt.case.reload.get("server_lifetime", 10))
    wait_timeout = int(rt.case.reload.get("wait_timeout", 30))
    prefix = "gc_capacity_shrink"
    start_conf = rt.render_runtime_conf(
        ps_limit_replacements(before_limit)
        + [
            ('server_lifetime  3600', 'server_lifetime  %s' % server_lifetime),
            ('server_lifetime 3600', 'server_lifetime %s' % server_lifetime),
        ],
        stem="capacity_reload_live.conf",
    )
    start_conf_text = start_conf.read_text(encoding="utf-8", errors="replace")
    rt.start_fbasecman(conf=start_conf)
    before_state = rt.capture_console_state("before")
    rt.summary["before_stats"] = before_state["stats"]
    seed_capacity_entries(rt, seed_count, prefix)
    before_reload_state = wait_capacity_entries_unref(
        rt, prefix, seed_count, wait_timeout
    )
    reload_conf = rt.render_runtime_conf(
        ps_limit_replacements(after_limit)
        + [
            ('server_lifetime  3600', 'server_lifetime  %s' % server_lifetime),
            ('server_lifetime 3600', 'server_lifetime %s' % server_lifetime),
        ],
        stem="capacity_reload_shrink.conf",
    )
    reload_conf_text = reload_conf.read_text(encoding="utf-8", errors="replace")
    rt.summary.update({
        "capacity_reload_live_conf": str(start_conf),
        "capacity_reload_shrink_conf": str(reload_conf),
        "capacity_before_limit": before_limit,
        "capacity_after_limit": after_limit,
        "capacity_server_lifetime": server_lifetime,
        "capacity_reload_live_conf_text": start_conf_text,
        "capacity_reload_shrink_conf_text": reload_conf_text,
        "capacity_before_reload_stats": dict(before_reload_state["stats"]),
    })
    record_config_transition(
        rt,
        "初始运行配置: global_prepared_statements_limit %s, server_lifetime %s"
        % (before_limit, server_lifetime),
        "修改运行中配置: global_prepared_statements_limit %s -> %s"
        % (before_limit, after_limit),
        start_conf,
        start_conf_text,
        reload_conf,
        reload_conf_text,
        [
            (GLOBAL_PS_LIMIT_KEY, before_limit, after_limit),
            (BACKEND_PS_LIMIT_KEY, before_limit, after_limit),
            ("server_lifetime", server_lifetime),
        ],
    )
    live_conf_text = install_reload_config(
        reload_conf,
        start_conf,
        ps_limit_required_items(after_limit) + [("server_lifetime", server_lifetime)],
    )
    rt.summary["capacity_reload_live_conf_after_copy_text"] = live_conf_text
    rt.console_reload()
    rt.summary["capacity_before_reload_entries"] = [
        "|".join(row) for row in before_reload_state["global"]
        if len(row) >= 2 and ("%s_" % prefix) in row[1]
    ]
def assert_capacity_reload_shrink(rt, before_state, after_state):
    before_limit = int(rt.summary.get("capacity_before_limit", rt.case.reload.get("before_limit", 10)))
    after_limit = int(rt.summary.get("capacity_after_limit", rt.case.reload.get("after_limit", 5)))
    seed_count = int(rt.case.reload.get("seed_count", before_limit))
    live_conf = rt.summary.get("capacity_reload_live_conf", "")
    conf_text = (
        Path(live_conf).read_text(encoding="utf-8", errors="replace")
        if live_conf and Path(live_conf).exists() else ""
    )
    if not all(has_config_value(conf_text, key, after_limit) for key in ps_limit_conf_keys()):
        raise GlobalCacheFailure(
            "capacity shrink case did not materialize %s/%s=%s into runtime conf"
            % (GLOBAL_PS_LIMIT_KEY, BACKEND_PS_LIMIT_KEY, after_limit)
        )
    before_stats = rt.summary.get("capacity_before_reload_stats", {})
    before_capacity = int(before_stats.get("capacity", "0") or "0")
    before_total = int(before_stats.get("total_entries", "0") or "0")
    if before_capacity != before_limit or before_total != seed_count:
        raise GlobalCacheFailure(
            "capacity shrink precondition mismatch: capacity=%s/%s total=%s/%s"
            % (before_capacity, before_limit, before_total, seed_count)
        )
    before_entries = rt.summary.get("capacity_before_reload_entries", [])
    unref_entries = rt.summary.get("capacity_unref_entries", [])
    if len(unref_entries) < seed_count:
        raise GlobalCacheFailure(
            "capacity shrink expects %s seeded entries to reach ref_count=0 before reload, got %s"
            % (seed_count, len(unref_entries))
        )
    after_capacity = int(after_state["stats"].get("capacity", "0"))
    after_total = int(after_state["stats"].get("total_entries", "0"))
    if after_capacity != after_limit or after_total != after_limit:
        raise GlobalCacheFailure(
            "capacity shrink expects capacity/total=%s, got %s/%s"
            % (after_limit, after_capacity, after_total)
        )
    matched = [
        "|".join(row) for row in after_state["global"]
        if len(row) >= 2 and "gc_capacity_shrink_" in row[1]
    ]
    if len(matched) != after_limit:
        raise GlobalCacheFailure(
            "capacity shrink expects %s surviving seeded entries, got %s"
            % (after_limit, len(matched))
        )
    rt.summary["core_result"] = {
        "capacity_before_reload": before_capacity,
        "capacity_after": after_capacity,
        "total_before_reload": before_total,
        "total_after": after_total,
        "entries_before_reload": list(before_entries),
        "matched_entries": list(matched),
        "runtime_conf": live_conf,
    }
    rt.summary["stats_delta"] = stats_delta(before_state["stats"], after_state["stats"])
    rt.summary["matched_global"] = matched
    _summary_set_report_blocks(
        rt,
        verification_checks=[
            {"title": "shrink 前 seeded entries 已全部进入 unref 状态", "expected": "reload 前 seeded entries 全部 ref_count=0", "actual": "%s 条 seeded entry 已观测到 ref_count=0" % len(unref_entries), "result": "PASS"},
            {"title": "reload 后 capacity 收缩到新上限", "expected": "capacity = %s" % after_limit, "actual": "capacity=%s" % after_capacity, "console_stats": dict(after_state["stats"]), "result": "PASS"},
            {"title": "reload 后全局缓存条目同步收缩", "expected": "total_entries = %s" % after_limit, "actual": "total_entries=%s" % after_total, "console_stats": dict(after_state["stats"]), "result": "PASS"},
            {"title": "缩容后仍保留可观测的 surviving entries", "expected": "console 中保留 %s 条 gc_capacity_shrink_* entry" % after_limit, "actual": "%s entries survive shrink" % len(matched), "result": "PASS"},
        ],
        business_summary=[],
        key_evidence=[],
    )
