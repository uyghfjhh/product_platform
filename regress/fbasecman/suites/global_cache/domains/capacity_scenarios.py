"""Capacity scenario orchestration."""

from framework.execution.phased_process import PhaseAction, observe_phases
from lib.report_utils import render_psql_expanded_from_pipe_text
from suites.global_cache.domains.capacity import (
    ps_limit_conf_keys as _ps_limit_conf_keys,
    ps_limit_replacements as _ps_limit_replacements,
    seed_capacity_entries as _seed_capacity_entries,
)
from suites.global_cache.domains.capacity_checks import (
    assert_capacity_eviction_zero_ref,
    assert_capacity_mixed_bypass_response_and_zero_ref_shortage,
    assert_ref_count_protects_active_entries,
)
from suites.global_cache.drivers import (
    run_prepared_sequence as _driver_run_prepared_sequence,
    start_phased_prepared as _start_phased_prepared,
)
from suites.global_cache.manifest import BACKEND_PS_LIMIT_KEY, GLOBAL_PS_LIMIT_KEY
from suites.global_cache.errors import GlobalCacheFailure
from products.fbasecman.config import extract_config_lines as _extract_conf_lines
from framework.configuration.reload import config_lines_by_keys as _conf_lines_by_keys
from suites.global_cache.waits import (
    wait_target_entries_released,
    wait_target_entries_unref,
)


def run_capacity_eviction_zero_ref_case(rt):
    capacity_limit = int(rt.case.reload.get("capacity_limit", 3))
    seed_count = int(rt.case.reload.get("seed_count", 3))
    server_lifetime = int(rt.case.reload.get("server_lifetime", 10))
    prefix = "gc_capacity_zero_ref"
    start_conf = rt.render_runtime_conf(
        _ps_limit_replacements(capacity_limit)
        + [
            ('server_lifetime  3600', 'server_lifetime  %s' % server_lifetime),
            ('server_lifetime 3600', 'server_lifetime %s' % server_lifetime),
        ],
        stem="capacity_zero_ref.conf",
    )
    rt.summary["runtime_conf_path"] = str(start_conf)
    rt.summary["runtime_conf_text"] = start_conf.read_text(encoding="utf-8", errors="replace")
    rt.summary["runtime_conf_excerpt"] = _extract_conf_lines(
        rt.summary["runtime_conf_text"],
        [
            "enable_guc_sync",
            "heartbeat_request",
            GLOBAL_PS_LIMIT_KEY,
            BACKEND_PS_LIMIT_KEY,
            "server_lifetime",
            "pool_reserve_prepared_statement",
        ],
    )
    rt.start_fbasecman(conf=start_conf)
    before_state = rt.capture_console_state("before")
    rt.summary["before_stats"] = before_state["stats"]
    rt.record_step(
        "运行配置: global_prepared_statements_limit 3, server_lifetime %s" % server_lifetime,
        output="conf : %s\n%s" % (
            start_conf,
            "\n".join(_conf_lines_by_keys(rt.summary["runtime_conf_text"], _ps_limit_conf_keys() + ["server_lifetime"])),
        ),
    )
    active_sql = "select name from test where id = ? /* %s_hold_01 */" % prefix
    active_process, active_command, active_logfile = _start_phased_prepared(
        rt,
        [("hold_value", active_sql, 1)],
        log_stem="capacity_eviction_zero_ref_active",
    )
    try:
        active_process.wait_for("PHASE=READY", timeout=20)
    except Exception:
        active_process.terminate()
        raise
    active_state = rt.capture_console_state("after_hold")
    rt.summary["capacity_active_rows"] = [
        "|".join(row)
        for row in active_state["global"]
        if len(row) >= 2 and "%s_hold_01" % prefix in row[1]
    ]
    for index in range(1, seed_count + 1):
        _seed_capacity_entries(rt, 1, "%s_%02d" % (prefix, index))
    rt.record_step(
        "等待 seed entries 的 server 引用释放",
        output="短连接 SQL 2/3/4 已执行完成，等待 server_lifetime=%s 秒，让 3 条 gc_capacity_zero_ref_%02d~%02d 从 ref_count=1 降到 ref_count=0；长连接 SQL 1 继续保持连接不释放。"
        % (server_lifetime, 1, seed_count),
    )
    zero_ref_state, _ = wait_target_entries_unref(
        # A zero-ref entry may be reclaimed as soon as capacity pressure
        # exists.  The product contract requires an observable candidate pool,
        # not preservation of every seeded entry.
        rt, "%s_0" % prefix, max(1, capacity_limit - 1), server_lifetime + 10
    )
    rt.summary["capacity_zero_ref_ready_entries"] = [
        "|".join(row)
        for row in zero_ref_state["global"]
        if len(row) >= 2 and prefix in row[1]
    ]
    _seed_capacity_entries(rt, 1, "%s_trigger_a" % prefix)
    trigger_a_state = rt.capture_console_state("after_trigger_a")
    trigger_a_total = int(trigger_a_state["stats"].get("total_entries", "0") or "0")
    trigger_a_unref = int(trigger_a_state["stats"].get("unreferenced_entries", "0") or "0")
    rt.summary["capacity_trigger_a_entries"] = [
        "|".join(row)
        for row in trigger_a_state["global"]
        if len(row) >= 2 and prefix in row[1]
    ]
    if trigger_a_total > capacity_limit and trigger_a_unref > 0:
        rt.record_step(
            "trigger_a 检查失败",
            output=(
                "after_trigger_a 出现 total_entries=%s > capacity=%s，且 unreferenced_entries=%s，"
                "说明明明存在可淘汰 zero-ref 条目，但缓存没有及时收敛。\n"
                "after_trigger_a global:\n%s"
            ) % (
                trigger_a_total,
                capacity_limit,
                trigger_a_unref,
                render_psql_expanded_from_pipe_text(
                    "global_name|description|sql_class|has_bypass_response|ref_count\n" +
                    "\n".join("|".join(row) for row in trigger_a_state["global"])
                ) if trigger_a_state["global"] else "<empty>",
            ),
        )
        active_process.terminate()
        raise GlobalCacheFailure(
            "capacity_eviction_zero_ref trigger_a snapshot still exceeds capacity: total=%s capacity=%s unreferenced=%s"
            % (trigger_a_total, capacity_limit, trigger_a_unref)
        )
    _seed_capacity_entries(rt, 1, "%s_trigger_b" % prefix)
    trigger_b_state = rt.capture_console_state("after_trigger_b")
    trigger_b_total = int(trigger_b_state["stats"].get("total_entries", "0") or "0")
    trigger_b_unref = int(trigger_b_state["stats"].get("unreferenced_entries", "0") or "0")
    if trigger_b_total > capacity_limit and trigger_b_unref > 0:
        rt.record_step(
            "trigger_b 检查失败",
            output=(
                "after_trigger_b 出现 total_entries=%s > capacity=%s，且 unreferenced_entries=%s，"
                "说明明明存在可淘汰 zero-ref 条目，但缓存没有及时收敛。\n"
                "after_trigger_b global:\n%s"
            ) % (
                trigger_b_total,
                capacity_limit,
                trigger_b_unref,
                render_psql_expanded_from_pipe_text(
                    "global_name|description|sql_class|has_bypass_response|ref_count\n" +
                    "\n".join("|".join(row) for row in trigger_b_state["global"])
                ) if trigger_b_state["global"] else "<empty>",
            ),
        )
        active_process.terminate()
        raise GlobalCacheFailure(
            "capacity_eviction_zero_ref trigger_b snapshot still exceeds capacity: total=%s capacity=%s unreferenced=%s"
            % (trigger_b_total, capacity_limit, trigger_b_unref)
        )
    active_process.resume("continue")
    rc, active_output = active_process.finish(timeout=20)
    rt.summary["capacity_active_output"] = active_output
    if rc != 0:
        raise GlobalCacheFailure("capacity active-long-conn JDBC driver failed with rc=%s" % rc)
    after_state, _ = wait_target_entries_unref(
        rt, "%s_hold_01" % prefix, 1, server_lifetime + 10
    )
    rt.summary["after_stats"] = after_state["stats"]
    rt.summary["capacity_zero_ref_hold_sql"] = active_sql
    rt.summary["capacity_zero_ref_seed_sqls"] = [
        "select name from test where id = ? /* %s_%02d_01 */" % (prefix, index)
        for index in range(1, seed_count + 1)
    ]
    rt.summary["capacity_zero_ref_trigger_sql_a"] = "select name from test where id = ? /* %s_trigger_a_01 */" % prefix
    rt.summary["capacity_zero_ref_trigger_sql_b"] = "select name from test where id = ? /* %s_trigger_b_01 */" % prefix
    rt.summary["capacity_zero_ref_snapshots"] = {
        "after_hold": ["|".join(row) for row in active_state["global"]],
        "zero_ref_ready": ["|".join(row) for row in zero_ref_state["global"]],
        "after_trigger_a": ["|".join(row) for row in trigger_a_state["global"]],
        "after_trigger_b": ["|".join(row) for row in trigger_b_state["global"]],
        "after": ["|".join(row) for row in after_state["global"]],
    }
    assert_capacity_eviction_zero_ref(rt, before_state, active_state, zero_ref_state, trigger_a_state, trigger_b_state, after_state)


def run_capacity_mixed_bypass_response_and_zero_ref_shortage_case(rt):
    capacity_limit = int(rt.case.reload.get("capacity_limit", 3))
    zero_ref_count = int(rt.case.reload.get("zero_ref_count", 4))
    server_lifetime = int(rt.case.reload.get("server_lifetime", 10))
    heartbeat_sql = rt.case.assertions.get("heartbeat_sql", "SELECT 124")
    guc_report_sql = rt.case.assertions.get("guc_report_sql", "SET application_name = 'gc_capacity_fused_report'")
    discard_all_sql = rt.case.assertions.get("discard_all_sql", "DISCARD ALL")
    pressure_prefix = rt.case.assertions.get("pressure_prefix", "gc_capacity_fused_zero_ref")
    active_trigger_sql = rt.case.assertions.get("active_trigger_sql", "select name from test where id = ? /* gc_capacity_fused_trigger_active */")
    start_conf = rt.render_runtime_conf(
        _ps_limit_replacements(capacity_limit)
        + [
            ('heartbeat_request "select 10086"', 'heartbeat_request "select 124"'),
            ('server_lifetime  3600', 'server_lifetime  %s' % server_lifetime),
            ('server_lifetime 3600', 'server_lifetime %s' % server_lifetime),
        ],
        stem="capacity_mixed_bypass_response_and_zero_ref_shortage.conf",
    )
    rt.summary["runtime_conf_path"] = str(start_conf)
    rt.summary["runtime_conf_text"] = start_conf.read_text(encoding="utf-8", errors="replace")
    rt.start_fbasecman(conf=start_conf)
    before_state = rt.capture_console_state("before")
    rt.summary["before_stats"] = before_state["stats"]

    _driver_run_prepared_sequence(
        rt,
        [("fused_heartbeat", "execute", heartbeat_sql)],
        log_stem="capacity_fused_heartbeat",
    )
    rt.summary["capacity_heartbeat_output"] = rt.summary.get("jdbc_sequence_output", "")
    heartbeat_state = rt.capture_console_state("after_heartbeat", include_server=False)

    _driver_run_prepared_sequence(
        rt,
        [("fused_guc_report", "execute", guc_report_sql)],
        log_stem="capacity_fused_guc_report",
    )
    rt.summary["capacity_guc_report_output"] = rt.summary.get("jdbc_sequence_output", "")
    guc_report_state = rt.capture_console_state("after_guc_report", include_server=False)

    _driver_run_prepared_sequence(
        rt,
        [("fused_discard", "execute", discard_all_sql)],
        log_stem="capacity_fused_discard",
    )
    rt.summary["capacity_discard_output"] = rt.summary.get("jdbc_sequence_output", "")
    discard_state = rt.capture_console_state("after_discard_all", include_server=False)

    for index in range(1, zero_ref_count + 1):
        _seed_capacity_entries(rt, 1, "%s_%02d" % (pressure_prefix, index))
    rt.record_step("等待 zero-ref ready", output="等待 server_lifetime=%s 秒，让 4 条普通 zero-ref SQL 与 DISCARD ALL 一起进入可淘汰集合；随后再发 1 条 active trigger SQL。" % server_lifetime)
    zero_ref_ready_state, _ = wait_target_entries_released(
        rt, "%s_0" % pressure_prefix, server_lifetime + 10
    )

    trigger_process, trigger_command, trigger_logfile = _start_phased_prepared(
        rt,
        [("fused_active_trigger_value", active_trigger_sql, 1)],
        log_stem="capacity_fused_active_trigger",
    )
    try:
        trigger_process.wait_for("PHASE=READY", timeout=20)
        after_state = rt.capture_console_state("after", include_server=False)
        trigger_process.resume("continue")
        trigger_rc, trigger_output = trigger_process.finish(timeout=20)
    except Exception:
        trigger_process.terminate()
        raise
    rt.summary["capacity_trigger_output"] = trigger_output
    if trigger_rc != 0:
        raise GlobalCacheFailure("capacity fused active trigger failed with rc=%s" % trigger_rc)
    rt.summary["after_stats"] = after_state["stats"]
    rt.summary["fused_bypass_shortage_snapshots"] = {
        "after_heartbeat": ["|".join(row) for row in heartbeat_state["global"]],
        "after_guc_report": ["|".join(row) for row in guc_report_state["global"]],
        "after_discard_all": ["|".join(row) for row in discard_state["global"]],
        "zero_ref_ready": ["|".join(row) for row in zero_ref_ready_state["global"]],
        "after": ["|".join(row) for row in after_state["global"]],
    }
    assert_capacity_mixed_bypass_response_and_zero_ref_shortage(rt, before_state, heartbeat_state, guc_report_state, discard_state, zero_ref_ready_state, after_state)


def run_ref_count_protects_active_entries_case(rt):
    capacity_limit = int(rt.case.reload.get("capacity_limit", 2))
    active_count = int(rt.case.reload.get("active_count", 4))
    prefix = "gc_capacity_active_ref"
    start_conf = rt.render_runtime_conf(
        _ps_limit_replacements(capacity_limit),
        stem="capacity_active_ref.conf",
    )
    rt.summary["runtime_conf_path"] = str(start_conf)
    rt.summary["runtime_conf_text"] = start_conf.read_text(encoding="utf-8", errors="replace")
    rt.start_fbasecman(conf=start_conf)
    before_state = rt.capture_console_state("before")
    rt.summary["before_stats"] = before_state["stats"]

    operations = [
        (
            "active_%02d" % index,
            "select name from test where id = ? /* %s_%02d */" % (prefix, index),
            index,
        )
        for index in range(1, active_count + 1)
    ]
    process, command, logfile = _start_phased_prepared(
        rt, operations, log_stem="ref_count_protects_active_entries"
    )
    observations, rc, output = observe_phases(
        process,
        [PhaseAction("active_entries_ready", "PHASE=READY")],
        lambda _name, _marker: rt.capture_console_state("after"),
        timeout=20,
        finish_timeout=20,
    )
    after_state = observations["active_entries_ready"]
    rt.summary["after_stats"] = after_state["stats"]
    rt.summary["phased_prepared_output"] = output
    if rc != 0:
        raise GlobalCacheFailure(
            "ref_count_protects_active_entries JDBC driver failed with rc=%s" % rc
        )
    assert_ref_count_protects_active_entries(rt, before_state, after_state)
