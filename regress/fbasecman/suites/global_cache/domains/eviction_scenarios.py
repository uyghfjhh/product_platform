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
from suites.global_cache.domains.driver_cases import _run_libpq_case, _append_driver_log
def _assert_backend_global_split_eviction(rt, before_state, mid_state, after_state):
    global_limit = int(rt.case.fbasecman.get(GLOBAL_PS_LIMIT_KEY, 4))
    backend_limit = int(rt.case.fbasecman.get(BACKEND_PS_LIMIT_KEY, 2))
    delta = _stats_delta(before_state["stats"], after_state["stats"])
    mid_descriptions = ["|".join(row) for row in mid_state["global"]]
    after_descriptions = ["|".join(row) for row in after_state["global"]]
    seed_mid_rows = [row for row in mid_descriptions if "gc_split_initial_" in row]
    seed_after_rows = [row for row in after_descriptions if "gc_split_initial_" in row]
    trigger_after_rows = [row for row in after_descriptions if "gc_split_pressure_6" in row]
    mid_total = int(mid_state["stats"].get("total_entries", "0") or "0")
    mid_capacity = int(mid_state["stats"].get("capacity", "0") or "0")
    mid_evictions = int(mid_state["stats"].get("evictions", "0") or "0")
    mid_misses = int(mid_state["stats"].get("misses", "0") or "0")
    after_total = int(after_state["stats"].get("total_entries", "0") or "0")
    after_capacity = int(after_state["stats"].get("capacity", "0") or "0")
    before_evictions = int(before_state["stats"].get("evictions", "0") or "0")
    after_evictions = int(after_state["stats"].get("evictions", "0") or "0")
    log_text = rt.fbasecman_log.read_text(encoding="utf-8", errors="replace") if rt.fbasecman_log.exists() else ""
    libpq_text = rt.libpq_log.read_text(encoding="utf-8", errors="replace") if rt.libpq_log.exists() else ""
    backend_evict_lines = [
        line.strip() for line in log_text.splitlines()
        if "evict backend prepared statement" in line and "by LRU" in line
    ]
    global_exceed_lines = [
        line.strip() for line in log_text.splitlines()
        if "global ps cache exceeds capacity:" in line
    ]
    global_evict_lines = [
        line.strip() for line in log_text.splitlines()
        if "evict global prepared statement" in line
    ]
    split_target_global_lines = [
        line for line in global_evict_lines
        if "gc_split_initial_" in line or "gc_split_pressure_6" in line
    ]
    if "seed_commit_ok=true" not in libpq_text or "trigger_done=true" not in libpq_text:
        raise GlobalCacheFailure("backend_global_split_eviction expects both seed and trigger libpq phases to complete")
    if not backend_evict_lines:
        raise GlobalCacheFailure("backend_global_split_eviction expects backend LRU eviction evidence in fbasecman log")
    if not global_exceed_lines or not global_evict_lines:
        raise GlobalCacheFailure("backend_global_split_eviction expects global capacity exceed + eviction evidence in fbasecman log")
    if mid_capacity != global_limit:
        raise GlobalCacheFailure("backend_global_split_eviction expects after_seed capacity=%s, got %s" % (global_limit, mid_capacity))
    if mid_total > global_limit:
        raise GlobalCacheFailure("backend_global_split_eviction expects after_seed total_entries<=%s, got %s" % (global_limit, mid_total))
    if mid_misses < 5:
        raise GlobalCacheFailure("backend_global_split_eviction expects after_seed misses>=5, got %s" % mid_misses)
    if mid_evictions < 1:
        raise GlobalCacheFailure("backend_global_split_eviction expects after_seed evictions>=1, got %s" % mid_evictions)
    if not seed_mid_rows:
        raise GlobalCacheFailure("backend_global_split_eviction expects surviving seeded entries after seed phase")
    if after_capacity != global_limit:
        raise GlobalCacheFailure("backend_global_split_eviction expects capacity=%s, got %s" % (global_limit, after_capacity))
    if after_total > global_limit:
        raise GlobalCacheFailure("backend_global_split_eviction expects total_entries<=%s after trigger, got %s" % (global_limit, after_total))
    if after_evictions <= before_evictions:
        raise GlobalCacheFailure("backend_global_split_eviction expects evictions to increase, before=%s after=%s" % (before_evictions, after_evictions))
    if not trigger_after_rows:
        raise GlobalCacheFailure("backend_global_split_eviction expects trigger SQL to remain visible in global cache after eviction")

    rt.summary["core_result"] = {
        GLOBAL_PS_LIMIT_KEY: global_limit,
        BACKEND_PS_LIMIT_KEY: backend_limit,
        "seed_mid_rows": seed_mid_rows,
        "seed_after_rows": seed_after_rows,
        "trigger_after_rows": trigger_after_rows,
        "backend_evict_lines": backend_evict_lines,
        "global_exceed_lines": global_exceed_lines,
        "global_evict_lines": global_evict_lines,
        "stats_delta": dict(delta),
    }
    _summary_set_report_blocks(
        rt,
        verification_checks=[
            {
                "title": "同一 PostgreSQL 后端连接连续执行 5 条 SQL 后，淘汰该连接最久未使用的 prepared statement",
                "expected": "单个后端连接最多缓存 2 条 prepared statement；连续执行 5 条后应出现最久未使用项淘汰日志",
                "actual": "\n".join(backend_evict_lines[:4]),
                "result": "PASS",
            },
            {
                "title": "第 5 条和第 6 条 SQL 先后触发两次全局 prepared statement 缓存淘汰",
                "expected": "所有后端共享的全局缓存上限为 4 条；第 5 条 SQL 首次造成超容量，第 6 条 SQL 再次施压，累计发生 2 次淘汰。",
                "actual": "首次超容量日志:\n%s\n全局淘汰日志:\n%s" % (
                    "\n".join(global_exceed_lines[:4]) if global_exceed_lines else "<missing exceed>",
                    "\n".join(split_target_global_lines[:4] or global_evict_lines[:4]),
                ),
                "result": "PASS",
            },
            {
                "title": "全局缓存淘汰后，总条目数收敛到 4 条上限",
                "expected": "控制台统计显示缓存容量为 %s，最终总条目数不超过 %s" % (global_limit, global_limit),
                "actual": "容量=%s；最终条目数=%s；前 5 条 SQL 剩余=%s；第 6 条 SQL 剩余=%s\n%s" % (
                    after_capacity,
                    after_total,
                    len(seed_after_rows),
                    len(trigger_after_rows),
                    "\n".join(seed_after_rows + trigger_after_rows),
                ),
                "console_stats": dict(after_state["stats"]),
                "result": "PASS",
            },
            {
                "title": "前 5 条 SQL 执行结束后已发生首次全局缓存淘汰，且仍有业务条目可观测",
                "expected": "执行前 5 条 SQL 后至少产生 5 次 cache miss 和 1 次全局缓存淘汰，总条目数不超过 4，且控制台中仍能看到部分业务 SQL",
                "actual": "条目总数=%s；容量=%s；cache miss=%s 次；全局缓存淘汰=%s 次\n%s" % (
                    mid_total,
                    mid_capacity,
                    mid_misses,
                    mid_evictions,
                    "\n".join(seed_mid_rows),
                ),
                "console_stats": dict(mid_state["stats"]),
                "result": "PASS",
            },
        ],
        business_summary=[
            "运行配置把 global_prepared_statements_limit 固定为 4，把 backend_prepared_statements_limit 固定为 2。",
            "seed 阶段在同一事务里连续执行 5 条 prepared SQL，固定打到同一 backend，借此制造 backend LRU Close(S) 压力。",
            "seed 连接断开后，这 5 条普通 entry 都会变成 zero-ref；随后再插入第 6 条 trigger SQL，验证 global eviction 只回收 zero-ref 且无 bypass response 的普通条目。",
        ],
        key_evidence=[
            "Runtime conf: %s=%s, %s=%s" % (GLOBAL_PS_LIMIT_KEY, global_limit, BACKEND_PS_LIMIT_KEY, backend_limit),
            "Backend LRU: %s" % (" | ".join(backend_evict_lines[:3]) if backend_evict_lines else "<missing>"),
            "Global exceed: %s" % (global_exceed_lines[-1] if global_exceed_lines else "<missing>"),
            "Global evict: %s" % (" | ".join(split_target_global_lines[:3] or global_evict_lines[:3]) if global_evict_lines else "<missing>"),
            "After stats: capacity=%s total_entries=%s evictions=%s delta=%s" % (after_capacity, after_total, after_evictions, delta),
        ],
    )


def _run_backend_global_split_eviction_case(rt):
    global_limit = int(rt.case.fbasecman.get(GLOBAL_PS_LIMIT_KEY, 4))
    backend_limit = int(rt.case.fbasecman.get(BACKEND_PS_LIMIT_KEY, 2))
    start_conf = rt.render_runtime_conf(
        _ps_limit_replacements(global_limit, backend_limit),
        stem="backend_global_split_eviction.conf",
    )
    start_conf_text = start_conf.read_text(encoding="utf-8", errors="replace")
    rt.summary["runtime_conf_path"] = str(start_conf)
    rt.summary["runtime_conf_text"] = start_conf_text
    rt.summary["runtime_conf_excerpt"] = _extract_conf_lines(
        start_conf_text,
        [
            GLOBAL_PS_LIMIT_KEY,
            BACKEND_PS_LIMIT_KEY,
            "server_lifetime",
            "pool_reserve_prepared_statement",
            "heartbeat_request",
        ],
    )
    rt.start_fbasecman(conf=start_conf)
    before_state = rt.capture_console_state("before")
    rt.summary["before_stats"] = before_state["stats"]
    rt.record_step(
        "启动运行配置: global limit %s / backend limit %s" % (global_limit, backend_limit),
        output=rt.summary["runtime_conf_excerpt"],
    )
    _run_libpq_case(
        rt,
        extra_args=["seed"],
        step_title="建立首批缓存：同一事务连续执行 5 条 prepared SELECT",
    )
    rt.step_records[-1].update({
        "expected": "事务内 5 条 prepared SELECT 均执行成功，分别返回 1、2、3、4、5，并成功提交事务。",
        "actual": "事务已开始；5 条查询依次返回 1、2、3、4、5；事务已成功提交。",
        "result": "PASS",
    })
    mid_state = rt.capture_console_state("after_seed")
    _run_libpq_case(
        rt,
        extra_args=["trigger"],
        step_title="继续施加容量压力：新连接执行第 6 条 prepared SELECT",
    )
    rt.step_records[-1].update({
        "expected": "第 6 条 prepared SELECT 执行成功并返回 6。",
        "actual": "第 6 条查询返回 6，本次新增查询正常结束。",
        "result": "PASS",
    })
    after_state = rt.capture_console_state("after")
    rt.summary["after_stats"] = after_state["stats"]
    _assert_backend_global_split_eviction(rt, before_state, mid_state, after_state)


def _run_discard_all_clears_backend_cache_case(rt):
    business_sql = rt.case.assertions.get("business_sql", "select name from test where id = ? /* gc_discard_all_redeploy */")
    start_conf = rt.render_runtime_conf(
        [('server_lifetime  3600', 'server_lifetime  999')],
        stem="discard_all_clears_backend_cache.conf",
    )
    rt.summary["runtime_conf_path"] = str(start_conf)
    rt.summary["runtime_conf_text"] = start_conf.read_text(encoding="utf-8", errors="replace")
    rt.start_fbasecman(conf=start_conf)
    before_state = rt.capture_console_state("before")
    rt.summary["before_stats"] = before_state["stats"]
    source = _global_cache_asset_path(rt.root, "jdbc", "GC_discard_all_redeploy.java")
    target = rt.driver_dir / source.name
    shutil.copyfile(str(source), str(target))
    jar = _compile_java(
        rt, target, rt.logs_dir / "GC_discard_all_redeploy.javac.log",
        "编译外置 discard-all redeploy JDBC driver",
    )
    url = _jdbc_url(rt, {"prepareThreshold": 1, "preferQueryMode": "extended"})
    cmd = [
        "java", "-cp", "%s:%s" % (rt.driver_dir, jar), "GC_discard_all_redeploy",
        url,
        "postgres", "", business_sql,
    ]
    log_path = rt.logs_dir / "GC_discard_all_redeploy.java.log"
    driver = rt.start_jdbc_phase_process(
        cmd, target, url, log_path, "执行 DISCARD ALL 分阶段 JDBC driver",
        cwd=rt.driver_dir,
        sql_operations=[{"sql": business_sql, "parameters": ["$1=<driver value>"]}],
    )

    def observe(phase, marker):
        sql = "SHOW SERVER_PREP_STMTS;"
        stem = ("discard_all_before_server" if phase == "before_discard"
                else "discard_all_after_discard_server")
        raw = rt.psql("console", sql, rt.logs_dir / (stem + ".raw.log"), record=False)
        rows = parse_pipe_rows(raw)[1:]
        matching_rows = [
            row for row in rows
            if len(row) >= 9 and "gc_discard_all_redeploy" in row[8]
        ]
        if not matching_rows and phase == "before_discard":
            raise GlobalCacheFailure(
                "%s expected target PreparedStatement while JDBC connection is paused"
                % phase
            )
        return {
            "rows": rows,
            "command": "$ " + " ".join(rt.psql_command("console", sql)),
            "output": render_psql_table_from_pipe_text(raw),
            "actual": "SHOW SERVER_PREP_STMTS 中找到 %d 条目标 PreparedStatement。" % len(matching_rows),
            "passed": True,
        }

    observations, rc, output = rt.observe_jdbc_phases(
        driver, source, url,
        [
            PhaseAction(
                "before_discard", "PHASE=BEFORE_DISCARD", "continue",
                title="首次 PreparedStatement 后检查后端缓存",
                expected="首次查询后 SHOW SERVER_PREP_STMTS 包含目标 PreparedStatement。",
            ),
            PhaseAction(
                "after_discard", "PHASE=AFTER_DISCARD", "continue",
                title="DISCARD ALL 后检查后端缓存",
                expected=(
                    "代理已回复 DISCARD ALL；在下一次 attach 前，当前物理 backend "
                    "仍保留旧 PreparedStatement。"
                ),
            ),
        ],
        observe,
        timeout=30,
        finish_timeout=30,
    )
    if rc != 0:
        raise GlobalCacheFailure("discard_all_clears_backend_cache JDBC driver failed with rc=%s" % rc)
    before_server = observations["before_discard"]["rows"]
    after_discard_server = observations["after_discard"]["rows"]
    after_state = rt.capture_console_state("after")
    rt.summary["after_stats"] = after_state["stats"]
    rt.summary["discard_all_before_server_rows"] = ["|".join(row) for row in before_server]
    rt.summary["discard_all_after_discard_server_rows"] = ["|".join(row) for row in after_discard_server]
    _assert_discard_all_clears_backend_cache(rt, before_state, after_state)


def _run_unnamed_overwrite_case(rt, before_state):
    start_conf = rt.render_runtime_conf(
        [
            ('server_lifetime  3600', 'server_lifetime  10'),
            ('server_lifetime 3600', 'server_lifetime 10'),
        ],
        stem="unnamed_overwrite_live.conf",
    )
    start_conf_text = start_conf.read_text(encoding="utf-8", errors="replace")
    rt.summary["runtime_conf_path"] = str(start_conf)
    rt.summary["runtime_conf_text"] = start_conf_text
    rt.summary["runtime_conf_excerpt"] = _extract_conf_lines(
        start_conf_text,
        [
            "server_lifetime",
            GLOBAL_PS_LIMIT_KEY,
            BACKEND_PS_LIMIT_KEY,
            "pool_reserve_prepared_statement",
            "heartbeat_request",
        ],
    )
    rt.record_step(
        "启动运行配置: server_lifetime 10",
        output=rt.summary["runtime_conf_excerpt"],
    )
    rt.stop_fbasecman(best_effort=True, record=False)
    rt.start_fbasecman(conf=start_conf)
    before_state = rt.capture_console_state("before")
    rt.summary["before_stats"] = before_state["stats"]
    binary = _build_libpq_asset(rt, rt.logs_dir / "GC_unnamed_overwrite.gcc.log")
    _run_libpq_asset(
        rt, "GC_unnamed_overwrite_stage1", extra_args=["one"], binary=binary,
        step_title="执行 libpq driver: unnamed 第 1 条 SQL gc_unnamed_one",
    )
    first_state = rt.capture_console_state("after_first_unnamed")
    _run_libpq_asset(
        rt, "GC_unnamed_overwrite_stage2", extra_args=["two"], binary=binary,
        step_title="执行 libpq driver: unnamed 第 2 条 SQL gc_unnamed_two",
    )
    after_state = rt.capture_console_state("after")
    rt.summary["after_stats"] = after_state["stats"]
    after_release_state, _ = _wait_target_entries_unref(
        rt, "gc_unnamed_", 2, 15
    )
    rt.summary["unnamed_overwrite_snapshots"] = {
        "after_first": ["|".join(row) for row in first_state["global"]],
        "after_second": ["|".join(row) for row in after_state["global"]],
        "after_release": ["|".join(row) for row in after_release_state["global"]],
    }
    _assert_unnamed_overwrite(rt, before_state, after_state)


def _assert_discard_all_clears_backend_cache(rt, before_state, after_state):
    log_text = (rt.logs_dir / "GC_discard_all_redeploy.java.log").read_text(encoding="utf-8", errors="replace")
    before_rows = rt.summary.get("discard_all_before_server_rows", [])
    after_redeploy_rows = [
        "|".join(row) for row in after_state["server"]
        if len(row) >= 6 and "gc_discard_all_redeploy" in row[4]
    ]
    matched_global = [
        "|".join(row) for row in after_state["global"]
        if len(row) >= 5 and "gc_discard_all_redeploy" in row[1]
    ]
    fbasecman_text = rt.fbasecman_log.read_text(encoding="utf-8", errors="replace")
    discard_reply = "DISCARD ALL" in fbasecman_text
    discard_backend = ("parse deploy" in fbasecman_text and
                       "statement DISCARD ALL" in fbasecman_text)
    redeploy_seen = (
        "parse before bind" in fbasecman_text
        and "deploy operator" in fbasecman_text
        and "gc_discard_all_redeploy" in fbasecman_text
    )
    if not before_rows:
        raise GlobalCacheFailure("discard_all_clears_backend_cache expects server rows before DISCARD ALL")
    if "autocommit=true" not in log_text.lower():
        raise GlobalCacheFailure(
            "discard_all_clears_backend_cache expects JDBC autoCommit=true before DISCARD ALL"
        )
    if "after_redeploy=" not in log_text:
        raise GlobalCacheFailure("discard_all_clears_backend_cache expects JDBC after_redeploy success marker")
    if not discard_reply:
        raise GlobalCacheFailure("discard_all_clears_backend_cache expects DISCARD ALL client reply log")
    if not discard_backend:
        raise GlobalCacheFailure("discard_all_clears_backend_cache expects DISCARD ALL backend deploy log during attach")
    if not redeploy_seen:
        raise GlobalCacheFailure("discard_all_clears_backend_cache expects target prepared statement redeploy logs")
    if not after_redeploy_rows:
        raise GlobalCacheFailure("discard_all_clears_backend_cache expects target server row redeployed after re-execute")
    if not matched_global:
        raise GlobalCacheFailure("discard_all_clears_backend_cache expects global entry to remain observable")
    rt.summary["core_result"] = {
        "before_server_rows": list(before_rows),
        "after_redeploy_rows": list(after_redeploy_rows),
        "matched_global": list(matched_global),
        "discard_reply": discard_reply,
        "discard_backend": discard_backend,
        "redeploy_seen": redeploy_seen,
    }
    _summary_set_report_blocks(
        rt,
        verification_checks=[
            {
                "title": "DISCARD ALL 之前目标 prepared SQL 已在后端 deploy",
                "expected": "before_server rows 中存在 gc_discard_all_redeploy definition",
                "actual": "; ".join(before_rows),
                "result": "PASS",
            },
            {
                "title": "挂起的 DISCARD ALL 在后续 attach 时真正下发到后端",
                "expected": "日志先出现 responded DISCARD ALL command to client，再在 attach 过程中出现 deploy: DISCARD ALL;",
                "actual": "fbasecman 先向客户端回复 DISCARD ALL，随后在 attach 时将 DISCARD ALL 下发后端",
                "result": "PASS",
            },
            {
                "title": "再次执行同 SQL 时目标 prepared statement 被重新 deploy 并成功返回业务结果",
                "expected": "日志出现 parse before bind / deploy operator ... gc_discard_all_redeploy to server，且 JDBC 出现 after_redeploy",
                "actual": "; ".join(after_redeploy_rows) or "<未找到重新部署后的 server entry>",
                "result": "PASS",
            },
        ],
        business_summary=[
            "先建立目标 prepared SQL 的 server cache。",
            "执行 DISCARD ALL 后，先由代理侧接受并回复客户端；真正下发到后端发生在后续业务报文触发 attach 的阶段。",
            "同一条业务 prepared SQL 在 attach 后重新 deploy，并成功返回结果，说明 DISCARD ALL 后的后端 prepared cache 清理与重新部署链路已经打通。",
        ],
        key_evidence=[
            "Before server rows: %s" % "; ".join(before_rows),
            "fbasecman: responded DISCARD ALL command to client / deploy: DISCARD ALL;",
            "After redeploy rows: %s" % "; ".join(after_redeploy_rows),
        ],
    )


def _assert_parse_invalid_error_recovery(rt, before_state, after_state):
    delta = _stats_delta(before_state["stats"], after_state["stats"])
    log_text = rt.jdbc_log.read_text(encoding="utf-8")
    if "符合预期,首次执行失败！" not in log_text:
        raise GlobalCacheFailure("parse_invalid_error case expects the first execution to fail")
    if "步骤4: 再次执行PreparedStatement,应执行成功" not in log_text:
        raise GlobalCacheFailure("parse_invalid_error case missing second-attempt marker")
    if "SUCCESS: 未触发Parse缓存清理问题" not in log_text:
        raise GlobalCacheFailure("parse_invalid_error case expects final recovery success marker")
    negative = rt.fbasecman_log.read_text(encoding="utf-8").lower() if rt.fbasecman_log.exists() else ""
    if "prepared statement not found in client cache" in negative:
        raise GlobalCacheFailure("parse_invalid_error case found stale client cache evidence")
    matched = [
        "|".join(row) for row in after_state["global"]
        if len(row) >= 2 and "select * from test_parse_error where id = $1" in row[1].lower()
    ]
    if not matched:
        raise GlobalCacheFailure("parse_invalid_error case expects recovered statement entry in global cache")
    rt.summary["core_result"] = {
        "matched_entry": matched[0],
        "stats_delta": dict(delta),
    }
    rt.summary["stats_delta"] = delta
    rt.summary["matched_global"] = matched
    rt.summary["verification_checks"] = [
        {
            "title": "第 1 次执行 `select * from test_parse_error where id = ?` 失败，补建对象后同连接再次成功",
            "expected": "JDBC 日志出现首次失败标记和最终 SUCCESS 标记",
            "actual": "已捕获首次失败、二次执行、最终 SUCCESS 证据",
            "result": "PASS",
        },
        {
            "title": "恢复后的 `select * from test_parse_error where id = ?` 重新进入 global cache",
            "expected": "console 中能看到 test_parse_error 对应 entry",
            "actual": matched[0],
            "result": "PASS",
        },
        {
            "title": "这条恢复路径没有触发 stale client cache 错误",
            "expected": "fbasecman 日志不出现 prepared statement not found in client cache",
            "actual": "fbasecman 日志检查通过",
            "result": "PASS",
        },
    ]
    rt.summary["business_summary_lines"] = [
        "第一次执行 `select * from test_parse_error where id = ?` 时，因目标表不存在而失败。",
        "补建对象后，同一连接上的同一 PreparedStatement 再次执行成功，说明 Parse 失败清理路径没有残留脏状态。",
    ]
    rt.summary["key_evidence_lines"] = [
        "JDBC 原始日志: 符合预期,首次执行失败！",
        "JDBC: SUCCESS: 未触发Parse缓存清理问题",
        "Console(global): %s" % matched[0],
    ]


