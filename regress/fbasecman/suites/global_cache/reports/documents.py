"""Structured report.txt document routing for global-cache cases."""

import re

from framework.reporting import ReportCheck, ReportDocument, ReportStep
from suites.global_cache.reports.helpers import (
    check_actual as _check_actual,
    describe_pg_log_window as _describe_pg_log_window,
    record_or_text as _record_or_text,
    sequence_calls_text as _sequence_calls_text,
)


STRUCTURED_REPORT_CASES = {
    "heartbeat_reload_reclassifies_existing_normal_entry",
    "global_capacity_reload_shrink",
    "ref_count_protects_active_entries",
    "capacity_eviction_zero_ref",
    "capacity_mixed_bypass_response_and_zero_ref_shortage",
}


def _build_heartbeat_reclassify_document(case, summary, status, started_at, finished_at,
                                        pass_reason, failure_reason):
    headers = ["global_name", "description", "sql_class", "has_bypass_response", "ref_count"]
    prepared_sql = summary.get("prepared_sql", "SELECT 124")
    verify_sql = summary.get("verify_sql", prepared_sql)
    before_entries = summary.get("matched_global_before", [])
    after_entries = summary.get("matched_global", [])
    initial_output = summary.get("initial_prepared_output", "")
    verify_output = summary.get("verify_after_reload", "")
    initial_value = re.search(r"sql124_value=([^\r\n]+)", initial_output)
    verify_value = re.search(r"reload_sql124_value=([^\r\n]+)", verify_output)
    initial_ok = "sql124_execute=true" in initial_output and initial_value is not None
    verify_ok = "reload_sql124_execute=true" in verify_output and verify_value is not None
    initial_calls = [
        'PreparedStatement ps = conn.prepareStatement("%s");' % prepared_sql,
        "boolean hasResult = ps.execute();",
        "ResultSet rs = ps.getResultSet();",
        "while (rs.next()) { rs.getInt(1); }",
    ]
    steps = [
        ReportStep(
            "首次建立 global prepared statement cache",
            details=[
                ("动作", "JDBC PreparedStatement"),
                ("SQL", prepared_sql),
                ("参数", "无（0 个参数）"),
                ("driver 核心调用", "\n".join(initial_calls)),
            ],
            expected="ps.execute() 返回结果集，ResultSet 第 1 列为 124。",
            actual=("ResultSet 第 1 列=%s" % initial_value.group(1).strip())
            if initial_value else "<未从 JDBC 输出解析到结果值>",
            result="PASS" if initial_ok else "FAIL",
            checks=[
                ReportCheck(
                    "初次执行后创建 NORMAL global entry",
                    "heartbeat_request=%s 时，%s 以 NORMAL entry 进入全局缓存。"
                    % (summary.get("heartbeat_before", "select 123"), prepared_sql),
                    _record_or_text(before_entries[0]) if before_entries else "<未找到目标 SQL 条目>",
                    "PASS" if before_entries else "FAIL",
                )
            ],
        ),
        ReportStep(
            "修改 heartbeat 规则并 reload",
            details=[
                ("动作", "将 heartbeat_request 从 %s 改为 %s；随后执行 console=>RELOAD;" % (
                    summary.get("heartbeat_before", "select 123"),
                    summary.get("heartbeat_after", "select 124"),
                )),
            ],
        ),
        ReportStep(
            "reload 后再次执行同一 PreparedStatement",
            details=[
                ("动作", "JDBC PreparedStatement"),
                ("SQL", verify_sql),
                ("参数", "无（0 个参数）"),
                ("driver 核心调用", "\n".join([
                    'PreparedStatement ps = conn.prepareStatement("%s");' % verify_sql,
                    "boolean hasResult = ps.execute();",
                    "ResultSet rs = ps.getResultSet();",
                    "while (rs.next()) { rs.getInt(1); }",
                ])),
            ],
            expected="ps.execute() 返回结果集，ResultSet 第 1 列为 124。",
            actual=("ResultSet 第 1 列=%s" % verify_value.group(1).strip())
            if verify_value else "<未从 JDBC 输出解析到结果值>",
            result="PASS" if verify_ok else "FAIL",
        ),
    ]
    logs = summary.get("verify_log_window", {}).get("text", "")
    bypass_attach_line = next(
        (line.strip() for line in logs.splitlines() if "attached cached bypass response" in line),
        "<未提取到 cached bypass response 原文>",
    )
    heartbeat_cache_line = next(
        (line.strip() for line in logs.splitlines() if "heartbeat response from cache" in line),
        "<未提取到 heartbeat cache response 原文>",
    )
    cached = "attached cached bypass response" in logs and "heartbeat response from cache" in logs
    pg = summary.get("pg_log_verify", {})
    steps[-1].checks.extend([
        ReportCheck(
            "entry 重分类为 HEARTBEAT 并获得 bypass response",
            "reload 后，%s 对应 entry 的 sql_class=HEARTBEAT、has_bypass_response=1。" % verify_sql,
            _record_or_text(after_entries[0]) if after_entries else "<未找到目标 SQL 条目>",
            "PASS" if after_entries else "FAIL",
        ),
        ReportCheck(
            "fbasecman 使用 cached bypass response 应答",
            "fbasecman 挂载 cached bypass response，并以缓存 heartbeat 响应客户端。",
            "\n".join([bypass_attach_line, heartbeat_cache_line]),
            "PASS" if cached else "FAIL",
        ),
        ReportCheck(
            "验证请求未下发 PostgreSQL",
            "验证窗口内 PG 不新增 %s 的记录。" % verify_sql,
            _describe_pg_log_window(pg, verify_sql),
            "PASS" if pg.get("delta") == 0 else "FAIL",
        ),
    ])
    return ReportDocument(
        target=case.target,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        purpose=case.summary,
        config_lines=[
            '初始: heartbeat_request "%s"' % summary.get("heartbeat_before", "select 123"),
            'reload 后: heartbeat_request "%s"' % summary.get("heartbeat_after", "select 124"),
        ],
        overview_steps=_build_test_step_overview(case.name),
        steps=steps,
        pass_reason=pass_reason,
        failure_reason=failure_reason,
    )


def _build_guc_reload_document(case, summary, status, started_at, finished_at,
                               pass_reason, failure_reason):
    cfg = summary.get("reload_toggle", {})
    sequences = summary.get("guc_reload_sequences", {})
    before = sequences.get("before", {})
    after = sequences.get("after", {})
    expected_before = cfg.get("expected_before", "<unknown>")
    expected_after = cfg.get("expected_after", "<unknown>")
    before_marker = "before_reload_current_setting=%s" % expected_before
    after_marker = "after_reload_current_setting=%s" % expected_after
    checks = summary.get("verification_checks", [])

    def report_check(index):
        if index >= len(checks):
            return None
        item = checks[index]
        return ReportCheck(
            item.get("title", "未命名检测项"),
            item.get("expected", "<未提供预期>"),
            _check_actual(item),
            item.get("result", "UNKNOWN"),
        )

    before_check = report_check(0)
    after_checks = [check for check in (report_check(1), report_check(2)) if check is not None]
    steps = [
        ReportStep(
            "reload 前执行 GUC 同步验证",
            details=[
                ("动作", "设置 %s=%s，切换只读路径并查询 current_setting" % (
                    cfg.get("guc_name", "<unknown>"), cfg.get("guc_value", "<unknown>")
                )),
                ("driver 核心调用", _sequence_calls_text(before)),
            ],
            expected="JDBC 输出 %s。" % before_marker,
            actual=next(
                (line for line in before.get("output", "").splitlines() if line == before_marker),
                "<未找到 %s>" % before_marker,
            ),
            result="PASS" if before_marker in before.get("output", "") else "FAIL",
            checks=[before_check] if before_check is not None else [],
        ),
        ReportStep(
            "修改 enable_guc_sync 并 reload",
            details=[
                ("动作", "enable_guc_sync %s -> %s；console=>RELOAD;" % (
                    cfg.get("start", "<unknown>"), cfg.get("after", "<unknown>")
                )),
            ],
        ),
        ReportStep(
            "reload 后再次执行 GUC 同步验证",
            details=[
                ("动作", "重复设置、只读切换和 current_setting 查询"),
                ("driver 核心调用", _sequence_calls_text(after)),
            ],
            expected="JDBC 输出 %s。" % after_marker,
            actual=next(
                (line for line in after.get("output", "").splitlines() if line == after_marker),
                "<未找到 %s>" % after_marker,
            ),
            result="PASS" if after_marker in after.get("output", "") else "FAIL",
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
            "初始: enable_guc_sync %s" % cfg.get("start", "<unknown>"),
            "reload 后: enable_guc_sync %s" % cfg.get("after", "<unknown>"),
        ],
        overview_steps=[
            "按初始 enable_guc_sync 配置执行 GUC 设置、切后端与 current_setting 查询。",
            "修改 enable_guc_sync 并执行 RELOAD。",
            "重复相同业务路径，验证新配置已影响后续后端同步行为。",
        ],
        steps=steps,
        pass_reason=pass_reason,
        failure_reason=failure_reason,
    )


def build_structured_report_document(case, summary, status, started_at, finished_at,
                                     pass_reason=None, failure_reason=None):
    """Return report-contract data for cases that have complete structured evidence."""
    from suites.global_cache.reports.capacity import (
        _build_active_ref_capacity_document,
        _build_capacity_pressure_document,
        _build_capacity_reload_document,
    )

    if case.name == "heartbeat_reload_reclassifies_existing_normal_entry":
        return _build_heartbeat_reclassify_document(
            case, summary, status, started_at, finished_at, pass_reason, failure_reason
        )
    if case.name == "global_capacity_reload_shrink":
        return _build_capacity_reload_document(
            case, summary, status, started_at, finished_at, pass_reason, failure_reason
        )
    if case.name == "ref_count_protects_active_entries":
        return _build_active_ref_capacity_document(
            case, summary, status, started_at, finished_at, pass_reason, failure_reason
        )
    if case.name in (
        "capacity_eviction_zero_ref",
        "capacity_mixed_bypass_response_and_zero_ref_shortage",
    ):
        return _build_capacity_pressure_document(
            case, summary, status, started_at, finished_at, pass_reason, failure_reason
        )
    if "reload_toggle" in summary and "guc_reload_sequences" in summary:
        return _build_guc_reload_document(
            case, summary, status, started_at, finished_at, pass_reason, failure_reason
        )
    if "report_steps" in summary:
        return _build_generic_report_document(
            case, summary, status, started_at, finished_at, pass_reason, failure_reason
        )
    return None


def _build_generic_report_document(case, summary, status, started_at, finished_at,
                                   pass_reason, failure_reason):
    """Build the shared contract from runtime steps and business verification checks."""
    steps = []
    report_steps = list(summary.get("report_steps", []))
    if not report_steps and summary.get("failed_step"):
        report_steps.append(summary["failed_step"])
    for item in report_steps:
        details = []
        note = item.get("note", "")
        has_driver_calls = note.startswith("driver 核心调用:")
        # report.txt is a product-behavior document. Raw executable paths,
        # classpaths and connection strings remain available in events/logs.
        if item.get("note"):
            prefix = "driver 核心调用:"
            if note.startswith(prefix):
                details.append(("driver 核心调用", note[len(prefix):].strip()))
            else:
                details.append(("说明", note))
        for index, operation in enumerate(item.get("sql_operations", []), 1):
            suffix = " %d" % index if len(item.get("sql_operations", [])) > 1 else ""
            details.append(("SQL%s" % suffix, operation.get("sql", "<未提取到 SQL>")))
            parameters = operation.get("parameters", [])
            details.append((
                "参数%s" % suffix,
                "，".join(parameters) if parameters else "无（0 个参数）",
            ))
        steps.append(
            ReportStep(
                item.get("title") or "未命名测试步骤",
                details=details,
                expected=item.get("expected") or None,
                actual=item.get("actual") or item.get("output") or None,
                result=item.get("result") or None,
            )
        )

    checks = [
        ReportCheck(
            item.get("title", "未命名检测项"),
            item.get("expected", "<未提供预期>"),
            _check_actual(item),
            item.get("result", "UNKNOWN"),
        )
        for item in summary.get("verification_checks", [])
    ]
    if checks:
        if not steps:
            steps.append(ReportStep("验证产品行为"))
        # Composite scenarios prefix both action and check with the business
        # phase (for example "跨客户端复用:").  Put the proof directly below
        # that action rather than collecting unrelated checks at report end.
        for check in checks:
            phase, separator, _ = check.title.partition(":")
            destination = None
            if separator:
                prefix = phase + ":"
                for step in reversed(steps):
                    if step.title.startswith(prefix):
                        destination = step
                        break
            (destination or steps[-1]).checks.append(check)

    config_lines = []
    for raw_line in summary.get("fbasecman_config_lines", []):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("global_prepared_statements_limit "):
            line += "（配置容量上限：整个 fbasecman 最多保留的 global cache 条目数，不是当前条目数）"
        elif line.startswith("backend_prepared_statements_limit "):
            line += "（配置容量上限：每个 PostgreSQL backend 连接最多保留的 prepared statement 数，不是当前条目数）"
        config_lines.append(line)
    overview = [step.title for step in steps]
    return ReportDocument(
        target=case.target,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        purpose=case.summary,
        config_lines=config_lines,
        overview_steps=overview,
        steps=steps,
        pass_reason=pass_reason,
        failure_reason=failure_reason,
    )


def _build_test_step_overview(case_name):
    """Short business flow shown before verbose verification evidence."""
    if case_name == "heartbeat_reload_reclassifies_existing_normal_entry":
        return [
            "首次执行 prepared `SELECT 124`，建立 NORMAL global cache entry。",
            "把 heartbeat_request 从 `select 123` 改为 `select 124`，并执行 RELOAD。",
            "再次执行同一 prepared `SELECT 124`，观察重分类、缓存应答与 PG 下发情况。",
        ]
    return []
