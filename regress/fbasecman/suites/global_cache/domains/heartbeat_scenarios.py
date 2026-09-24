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

from suites.global_cache.domains.common_assertions import _stats_change_text
from suites.global_cache.domains.driver_cases import _run_libpq_case, _run_jdbc_case
def _run_heartbeat_rule_precedence_case(rt):
    global_heartbeat = rt.case.assertions.get("global_heartbeat", "select 999")
    user_heartbeat = rt.case.assertions.get("user_heartbeat", "select 124")
    verify_sql = rt.case.assertions.get("verify_sql", "SELECT 124")
    expected_value = rt.case.assertions.get("expected_value", "124")
    start_conf = rt.render_runtime_conf(
        [
            ('heartbeat_request "select 10086"', 'heartbeat_request "%s"' % global_heartbeat),
            ('storage_user "postgres"', 'storage_user "postgres"\n    heartbeat_request "%s"' % user_heartbeat),
        ],
        "heartbeat_rule_precedence.conf",
    )
    rt.start_fbasecman(conf=start_conf)
    verify_log_start = rt.log_offset(rt.fbasecman_log)
    _, verify_text = _run_jdbc_asset_phased(
        rt,
        "GC_prepared_single_query.java",
        "GC_prepared_single_query",
        [verify_sql, "rule_precedence"],
        step_title="执行外置 heartbeat 规则优先级 JDBC driver",
        actions=[PhaseAction(
            "after_execute", "PHASE=AFTER_EXECUTE",
            title="用户级 heartbeat 查询完成后观察缓存",
            expected="JDBC 查询完成且连接保持；console 和日志显示用户级 heartbeat 语义。",
        )],
        sql_operations=[{"sql": verify_sql, "parameters": []}],
    )
    verify_log_end = rt.log_offset(rt.fbasecman_log)
    verify_window_text = rt.read_log_slice(rt.fbasecman_log, verify_log_start, verify_log_end)
    after_state = rt.capture_console_state("after")
    rt.summary["after_stats"] = after_state["stats"]
    rt.summary["global_heartbeat"] = global_heartbeat
    rt.summary["user_heartbeat"] = user_heartbeat
    rt.summary["verify_sql"] = verify_sql
    rt.summary["verify_log_window"] = {
        "start": verify_log_start,
        "end": verify_log_end,
        "text": verify_window_text.strip(),
    }
    matched = [
        "|".join(row) for row in after_state["global"]
        if len(row) >= 2 and verify_sql.lower() in row[1].lower()
    ]
    if "rule_precedence_execute=true" not in verify_text:
        raise GlobalCacheFailure("heartbeat rule precedence expects JDBC execute=true")
    if ("rule_precedence_value=%s" % expected_value) not in verify_text:
        raise GlobalCacheFailure(
            "heartbeat rule precedence expects returned value=%s, actual log: %s" % (expected_value, verify_text.strip() or "<empty>")
        )
    if "heartbeat response from cache" not in verify_window_text:
        raise GlobalCacheFailure("heartbeat rule precedence expects cached heartbeat bypass response")
    if re.search(r"rule_precedence_value=999\b", verify_text):
        raise GlobalCacheFailure("heartbeat rule precedence expects user-level heartbeat to override global heartbeat value 999")
    rt.step_records[-1].update({
        "expected": "%s 返回 %s，且不采用全局 heartbeat 返回值 999。" % (
            verify_sql, expected_value
        ),
        "actual": "%s 返回 %s；用户级 heartbeat 规则生效。" % (
            verify_sql, expected_value
        ),
        "result": "PASS",
    })
    rt.summary["matched_global"] = matched
    rt.summary["verification_checks"] = [
        {
            "title": "用户级 heartbeat 优先于全局 heartbeat",
            "expected": "发送 %s 时返回 %s，而不是全局 heartbeat 的 999" % (verify_sql, expected_value),
            "actual": "%s 返回 %s；未返回全局 heartbeat 值 999" % (
                verify_sql, expected_value
            ),
            "result": "PASS",
        },
        {
            "title": "请求继续走 heartbeat bypass",
            "expected": "关键日志出现 heartbeat response from cache",
            "actual": "heartbeat response from cache",
            "result": "PASS",
        },
    ]
    rt.summary["business_summary_lines"] = [
        "将全局 heartbeat 配成 %s，同时在业务用户规则里单独配置 heartbeat 为 %s。" % (global_heartbeat, user_heartbeat),
        "业务用户发送 %s 后返回值应来自用户级 heartbeat，而不是全局 heartbeat。" % verify_sql,
    ]
    rt.summary["key_evidence_lines"] = [
        "JDBC: %s" % (verify_text.strip() or "<empty>"),
        "Console(global): %s" % ("; ".join(matched) if matched else "<empty>"),
    ]


def _run_same_sql_different_users_isolation_case(rt):
    shared_sql = rt.case.assertions.get("shared_sql", "SELECT 124")
    user1 = rt.case.assertions.get("user1_name", "postgres")
    user2 = rt.case.assertions.get("user2_name", "postgres2")
    user1_heartbeat = rt.case.assertions.get("user1_heartbeat", "select 124")
    user2_heartbeat = rt.case.assertions.get("user2_heartbeat", "select 999")
    expected_user1_value = rt.case.assertions.get("expected_user1_value", "124")
    expected_user2_value = rt.case.assertions.get("expected_user2_value", "124")
    start_conf = rt.render_runtime_conf(
        [
            ('storage_user "postgres"', 'storage_user "postgres"\n    heartbeat_request "%s"' % user1_heartbeat),
            ('user "admin" {', 'user "postgres2" {\n    group_names  "postgres"\n    rw_split_method "hint"\n    authentication "none"\n    storage_user "postgres"\n    heartbeat_request "%s"\n    pool "transaction"\n    pool_size 20\n    log_debug yes\n    pool_discard no\n    pool_reserve_prepared_statement yes\n    pool_ttl 1\n    server_lifetime 3600\n    quantiles "0.99,0.95,0.5"\n    client_max 100\n}\n\nuser "admin" {' % user2_heartbeat),
        ],
        "same_sql_different_users.conf",
    )
    rt.start_fbasecman(conf=start_conf)
    verify_log_start = rt.log_offset(rt.fbasecman_log)
    _, verify_text = _run_jdbc_asset_phased(
        rt,
        "GC_same_sql_different_users.java",
        "GC_same_sql_different_users",
        [user2, "", shared_sql],
        user=user1,
        actions=[
            PhaseAction(
                "after_user1", "PHASE=AFTER_USER1",
                title="user1 执行同 SQL 后观察缓存",
                expected="user1 已执行并释放连接，user2 尚未执行同 SQL。",
            ),
            PhaseAction(
                "after_user2", "PHASE=AFTER_USER2",
                title="user2 执行同 SQL 后观察隔离结果",
                expected="user2 执行完成且连接保持；同 SQL 未发生跨用户 heartbeat 污染。",
            ),
        ],
        sql_operations=[{"sql": shared_sql, "parameters": []}],
        step_title="执行外置双用户 JDBC driver",
    )
    verify_log_end = rt.log_offset(rt.fbasecman_log)
    verify_window_text = rt.read_log_slice(rt.fbasecman_log, verify_log_start, verify_log_end)
    after_state = rt.capture_console_state("after")
    rt.summary["after_stats"] = after_state["stats"]
    rt.summary["verify_log_window"] = {"start": verify_log_start, "end": verify_log_end, "text": verify_window_text.strip()}
    if ("user1_value=%s" % expected_user1_value) not in verify_text:
        raise GlobalCacheFailure("same_sql_different_users expects user1 value=%s" % expected_user1_value)
    if ("user2_value=%s" % expected_user2_value) not in verify_text:
        raise GlobalCacheFailure("same_sql_different_users expects user2 value=%s" % expected_user2_value)
    if "user2_value=999" in verify_text:
        raise GlobalCacheFailure("same_sql_different_users detected cross-user heartbeat pollution into user2")
    rt.set_latest_evidence_outcome(
        "user1 与 user2 执行同一条 %s 都返回各自应得到的结果。" % shared_sql,
        "user1 返回 %s；user2 返回 %s；两个连接均执行成功。" % (
            expected_user1_value, expected_user2_value
        ), True,
    )
    rt.summary["verification_checks"] = [
        {
            "title": "user1 执行 `SELECT 124` 时命中自己的 heartbeat 语义",
            "expected": "user1 执行 %s 返回 %s" % (shared_sql, expected_user1_value),
            "actual": "user1 执行 %s 返回 %s" % (shared_sql, expected_user1_value),
            "result": "PASS",
        },
        {
            "title": "user2 执行同一条 `SELECT 124` 时不被 user1 的 heartbeat/bypass response 污染",
            "expected": "user2 执行同一 SQL 返回真实结果 %s，而不是被改写成 999" % expected_user2_value,
            "actual": "user2 执行 %s 返回 %s；未被改写成 999" % (
                shared_sql, expected_user2_value
            ),
            "result": "PASS",
        },
    ]
    rt.summary["business_summary_lines"] = [
        "为 user1 配置 heartbeat `%s`，为 user2 配置 heartbeat `%s`，但两人都执行同一条 SQL `%s`。" % (user1_heartbeat, user2_heartbeat, shared_sql),
        "先看 user1 执行 `%s` 是否按自己的 heartbeat 语义返回，再看 user2 执行同一条 SQL 时是否仍返回真实结果 `%s`。" % (shared_sql, expected_user2_value),
    ]
    rt.summary["key_evidence_lines"] = [
        "JDBC: %s" % (verify_text.strip() or "<empty>"),
    ]


def _assert_guc_set_report_bypass(rt, before_state, after_state):
    expected_class = rt.case.assertions.get("expected_sql_class", "GUC_SET_REPORT")
    matched = [
        "|".join(row) for row in after_state["global"]
        if len(row) >= 5 and "application_name" in row[1].lower()
    ]
    if not matched:
        raise GlobalCacheFailure("guc set report case expects application_name entry in global cache")
    if expected_class not in matched[0]:
        raise GlobalCacheFailure("guc set report case expects sql_class=%s, got %s" % (expected_class, matched[0]))
    if "|1|" not in matched[0] and not matched[0].endswith("|1"):
        raise GlobalCacheFailure("guc set report case expects has_bypass_response=1, got %s" % matched[0])
    rt.summary["matched_global"] = matched
    rt.summary["verification_checks"] = [
        {
            "title": "prepared SET application_name 被识别为 GUC_SET_REPORT",
            "expected": "global cache 中存在 application_name 对应 entry，sql_class=GUC_SET_REPORT",
            "actual": matched[0],
            "result": "PASS",
        },
        {
            "title": "该 GUC entry 已缓存 bypass response",
            "expected": "has_bypass_response=1，说明客户端可以直接消费 bypass 响应",
            "actual": matched[0],
            "result": "PASS",
        },
    ]
    rt.summary["business_summary_lines"] = [
        "显式以 prepared 方式执行 SET application_name。",
        "验证该 GUC SQL 会进入 global cache，并被标记成 GUC_SET_REPORT bypass entry。",
    ]
    rt.summary["key_evidence_lines"] = [
        "Console(global): %s" % matched[0],
    ]


def _assert_guc_reset_all_bypass(rt, before_state, after_state):
    expected_class = rt.case.assertions.get("expected_reset_sql_class", "GUC_RESET_ALL")
    log_text = (rt.logs_dir / "GCGucResetAllBypass.java.log").read_text(encoding="utf-8", errors="replace")
    reset_entries = [
        "|".join(row) for row in after_state["global"]
        if len(row) >= 5 and row[1].strip().upper() == "RESET ALL"
    ]
    if not reset_entries:
        raise GlobalCacheFailure("guc reset all bypass case expects RESET ALL entry in global cache")
    if expected_class not in reset_entries[0]:
        raise GlobalCacheFailure("guc reset all bypass case expects sql_class=%s, got %s" % (expected_class, reset_entries[0]))
    if "guc_reset_all_verify_value=" not in log_text:
        raise GlobalCacheFailure("guc reset all bypass case expects follow-up SHOW output")
    rt.summary["matched_global"] = reset_entries
    rt.summary["verification_checks"] = [
        {
            "title": "prepared RESET ALL 被识别为 GUC_RESET_ALL",
            "expected": "global cache 中存在 RESET ALL 对应 entry，sql_class=GUC_RESET_ALL",
            "actual": reset_entries[0],
            "result": "PASS",
        },
        {
            "title": "RESET ALL bypass 后连接仍可继续查询",
            "expected": "后续 SHOW extra_float_digits 正常返回",
            "actual": "JDBC 日志已捕获 guc_reset_all_verify_value",
            "result": "PASS",
        },
    ]
    rt.summary["business_summary_lines"] = [
        "先设置一个 report 参数和一个 noreport 参数。",
        "再以 prepared 方式执行 RESET ALL，验证它形成独立的 GUC_RESET_ALL bypass entry。",
    ]
    rt.summary["key_evidence_lines"] = [
        "Console(global): %s" % reset_entries[0],
    ]


def _find_rows_by_sql(state, sql_text):
    needle = sql_text.lower()
    return [
        row for row in state["global"]
        if len(row) >= 3 and row[1].strip().lower() == needle
    ]


def _assert_heartbeat_reload_reclassifies_existing_normal_entry(rt, before_state, after_state):
    sql_text = rt.case.assertions.get("prepared_sql", "SELECT 124")
    verify_sql = rt.case.assertions.get("verify_sql", sql_text)
    heartbeat_before = rt.case.assertions.get("heartbeat_before", "select 123")
    heartbeat_after = rt.case.assertions.get("heartbeat_after", "select 124")
    expect_heartbeat_after_reload = rt.case.assertions.get("expect_heartbeat_after_reload", True)
    before_rows = _find_rows_by_sql(before_state, sql_text)
    after_rows = _find_rows_by_sql(after_state, sql_text)
    before_classes = sorted(set(row[2].strip().upper() for row in before_rows if len(row) >= 3))
    after_classes = sorted(set(row[2].strip().upper() for row in after_rows if len(row) >= 3))
    after_bypass_flags = sorted(set(row[3].strip() for row in after_rows if len(row) >= 4))
    verify_text = rt.summary.get("heartbeat_verify_output", "")
    verify_window = rt.summary.get("verify_log_window", {})
    verify_window_text = verify_window.get("text", "")
    pg_log_verify = rt.summary.get("pg_log_verify", {})
    rt.summary["heartbeat_before"] = heartbeat_before
    rt.summary["heartbeat_after"] = heartbeat_after
    rt.summary["prepared_sql"] = sql_text
    rt.summary["verify_sql"] = verify_sql
    rt.summary["heartbeat_config_before"] = 'heartbeat_request "%s"' % heartbeat_before
    rt.summary["heartbeat_config_after"] = 'heartbeat_request "%s"' % heartbeat_after
    rt.summary["matched_global_before"] = ["|".join(row) for row in before_rows]
    rt.summary["matched_global"] = ["|".join(row) for row in after_rows]
    rt.summary["reclassify"] = {"before": before_classes, "after": after_classes}
    rt.summary["verify_after_reload"] = verify_text.strip() or "<empty>"
    if not before_rows:
        raise GlobalCacheFailure("heartbeat reclassify case expects target sql entry before reload")
    if not after_rows:
        raise GlobalCacheFailure("heartbeat reclassify case expects target sql entry after reload")
    if before_classes != ["NORMAL"]:
        raise GlobalCacheFailure("heartbeat reclassify case expects target sql class NORMAL before reload, got %s" % before_classes)
    if expect_heartbeat_after_reload:
        if "HEARTBEAT" not in after_classes:
            raise GlobalCacheFailure("heartbeat reclassify case expects target sql class HEARTBEAT after reload, got %s" % after_classes)
        if "1" not in after_bypass_flags:
            raise GlobalCacheFailure("heartbeat reclassify case expects target sql has_bypass_response=1 after reload, got %s" % after_bypass_flags)
    else:
        verify_prefix = verify_sql.rstrip(";")
        variant_verify_rows = [
            row for row in after_state["global"]
            if len(row) >= 4 and row[1].lower().startswith(verify_prefix.lower()) and row[1].lower() != sql_text.lower()
        ]
        rt.summary["matched_verify_sql_variants"] = ["|".join(row) for row in variant_verify_rows]
        exact_verify_classes = sorted(set(row[2].strip().upper() for row in variant_verify_rows if len(row) >= 3))
        exact_verify_bypass_flags = sorted(set(row[3].strip() for row in variant_verify_rows if len(row) >= 4))
        rt.summary["verify_sql_reclassify"] = {
            "sql": verify_sql,
            "classes": exact_verify_classes,
            "bypass_flags": exact_verify_bypass_flags,
        }
        if not variant_verify_rows:
            raise GlobalCacheFailure(
                "heartbeat reclassify case expects a non-canonical verify_sql variant entry after reload, but none found for %s" % verify_sql
            )
        if "HEARTBEAT" in exact_verify_classes:
            raise GlobalCacheFailure(
                "heartbeat reclassify case expects verify_sql to remain non-HEARTBEAT after reload, got %s" % exact_verify_classes
            )
        if "0" not in exact_verify_bypass_flags:
            raise GlobalCacheFailure(
                "heartbeat reclassify case expects verify_sql has_bypass_response=0 after reload, got %s" % exact_verify_bypass_flags
            )
    if "reload_sql124_execute=true" not in verify_text or "reload_sql124_value=124" not in verify_text:
        raise GlobalCacheFailure("heartbeat reclassify case expects post-reload JDBC verify to return 124 via prepared statement")
    if expect_heartbeat_after_reload:
        if "heartbeat response from cache" not in verify_window_text:
            raise GlobalCacheFailure("heartbeat reclassify case expects verify log window to show cached heartbeat bypass response")
        if pg_log_verify.get("delta") != 0:
            raise GlobalCacheFailure(
                "heartbeat reclassify case expects post-reload verify SQL to stay out of PG logs, got delta=%s" % pg_log_verify.get("delta")
            )
        forbidden_patterns = [
            "(outstanding_request) added E",
            "(outstanding_request) added S",
        ]
        hit_forbidden = [pattern for pattern in forbidden_patterns if pattern in verify_window_text]
        if hit_forbidden:
            raise GlobalCacheFailure(
                "heartbeat reclassify case expects post-reload verify to avoid backend execute/sync, but found %s" % hit_forbidden
            )
    else:
        if pg_log_verify.get("delta") != 1:
            raise GlobalCacheFailure(
                "heartbeat reclassify case expects non-heartbeat verify SQL to reach PG exactly once, got delta=%s" % pg_log_verify.get("delta")
            )
        required_patterns = [
            "(outstanding_request) added E",
            "(outstanding_request) added S",
        ]
        missing_patterns = [pattern for pattern in required_patterns if pattern not in verify_window_text]
        if missing_patterns:
            raise GlobalCacheFailure(
                "heartbeat reclassify case expects non-heartbeat verify to include backend execute/sync, missing %s" % missing_patterns
            )


def _run_heartbeat_reclassify_case(rt):
    heartbeat_before = rt.case.assertions.get("heartbeat_before", "select 123")
    heartbeat_after = rt.case.assertions.get("heartbeat_after", "select 124")
    prepared_sql = rt.case.assertions.get("prepared_sql", "SELECT 124")
    verify_sql = rt.case.assertions.get("verify_sql", prepared_sql)
    verify_value = verify_sql.split()[-1].rstrip(";")
    start_conf = rt.render_runtime_conf(
        [('heartbeat_request "select 10086"', 'heartbeat_request "%s"' % heartbeat_before)],
        "heartbeat_reclassify_live.conf",
    )
    start_conf_text = start_conf.read_text(encoding="utf-8", errors="replace")
    rt.start_fbasecman(conf=start_conf)
    rt.case.sql = {"statement": prepared_sql, "tag": "gc_heartbeat_variant"}
    initial_output = _run_jdbc_case(rt)
    rt.summary["initial_prepared_output"] = "\n".join(initial_output)
    before_reload_state = rt.capture_console_state("before_reload")
    rt.summary["before_reload_stats"] = before_reload_state["stats"]
    reload_conf = rt.render_runtime_conf(
        [('heartbeat_request "select 10086"', 'heartbeat_request "%s"' % heartbeat_after)],
        "heartbeat_reclassify_next.conf",
    )
    reload_conf_text = reload_conf.read_text(encoding="utf-8", errors="replace")
    _record_reload_conf_steps(
        rt,
        "初始运行配置: heartbeat_request %s" % heartbeat_before,
        "修改运行中配置: heartbeat_request %s -> %s" % (heartbeat_before, heartbeat_after),
        start_conf,
        start_conf_text,
        reload_conf,
        reload_conf_text,
        [("heartbeat_request", '"%s"' % heartbeat_before, '"%s"' % heartbeat_after)],
    )
    _install_reload_config(reload_conf, start_conf, [("heartbeat_request", '"%s"' % heartbeat_after)])
    rt.summary["heartbeat_before"] = heartbeat_before
    rt.summary["heartbeat_after"] = heartbeat_after
    rt.summary["prepared_sql"] = prepared_sql
    rt.summary["verify_sql"] = verify_sql
    rt.summary["heartbeat_config_before"] = 'heartbeat_request "%s"' % heartbeat_before
    rt.summary["heartbeat_config_after"] = 'heartbeat_request "%s"' % heartbeat_after
    rt.summary["reload_conf_path"] = str(start_conf)
    rt.console_reload()
    reload_log = rt.logs_dir / "console_reload.log"
    if reload_log.exists():
        reload_text = reload_log.read_text(encoding="utf-8", errors="replace").strip()
        rt.summary["reload_result"] = reload_text or "<empty>"
    rt.record_step(
        "reload 后再次执行 prepared statement: %s" % verify_sql,
        note="验证重分类后再次执行会命中心跳拦截，并优先使用 cached bypass response",
    )
    fbasecman_window = rt.begin_fbasecman_log_window()
    pg_window = rt.begin_pg_log_window("heartbeat_reclassify")
    _, verify_output = _run_jdbc_asset_phased(
        rt,
        "GC_prepared_single_query.java",
        "GC_prepared_single_query",
        [verify_sql, "reload_sql124"],
        step_title="执行外置 heartbeat 重分类验证 driver",
        actions=[PhaseAction(
            "after_execute", "PHASE=AFTER_EXECUTE",
            title="reload 后 prepared SQL 执行完成后观察缓存",
            expected="reload 后 JDBC 连接保持；SELECT 124 的 global cache 分类和日志符合 heartbeat 语义。",
        )],
        sql_operations=[{"sql": verify_sql, "parameters": []}],
    )
    rt.summary["heartbeat_verify_output"] = verify_output
    verify_window_text, _ = rt.collect_fbasecman_log_window(fbasecman_window)
    pg_capture = rt.collect_pg_log_window(
        pg_window,
        "heartbeat_reclassify",
        patterns=[re.escape(verify_sql)],
    )
    rt.summary["verify_log_window"] = {
        "text": verify_window_text.strip(),
    }
    key_verify_lines = _summary_set_log_window(
        rt,
        verify_window_text,
        [
            "attached cached bypass response",
            "heartbeat response from cache",
            "global prepared statement cache hit",
            "rw(0) skip(3)",
        ],
        title="reload 后 prepared sql124 验证窗口关键日志",
    )
    _summary_set_pg_log_verify(rt, 0, len(pg_capture["lines"]), pg_capture["paths"])
    rt.summary["pg_log_verify"]["lines"] = pg_capture["lines"]
    rt.summary["pg_log_verify"]["window_log"] = pg_capture["logfile"]
    rt.record_step(
        "验证窗口内 fbasecman 关键日志",
        output="\n".join(key_verify_lines) if key_verify_lines else "<empty>",
        note="只保留 reload 后第二次 JDBC 验证期间与 global cache / cached bypass / heartbeat 直接相关的关键日志",
    )
    after_reload_state = rt.capture_console_state("after")
    rt.summary["after_stats"] = after_reload_state["stats"]
    _assert_heartbeat_reload_reclassifies_existing_normal_entry(rt, before_reload_state, after_reload_state)
    return after_reload_state


