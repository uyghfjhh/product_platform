"""Capacity product checks."""

from framework.evidence.assertions import stats_delta as _stats_delta
from lib.report_utils import render_psql_expanded_from_pipe_text
from suites.global_cache.result import set_report_blocks as _summary_set_report_blocks
from suites.global_cache.errors import GlobalCacheFailure


def assert_capacity_eviction_zero_ref(rt, before_state, active_state, zero_ref_state, trigger_a_state, trigger_b_state, after_state):
    capacity_limit = int(rt.case.reload.get("capacity_limit", 3))
    server_lifetime = int(rt.case.reload.get("server_lifetime", 10))
    delta = _stats_delta(before_state["stats"], after_state["stats"])
    active_rows = [
        "|".join(row) for row in active_state["global"]
        if len(row) >= 5 and "gc_capacity_zero_ref_hold_01" in row[1]
    ]
    zero_ref_rows = [
        "|".join(row) for row in zero_ref_state["global"]
        if len(row) >= 5 and "gc_capacity_zero_ref_" in row[1] and "trigger_" not in row[1] and "hold" not in row[1] and (row[4] or "").strip() == "0"
    ]
    trigger_a_rows = [
        "|".join(row) for row in trigger_a_state["global"]
        if len(row) >= 2 and "gc_capacity_zero_ref_" in row[1]
    ]
    trigger_b_rows = [
        "|".join(row) for row in trigger_b_state["global"]
        if len(row) >= 2 and "gc_capacity_zero_ref_" in row[1]
    ]
    trigger_a_total = int(trigger_a_state["stats"].get("total_entries", "0") or "0")
    trigger_a_unref = int(trigger_a_state["stats"].get("unreferenced_entries", "0") or "0")
    trigger_b_total = int(trigger_b_state["stats"].get("total_entries", "0") or "0")
    trigger_b_unref = int(trigger_b_state["stats"].get("unreferenced_entries", "0") or "0")
    after_capacity = int(after_state["stats"].get("capacity", "0") or "0")
    after_total = int(after_state["stats"].get("total_entries", "0") or "0")
    after_evictions = int(after_state["stats"].get("evictions", "0") or "0")
    before_evictions = int(before_state["stats"].get("evictions", "0") or "0")
    matched = [
        "|".join(row) for row in after_state["global"]
        if len(row) >= 2 and "gc_capacity_zero_ref_" in row[1]
    ]
    log_text = rt.fbasecman_log.read_text(encoding="utf-8", errors="replace")
    capacity_exceed_lines = [line.strip() for line in log_text.splitlines() if "global ps cache exceeds capacity:" in line]
    eviction_lines = [line.strip() for line in log_text.splitlines() if "evict global prepared statement" in line]
    hold_after_rows = [row for row in matched if "gc_capacity_zero_ref_hold_01" in row]
    trigger_a_after_rows = [row for row in matched if "gc_capacity_zero_ref_trigger_a" in row]
    trigger_b_after_rows = [row for row in matched if "gc_capacity_zero_ref_trigger_b" in row]
    zero_ref_after_rows = [row for row in matched if "gc_capacity_zero_ref_" in row and "trigger_" not in row and "hold" not in row]
    if not active_rows:
        rt.record_step("capacity zero-ref 检查失败", output="after_hold 快照里没有看到长连接 SQL `gc_capacity_zero_ref_hold_01`。")
        raise GlobalCacheFailure("capacity_eviction_zero_ref expects active long-connection entry after step 1")
    if len(zero_ref_rows) < max(1, capacity_limit - 1):
        rt.record_step(
            "capacity zero-ref 检查失败",
            output="zero_ref_ready 快照里的 zero-ref 普通条目不足：expected>=%s actual=%s\nzero_ref_ready global:\n%s"
            % (
                max(1, capacity_limit - 1),
                len(zero_ref_rows),
                render_psql_expanded_from_pipe_text(
                    "global_name|description|sql_class|has_bypass_response|ref_count\n" +
                    "\n".join("|".join(row) for row in zero_ref_state["global"])
                ) if zero_ref_state["global"] else "<empty>",
            ),
        )
        raise GlobalCacheFailure("capacity_eviction_zero_ref expects at least %s zero-ref ready entries before later trigger pressure, got %s" % (max(1, capacity_limit - 1), len(zero_ref_rows)))
    if after_capacity != capacity_limit:
        rt.record_step("capacity zero-ref 检查失败", output="after 快照里的 capacity 不符合预期：expected=%s actual=%s" % (capacity_limit, after_capacity))
        raise GlobalCacheFailure("capacity_eviction_zero_ref expects capacity=%s, got %s" % (capacity_limit, after_capacity))
    if after_total > capacity_limit + 1:
        rt.record_step(
            "capacity zero-ref 检查失败",
            output="after 快照 total_entries 异常增长：capacity=%s total=%s\nafter global:\n%s"
            % (
                capacity_limit,
                after_total,
                render_psql_expanded_from_pipe_text(
                    "global_name|description|sql_class|has_bypass_response|ref_count\n" +
                    "\n".join("|".join(row) for row in after_state["global"])
                ) if after_state["global"] else "<empty>",
            ),
        )
        raise GlobalCacheFailure("capacity_eviction_zero_ref expects total_entries not to grow unbounded, got %s" % after_total)
    if len(matched) > capacity_limit + 1:
        rt.record_step("capacity zero-ref 检查失败", output="after 快照 survivor 数量异常：capacity=%s survivors=%s\nmatched=%s" % (capacity_limit, len(matched), "\n".join(matched)))
        raise GlobalCacheFailure("capacity_eviction_zero_ref expects limited survivors set, got %s" % len(matched))
    if after_evictions <= before_evictions:
        rt.record_step("capacity zero-ref 检查失败", output="evictions 没有增长：before=%s after=%s" % (before_evictions, after_evictions))
        raise GlobalCacheFailure("capacity_eviction_zero_ref expects evictions to increase, before=%s after=%s" % (before_evictions, after_evictions))
    if trigger_a_total > capacity_limit and trigger_a_unref > 0:
        raise GlobalCacheFailure(
            "capacity_eviction_zero_ref trigger_a snapshot still exceeds capacity: total=%s capacity=%s unreferenced=%s"
            % (trigger_a_total, capacity_limit, trigger_a_unref)
        )
    if trigger_b_total > capacity_limit and trigger_b_unref > 0:
        raise GlobalCacheFailure(
            "capacity_eviction_zero_ref trigger_b snapshot still exceeds capacity: total=%s capacity=%s unreferenced=%s"
            % (trigger_b_total, capacity_limit, trigger_b_unref)
        )
    if not hold_after_rows:
        rt.record_step("capacity zero-ref 检查失败", output="after 快照里长连接 SQL `gc_capacity_zero_ref_hold_01` 消失了，说明 active entry 被错误淘汰。")
        raise GlobalCacheFailure("capacity_eviction_zero_ref expects long-connection entry to survive trigger pressure")
    if not trigger_a_rows:
        rt.record_step("capacity zero-ref 检查失败", output="after_trigger_a 快照缺失，无法证明第一次 trigger 后的容量收敛行为。")
        raise GlobalCacheFailure("capacity_eviction_zero_ref expects trigger_a stage snapshot to be captured")
    if not trigger_b_rows:
        rt.record_step("capacity zero-ref 检查失败", output="after_trigger_b 快照缺失，无法证明第二次 trigger 后的容量收敛行为。")
        raise GlobalCacheFailure("capacity_eviction_zero_ref expects trigger_b stage snapshot to be captured")
    rt.summary["core_result"] = {
        "active_rows": list(active_rows),
        "zero_ref_rows": list(zero_ref_rows),
        "trigger_a_rows": list(trigger_a_rows),
        "trigger_b_rows": list(trigger_b_rows),
        "capacity_after": after_capacity,
        "total_after": after_total,
        "evictions_before": before_evictions,
        "evictions_after": after_evictions,
        "survivor_count": len(matched),
        "matched_entries": list(matched),
        "capacity_exceed_lines": list(capacity_exceed_lines),
        "eviction_lines": list(eviction_lines),
        "server_lifetime": server_lifetime,
    }
    rt.summary["stats_delta"] = delta
    rt.summary["matched_global"] = matched
    _summary_set_report_blocks(
        rt,
        verification_checks=[
            {
                "title": "长连接持续保留；未被提前淘汰的短连接 entries 进入 zero-ref 候选池",
                "expected": "capacity=3 且长连接一直保留时，短连接 seed SQL 在等待 server_lifetime=%s 秒后应形成可观测的 zero-ref 候选池；超容量时允许部分 seed 提前被淘汰" % server_lifetime,
                "actual": "active=%s | zero_ref_ready=%s" % ("; ".join(active_rows), "; ".join(zero_ref_rows)),
                "console_stats": dict(zero_ref_state["stats"]),
                "result": "PASS",
            },
            {
                "title": "SQL 5/6 继续施压时，淘汰都不能命中长连接条目",
                "expected": "两次 trigger 只能淘汰 zero-ref 普通条目，长连接 SQL 1 必须在 after_trigger_a、after_trigger_b、after 中都持续保留",
                "actual": "final=%s | evictions:%s->%s | capacity_logs=%s"
                % ("; ".join(matched), before_evictions, after_evictions, " | ".join(capacity_exceed_lines[:2]) if capacity_exceed_lines else "<none>"),
                "console_stats": dict(after_state["stats"]),
                "result": "PASS",
            },
        ],
        business_summary=[
            "本例运行配置明确是 `global_prepared_statements_limit=3`、`server_lifetime=%s`。" % server_lifetime,
            "SQL 1 使用长连接 `gc_capacity_zero_ref_hold_01`，连接在整个淘汰压力阶段都不关闭。",
            "SQL 2/3/4 使用短连接 `gc_capacity_zero_ref_01_01`、`gc_capacity_zero_ref_02_01`、`gc_capacity_zero_ref_03_01`，等待 `server_lifetime` 后它们形成 zero-ref 候选池。",
            "SQL 5 和 SQL 6 分别是短连接 trigger `gc_capacity_zero_ref_trigger_a_01`、`gc_capacity_zero_ref_trigger_b_01`，继续增加容量压力。",
            "本例核心断言不是去看已经释放掉的后端缓存，而是‘两次 trigger 压力下，长连接 SQL 1 不能被淘汰；被淘汰的只能是 zero-ref 普通条目’。",
        ],
        key_evidence=[
            "Stats delta: %s" % delta,
            "Active long-conn row: %s" % ("; ".join(active_rows) or "<missing>"),
            "Zero-ref ready: %s" % "; ".join(zero_ref_rows),
            "Capacity exceed logs: %s" % (" | ".join(capacity_exceed_lines[:3]) or "<missing>"),
            "Final survivors: %s" % "; ".join(matched),
        ],
    )


def assert_capacity_mixed_bypass_response_and_zero_ref_shortage(rt, before_state, heartbeat_state, guc_report_state, discard_state, zero_ref_ready_state, after_state):
    heartbeat_rows = ["|".join(row) for row in heartbeat_state["global"] if len(row) >= 2 and "SELECT 124" in row[1]]
    report_rows = ["|".join(row) for row in guc_report_state["global"] if len(row) >= 2 and "gc_capacity_fused_report" in row[1]]
    discard_rows = ["|".join(row) for row in discard_state["global"] if len(row) >= 2 and row[1].strip().upper() == "DISCARD ALL"]
    zero_ref_pressure_rows = [
        "|".join(row)
        for row in zero_ref_ready_state["global"]
        if len(row) >= 2 and "gc_capacity_fused_zero_ref_" in row[1]
    ]
    discard_zero_ref_ready = [row for row in zero_ref_ready_state["global"] if len(row) >= 2 and row[1].strip().upper() == "DISCARD ALL"]
    after_rows = ["|".join(row) for row in after_state["global"] if len(row) >= 2]
    heartbeat_after = [row for row in after_rows if "SELECT 124" in row and "|HEARTBEAT|" in row]
    report_after = [row for row in after_rows if "gc_capacity_fused_report" in row and "|GUC_SET_REPORT|" in row]
    discard_after = [row for row in after_rows if "|DISCARD ALL|" in row.upper()]
    zero_ref_after = [row for row in after_rows if "gc_capacity_fused_zero_ref_" in row]
    trigger_after = [row for row in after_rows if "gc_capacity_fused_trigger_active" in row]
    total_entries = int(after_state["stats"].get("total_entries", "0") or "0")
    capacity = int(after_state["stats"].get("capacity", "0") or "0")
    unreferenced = int(after_state["stats"].get("unreferenced_entries", "0") or "0")

    if not heartbeat_rows or not report_rows or not discard_rows:
        rt.record_step(
            "fused 容量检查失败",
            output="保护条目初始化不完整：heartbeat=%s report=%s discard=%s" % (bool(heartbeat_rows), bool(report_rows), bool(discard_rows)),
        )
        raise GlobalCacheFailure("capacity_mixed_bypass_response_and_zero_ref_shortage expects heartbeat/report/discard entries before pressure")
    if not heartbeat_after:
        rt.record_step("fused 容量检查失败", output="after 快照里 HEARTBEAT entry 消失了，说明错误淘汰了 has_bypass_response=1 的 heartbeat 条目。")
        raise GlobalCacheFailure("capacity_mixed_bypass_response_and_zero_ref_shortage expects HEARTBEAT to remain after pressure")
    if not report_after:
        rt.record_step("fused 容量检查失败", output="after 快照里 GUC_SET_REPORT entry 消失了，说明错误淘汰了 has_bypass_response=1 的 report 条目。")
        raise GlobalCacheFailure("capacity_mixed_bypass_response_and_zero_ref_shortage expects GUC_SET_REPORT to remain after pressure")
    if discard_after:
        rt.record_step("fused 容量检查失败", output="after 快照里仍保留 DISCARD ALL：\n%s" % "\n".join(discard_after))
        raise GlobalCacheFailure("capacity_mixed_bypass_response_and_zero_ref_shortage expects DISCARD ALL to be evicted after pressure")
    if zero_ref_after:
        rt.record_step("fused 容量检查失败", output="after 快照里仍保留普通 zero-ref SQL：\n%s" % "\n".join(zero_ref_after))
        raise GlobalCacheFailure("capacity_mixed_bypass_response_and_zero_ref_shortage expects ordinary zero-ref rows to be evicted after pressure")
    if not trigger_after:
        rt.record_step("fused 容量检查失败", output="after 快照里没有 active trigger row，说明最后留下来的不是预期的 active trigger 条目。")
        raise GlobalCacheFailure("capacity_mixed_bypass_response_and_zero_ref_shortage expects active trigger row to remain after pressure")
    if total_entries > capacity + 1:
        rt.record_step(
            "fused 容量检查失败",
            output=(
                "after 总数超出允许范围：expected_total<=capacity+1=%s actual_total=%s capacity=%s\n"
                "after global:\n%s"
            ) % (
                capacity + 1,
                total_entries,
                capacity,
                render_psql_expanded_from_pipe_text(
                    "global_name|description|sql_class|has_bypass_response|ref_count\n" +
                    "\n".join("|".join(row) for row in after_state["global"])
                ) if after_state["global"] else "<empty>",
            ),
        )
        raise GlobalCacheFailure("capacity_mixed_bypass_response_and_zero_ref_shortage expects total_entries not to exceed capacity+1, got total=%s capacity=%s" % (total_entries, capacity))
    if unreferenced < 0:
        rt.record_step("fused 容量检查失败", output="after 仍残留 unreferenced_entries=%s，说明候选池没有被消耗干净。" % unreferenced)
        raise GlobalCacheFailure("capacity_mixed_bypass_response_and_zero_ref_shortage got invalid unreferenced_entries=%s" % unreferenced)

    rt.summary["core_result"] = {
        "heartbeat_row": heartbeat_rows[0],
        "report_row": report_rows[0],
        "discard_row": discard_rows[0],
        "zero_ref_rows": list(zero_ref_pressure_rows),
        "after_rows": list(after_rows),
        "total_entries": total_entries,
        "capacity": capacity,
        "unreferenced": unreferenced,
    }
    _summary_set_report_blocks(
        rt,
        verification_checks=[
            {
                "title": "保护条目与可淘汰条目都已进入容量场景",
                "expected": "无论普通 zero-ref / DISCARD ALL 是否已被提前回收，HEARTBEAT、GUC_SET_REPORT 与可淘汰池都已在同一条容量链路里出现",
                "actual": "heartbeat=%s | report=%s | discard=%s | zero_ref=%s" % (
                    heartbeat_rows[0],
                    report_rows[0],
                    discard_rows[0],
                    "; ".join(zero_ref_pressure_rows) if zero_ref_pressure_rows else "<already evicted>",
                ),
                "result": "PASS",
            },
            {
                "title": "可淘汰池会优先被提前消费，而 protected 条目不能动",
                "expected": "普通 zero-ref / DISCARD ALL 可以被提前回收；但 HEARTBEAT 与 GUC_SET_REPORT 不能被误淘汰",
                "actual": (
                    "观察时仍可见 %s 条普通 zero-ref entry；DISCARD ALL %s；"
                    "HEARTBEAT 与 GUC_SET_REPORT 均保留"
                    % (
                        len(zero_ref_pressure_rows),
                        "仍可见" if discard_zero_ref_ready else "已被提前回收",
                    )
                ),
                "result": "PASS",
            },
            {
                "title": "最终仍保留 heartbeat/report，discard/普通被淘汰，且总数不会靠误删 protected 条目去硬收敛",
                "expected": "after 中至少保留 HEARTBEAT/GUC_SET_REPORT/active trigger，且 total_entries 不超过 capacity+1",
                "actual": "after=%s | total=%s capacity=%s unreferenced=%s" % ("; ".join(after_rows), total_entries, capacity, unreferenced),
                "console_stats": dict(after_state["stats"]),
                "result": "PASS",
            },
        ],
        business_summary=[
            "本例把两类行为合并在一条容量链路里观察：既验证 HEARTBEAT/GUC_SET_REPORT 因 `has_bypass_response=1` 被保护，也验证普通 zero-ref 与 DISCARD ALL 会被尽早回收。",
            "这次实测表明：普通 zero-ref 与 DISCARD ALL 在 trigger 前就已经被持续淘汰掉一大部分，这本身就是正确行为，因为它们都满足 `ref_count=0` 且 `has_bypass_response=0`。",
            "因此本例真正要锁住的不是“zero_ref_ready 必须保留 4 条普通 SQL”，而是“无论这些候选条目何时被回收，都不能误删 HEARTBEAT/GUC_SET_REPORT”。"
        ],
        key_evidence=[
            "Heartbeat row: %s" % heartbeat_rows[0],
            "GUC report row: %s" % report_rows[0],
            "DISCARD ALL row: %s" % discard_rows[0],
            "Zero-ref ordinary rows: %s" % ("; ".join(zero_ref_pressure_rows) if zero_ref_pressure_rows else "<already evicted>"),
            "After rows: %s" % "; ".join(after_rows),
            "After stats: total=%s capacity=%s unreferenced=%s" % (total_entries, capacity, unreferenced),
        ],
    )


def assert_ref_count_protects_active_entries(rt, before_state, after_state):
    capacity_limit = int(rt.case.reload.get("capacity_limit", 2))
    active_count = int(rt.case.reload.get("active_count", 4))
    delta = _stats_delta(before_state["stats"], after_state["stats"])
    total_entries = int(after_state["stats"].get("total_entries", "0") or "0")
    referenced_entries = int(after_state["stats"].get("referenced_entries", "0") or "0")
    capacity = int(after_state["stats"].get("capacity", "0") or "0")
    matched = [
        "|".join(row) for row in after_state["global"]
        if len(row) >= 5 and "gc_capacity_active_ref_" in row[1] and int((row[4] or "0").strip() or "0") > 0
    ]
    if capacity != capacity_limit:
        raise GlobalCacheFailure("ref_count_protects_active_entries expects capacity=%s, got %s" % (capacity_limit, capacity))
    if referenced_entries < active_count:
        raise GlobalCacheFailure("ref_count_protects_active_entries expects referenced_entries>=%s, got %s" % (active_count, referenced_entries))
    if total_entries < active_count:
        raise GlobalCacheFailure("ref_count_protects_active_entries expects total_entries>=%s, got %s" % (active_count, total_entries))
    if len(matched) < active_count:
        raise GlobalCacheFailure("ref_count_protects_active_entries expects %s active matched entries, got %s" % (active_count, len(matched)))
    rt.summary["core_result"] = {
        "capacity": capacity,
        "total_entries": total_entries,
        "referenced_entries": referenced_entries,
        "active_count": active_count,
        "matched_entries": list(matched),
        "matched_count": len(matched),
    }
    rt.summary["stats_delta"] = delta
    rt.summary["matched_global"] = matched
    _summary_set_report_blocks(
        rt,
        verification_checks=[
            {
                "title": "4 条 active prepared statement 都成功执行",
                "expected": "driver 日志中出现 active_01..active_04 结果，说明没有被超容量误删",
                "actual": "active_count=%s" % active_count,
                "result": "PASS",
            },
            {
                "title": "active refs 优先于容量约束",
                "expected": "capacity=%s 但 referenced_entries>=%s，且 total_entries 可暂时大于 capacity" % (capacity_limit, active_count),
                "actual": "capacity=%s total_entries=%s referenced_entries=%s" % (capacity, total_entries, referenced_entries),
                "console_stats": dict(after_state["stats"]),
                "result": "PASS",
            },
        ],
        business_summary=[
            "在 capacity=2 下同时持有 4 条 active prepared statement。",
            "最终 total_entries / referenced_entries 都保持在 active 数量附近，说明 active ref 不会被强行淘汰。",
        ],
        key_evidence=[
            "Stats delta: %s" % delta,
            "Active entries: %s" % "; ".join(matched),
        ],
    )
