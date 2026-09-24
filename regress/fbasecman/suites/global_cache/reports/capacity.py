"""Structured report documents for global-cache capacity scenarios."""

from framework.reporting import ReportCheck, ReportDocument, ReportStep
from suites.global_cache.reports.helpers import (
    check_actual as _check_actual,
    global_records_or_text as _global_records_or_text,
    record_or_text as _record_or_text,
    sequence_calls_text as _sequence_calls_text,
    stats_record_or_text as _stats_record_or_text,
)


def _checks_by_title(summary):
    return {
        item.get("title", ""): ReportCheck(
            item.get("title", "未命名检测项"),
            item.get("expected", "<未提供预期>"),
            _check_actual(item),
            item.get("result", "UNKNOWN"),
        )
        for item in summary.get("verification_checks", [])
    }


def _build_capacity_reload_document(case, summary, status, started_at, finished_at,
                                    pass_reason, failure_reason):
    before_limit = summary.get("capacity_before_limit", 10)
    after_limit = summary.get("capacity_after_limit", 5)
    server_lifetime = summary.get("capacity_server_lifetime", 10)
    checks = _checks_by_title(summary)
    unref = checks.get("shrink 前 seeded entries 已全部进入 unref 状态")
    after_checks = [
        checks[title] for title in (
            "reload 后 capacity 收缩到新上限",
            "reload 后全局缓存条目同步收缩",
            "缩容后仍保留可观测的 surviving entries",
        ) if title in checks
    ]
    output = summary.get("jdbc_sequence_output", "").strip() or "<未采集到 JDBC 输出>"
    steps = [
        ReportStep(
            "建立待缩容的 global cache entries",
            details=[
                ("动作", "连续执行 %s 条不同的 prepared SELECT" % len(summary.get("jdbc_sequence", []))),
                ("driver 核心调用", _sequence_calls_text({"operations": summary.get("jdbc_sequence", [])})),
            ],
            expected="每条 PreparedStatement 均成功返回查询结果。",
            actual=output,
            result="PASS" if output != "<未采集到 JDBC 输出>" else "FAIL",
        ),
        ReportStep(
            "等待 seeded entries 释放引用",
            details=[("动作", "等待并通过 fbasecman console 观察目标 entries 的 ref_count")],
            expected="所有 seeded entries 的 ref_count 均变为 0。",
            actual=unref.actual if unref else "<未采集到 ref_count 观察>",
            result=unref.result if unref else "UNKNOWN",
        ),
        ReportStep(
            "修改容量配置并 reload",
            details=[("动作", "global/backend prepared statements limit: %s -> %s；console=>RELOAD;" % (before_limit, after_limit))],
            checks=after_checks,
        ),
    ]
    return ReportDocument(
        target=case.target,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        purpose=case.summary,
        config_lines=[
            "初始: global_prepared_statements_limit %s" % before_limit,
            "初始: backend_prepared_statements_limit %s" % before_limit,
            "reload 后: global_prepared_statements_limit %s" % after_limit,
            "reload 后: backend_prepared_statements_limit %s" % after_limit,
            "server_lifetime %s" % server_lifetime,
        ],
        overview_steps=[step.title for step in steps],
        steps=steps,
        pass_reason=pass_reason,
        failure_reason=failure_reason,
    )


def _build_active_ref_capacity_document(case, summary, status, started_at, finished_at,
                                        pass_reason, failure_reason):
    operations = summary.get("phased_prepared_operations", [])
    calls = []
    for operation in operations:
        calls.append(
            'PreparedStatement ps = conn.prepareStatement("%s"); ps.setInt(1, %s); ResultSet rs = ps.executeQuery();'
            % (operation.get("sql", "<unknown>"), operation.get("bind_value", "<unknown>"))
        )
    output = summary.get("phased_prepared_output", "").strip()
    checks = [
        ReportCheck(
            item.get("title", "未命名检测项"),
            item.get("expected", "<未提供预期>"),
            _check_actual(item),
            item.get("result", "UNKNOWN"),
        )
        for item in summary.get("verification_checks", [])
    ]
    core = summary.get("core_result", {})
    checks.append(ReportCheck(
        "活动业务 entries 均保留在 global cache",
        "4 条目标 entry 均存在，且 ref_count 全部大于 0。",
        _global_records_or_text(core.get("matched_entries", [])),
        "PASS" if core.get("matched_count") == len(operations) else "FAIL",
    ))
    step = ReportStep(
        "建立并保持多个 active prepared statements",
        details=[
            ("动作", "在 %s 个独立 JDBC 连接中执行 prepared SELECT，并在观察期间保持连接和 statement 不关闭" % len(operations)),
            ("driver 核心调用", "\n".join(calls)),
        ],
        expected="每条查询均成功返回，%s 个连接在观察期间保持活动。" % len(operations),
        actual=(
            "%s 条查询均成功返回；%s 个连接在观察期间保持活动；观察完成后正常结束。"
            % (len(operations), len(operations))
            if "PHASE=READY" in output else "<未进入连接保持阶段>"
        ),
        result="PASS" if "PHASE=READY" in output else "FAIL",
        checks=checks,
    )
    capacity = core.get("capacity", 2)
    return ReportDocument(
        target=case.target,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        purpose=case.summary,
        config_lines=[
            "global_prepared_statements_limit %s" % capacity,
            "backend_prepared_statements_limit %s" % capacity,
        ],
        overview_steps=[
            "建立并保持 %s 个 active prepared statements。" % len(operations),
            "在连接仍活动时检查 capacity、引用数量和 global entries。",
        ],
        steps=[step],
        pass_reason=pass_reason,
        failure_reason=failure_reason,
    )


def _capacity_checks(summary):
    return [
        ReportCheck(
            item.get("title", "未命名检测项"),
            item.get("expected", "<未提供预期>"),
            _check_actual(item),
            item.get("result", "UNKNOWN"),
        )
        for item in summary.get("verification_checks", [])
    ]


def _build_capacity_pressure_document(case, summary, status, started_at, finished_at,
                                      pass_reason, failure_reason):
    core = summary.get("core_result", {})
    checks = _capacity_checks(summary)
    name = case.name
    if name == "capacity_eviction_zero_ref":
        if checks:
            checks[0].actual = "活动 entry:\n%s\nzero-ref entries:\n%s" % (
                _global_records_or_text(core.get("active_rows", [])),
                _global_records_or_text(core.get("zero_ref_rows", [])),
            )
            stats = summary.get("verification_checks", [{}])[0].get("console_stats")
            if stats:
                checks[0].actual += "\n控制台统计:\n" + _stats_record_or_text(stats)
        steps = [
            ReportStep(
                "建立并保持 active prepared statement",
                details=[("动作", "执行长连接 prepared SELECT，并在观察期间保持连接")],
                expected="查询成功返回，目标 entry 在观察期间保持 active。",
                actual=(
                    "长连接查询返回 name；连接与 prepared statement 在观察期间保持活动；"
                    "观察完成后正常结束。"
                    if "PHASE=READY" in summary.get("capacity_active_output", "")
                    else "<未进入连接保持阶段>"
                ),
                result="PASS" if "PHASE=READY" in summary.get("capacity_active_output", "") else "FAIL",
            ),
            ReportStep(
                "建立短连接 entries 并等待 zero-ref",
                details=[("动作", "执行 3 条短连接 prepared SELECT，等待 server_lifetime 释放引用")],
                expected="短连接 entries 进入 ref_count=0 候选池，active entry 仍保留。",
                actual="active=%s 条；zero-ref=%s 条" % (
                    len(core.get("active_rows", [])), len(core.get("zero_ref_rows", []))
                ),
                result="PASS" if core.get("active_rows") and core.get("zero_ref_rows") else "FAIL",
                checks=checks[:1],
            ),
            ReportStep(
                "连续发送两条 trigger SQL",
                details=[("动作", "依次执行 trigger_a 和 trigger_b，对 capacity=3 持续施压")],
                expected="两次 trigger 后 total_entries 均不超过 capacity，active entry 不被淘汰。",
                actual="trigger_a total_entries=%s；trigger_b total_entries=%s；最终 evictions=%s" % (
                    len(core.get("trigger_a_rows", [])),
                    len(core.get("trigger_b_rows", [])),
                    core.get("evictions_after", "<unknown>"),
                ),
                result="PASS" if checks[1:] and all(check.result == "PASS" for check in checks[1:]) else "FAIL",
                checks=checks[1:],
            ),
        ]
        config_lines = [
            "global_prepared_statements_limit 3",
            "backend_prepared_statements_limit 3",
            "server_lifetime %s" % core.get("server_lifetime", 10),
        ]
    elif name == "capacity_eviction_limited_by_zero_ref_count":
        if checks:
            checks[0].actual = "active=3 条；普通 zero-ref=%s 条；bypass-response=%s 条" % (
                len(core.get("zero_ref_rows", [])), len(core.get("bypass_rows", []))
            )
        steps = [
            ReportStep(
                "建立 active、zero-ref 和 bypass-response 三类 entries",
                details=[("动作", "保持 3 条 active；建立 4 条普通短连接和 2 条 GUC report entries")],
                expected="阶段 driver 已建立 active entry，随后普通短连接释放为 zero-ref。",
                actual=(
                    "active prepared SQL 已执行并保持连接；普通短连接后续可释放为 zero-ref。"
                    if "PHASE=READY" in summary.get("capacity_active_output", "")
                    else "<未进入连接保持阶段>"
                ),
                result="PASS" if "PHASE=READY" in summary.get("capacity_active_output", "") else "FAIL",
                checks=checks[:1],
            ),
            ReportStep(
                "reload 缩小容量",
                details=[("动作", "global/backend prepared statements limit: 10 -> 3；console=>RELOAD;")],
                checks=checks[1:],
            ),
        ]
        config_lines = [
            "初始: global_prepared_statements_limit 10",
            "初始: backend_prepared_statements_limit 10",
            "reload 后: global_prepared_statements_limit 3",
            "reload 后: backend_prepared_statements_limit 3",
            "server_lifetime 10",
        ]
    else:
        if checks:
            checks[0].actual = _global_records_or_text([
                value for value in (
                    core.get("heartbeat_row"), core.get("report_row"),
                    core.get("discard_row"),
                ) if value
            ] + core.get("zero_ref_rows", []))
        if len(checks) > 2:
            checks[2].actual = _global_records_or_text(core.get("after_rows", []))
            source_checks = summary.get("verification_checks", [])
            stats = source_checks[2].get("console_stats") if len(source_checks) > 2 else None
            if stats:
                checks[2].actual += "\n控制台统计:\n" + _stats_record_or_text(stats)
        setup_output = "\n".join(filter(None, [
            summary.get("capacity_heartbeat_output", "").strip(),
            summary.get("capacity_guc_report_output", "").strip(),
            summary.get("capacity_discard_output", "").strip(),
        ]))
        steps = [
            ReportStep(
                "建立 protected 与可淘汰 entries",
                details=[("动作", "依次执行 heartbeat、GUC report、DISCARD ALL 和 4 条普通短连接 prepared SQL")],
                expected="protected 与普通 zero-ref 两类 entry 均进入同一容量场景。",
                actual=(
                    "heartbeat、GUC report、DISCARD ALL 与 4 条普通 prepared SQL 均已执行；"
                    "entry 分类与保留状态见检测项。"
                    if setup_output else "<未采集到 JDBC 输出>"
                ),
                result="PASS" if setup_output else "FAIL",
                checks=checks[:2],
            ),
            ReportStep(
                "保持 active trigger 并观察最终容量状态",
                details=[("动作", "执行 trigger prepared SELECT，在连接保持期间查询 fbasecman console")],
                expected="trigger 查询成功且连接保持期间 protected entries 不被误淘汰。",
                actual=(
                    "trigger 查询返回 name；连接在观察期间保持，随后正常结束。"
                    if "PHASE=READY" in summary.get("capacity_trigger_output", "")
                    else "<未进入连接保持阶段>"
                ),
                result="PASS" if "PHASE=READY" in summary.get("capacity_trigger_output", "") else "FAIL",
                checks=checks[2:],
            ),
        ]
        config_lines = [
            "global_prepared_statements_limit 3",
            "backend_prepared_statements_limit 3",
            'heartbeat_request "select 124"',
            "server_lifetime 10",
        ]
    return ReportDocument(
        target=case.target,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        purpose=case.summary,
        config_lines=config_lines,
        overview_steps=[step.title for step in steps],
        steps=steps,
        pass_reason=pass_reason,
        failure_reason=failure_reason,
    )
