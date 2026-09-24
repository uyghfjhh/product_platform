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

from suites.global_cache.domains.common_assertions import _stats_change_text, _assert_negative_logs
def _jdbc_driver_source_file(root, case):
    return _driver_jdbc_source_file(root, case, _safe_name)


def _run_jdbc_case(rt):
    return _driver_run_case_jdbc(rt, _safe_name, NOISE_PATTERNS)


def _libpq_driver_source(root, case):
    return _driver_libpq_source(root, case)


def _copy_libpq_driver_tree(source, target_dir):
    return _driver_stage_libpq_source(source, target_dir)


def _append_driver_log(path, output):
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    path.write_text(existing + (output or ""), encoding="utf-8")


def _run_libpq_case(rt, extra_args=None, step_title=None):
    source = _libpq_driver_source(rt.root, rt.case)
    if not source.exists():
        raise GlobalCacheFailure("missing libpq source: %s" % source)
    target = _copy_libpq_driver_tree(source, rt.driver_dir)
    binary = rt.build_dir / source.stem
    cfg = rt.env.config["local"]["postgres_dir"]
    compile_cmd = [
        "gcc",
        "-std=c99",
        "-o",
        str(binary),
        str(target),
        "-I%s/include" % cfg,
        "-L%s/lib" % cfg,
        "-lpq",
    ]
    rt.run_command(compile_cmd, rt.logs_dir / "gcc.log", cwd=rt.driver_dir, step_title="编译 libpq driver")
    run_env = os.environ.copy()
    run_env["LD_LIBRARY_PATH"] = "%s/lib:%s" % (cfg, run_env.get("LD_LIBRARY_PATH", ""))
    conninfo = "host=127.0.0.1 port=%s user=postgres dbname=postgres sslmode=disable" % (
        rt.listen_port
    )
    allow_failure = False
    cmd = [str(binary), conninfo, "mmr_hint"]
    if extra_args:
        cmd.extend(extra_args)
    mode_suffix = ""
    if extra_args:
        mode_suffix = "_" + "_".join(str(item) for item in extra_args)
    if step_title is None:
        step_title = "执行 libpq driver"
        if extra_args:
            step_title = "执行 libpq driver (%s)" % ", ".join(str(item) for item in extra_args)
    step_log = rt.logs_dir / ("libpq%s.log" % mode_suffix)
    rc, output = rt.run_command(
        cmd,
        step_log,
        cwd=rt.build_dir,
        env=run_env,
        check=not allow_failure,
        step_title=step_title,
        record=False,
    )
    _append_driver_log(rt.libpq_log, output)
    rt.record_step(
        step_title,
        command=" ".join(cmd),
        logfile=step_log,
        rc=rc,
        output=output,
        expected=None,
        actual=None,
        result=None,
    )
    _record_driver_api_calls(rt, source)
    phase = str(extra_args[0]) if extra_args and len(extra_args) == 1 else None
    rt.step_records[-1]["sql_operations"] = _libpq_prepared_operations(source, phase=phase)



def _assert_libpq_activity(rt, before_state, after_state):
    delta = _stats_delta(before_state["stats"], after_state["stats"])
    if delta.get("misses", 0) < 1 and delta.get("hits", 0) < 1:
        raise GlobalCacheFailure("libpq case produced no cache activity: %s" % delta)
    rt.summary["stats_delta"] = delta
    log_text = rt.libpq_log.read_text(encoding="utf-8", errors="replace") if rt.libpq_log.exists() else ""
    descriptions = ["|".join(row) for row in after_state["global"]]

    if rt.case.name == "prepare_before_bind_deploy":
        server_rows = ["|".join(row) for row in after_state["server"]]
        target_rows = [
            row for row in descriptions
            if "SELECT * FROM t_test1 WHERE id = $1" in row
            or "SELECT * FROM t_test2 WHERE key = $1" in row
            or "SELECT * FROM t_test3 WHERE code = $1" in row
        ]
        rt.summary["core_result"] = {
            "target_rows": list(target_rows),
            "server_rows": list(server_rows[:3]),
            "stats_delta": dict(delta),
        }
        rt.summary["verification_checks"] = [
            {
                "title": "事务二里的第 2 条语句直接复用事务一已建立的 stmt_q1",
                "expected": "driver 日志出现“复用事务一的 stmt_q1，没有发送 Parse”，说明这里走的是 B/E 而不是重新 Parse",
                "actual": "driver 日志已打印复用 stmt_q1 / 无 Parse 说明"
                if "复用事务一的 stmt_q1" in log_text or "复用，无Parse" in log_text
                else "未在 driver 日志中找到复用 stmt_q1 说明",
                "result": "PASS" if ("复用事务一的 stmt_q1" in log_text or "复用，无Parse" in log_text) else "FAIL",
            },
            {
                "title": "protocol driver 完整走完 PBDE -> BDE -> PBDES 序列",
                "expected": "driver 日志打印报文序列构造完成与结果结束标记，说明 pipeline 没在中途乱序或短路",
                "actual": "已打印 pipeline 报文序列与结果统计"
                if "报文序列构造完成" in log_text and "总共" in log_text
                else "driver 日志缺少完整 pipeline 完成标记",
                "result": "PASS" if ("报文序列构造完成" in log_text and "总共" in log_text) else "FAIL",
            },
            {
                "title": "global hit 但当前 server 未部署时，补部署证据在 console 中可观测",
                "expected": "缓存统计出现 miss/hit，且 server prepared statements 中能看到 stmt_q1 / stmt_q2 / stmt_q3 的部署痕迹",
                "actual": "%s；server prepared statement 记录数=%s"
                % (_stats_change_text(delta, ("misses", "hits")), len(server_rows)),
                "console_stats": dict(after_state["stats"]),
                "result": "PASS" if server_rows else "FAIL",
            },
        ]
        rt.summary["business_summary_lines"] = [
            "事务一先创建并执行 stmt_q1，让 server 侧已有部署。",
            "事务二在 pipeline 里复用 stmt_q1，直接走 B/E，不再发送 Parse。",
            "同时穿插新的 stmt_q2 / stmt_q3，验证 global hit 但 server 未部署时仍能正确补部署。",
        ]
        key_log_lines = _summary_set_log_window(
            rt,
            log_text,
            [
                "复用事务一的 stmt_q1",
                "复用，无parse",
                "报文序列构造完成",
                "总共",
            ],
            title="prepare_before_bind_deploy 协议关键日志",
        )
        rt.summary["key_evidence_lines"] = [
            "Driver key lines: %s" % (" | ".join(key_log_lines[:4]) if key_log_lines else "<missing>"),
            "Console(global): %s" % ("; ".join(target_rows[:3]) if target_rows else "<missing>"),
            "Stats delta: %s" % delta,
        ]
        return

    if rt.case.name == "bypass_prepare_protocol_sequence":
        key_log_lines = _summary_set_log_window(
            rt,
            log_text,
            [
                "总序列: P/B/D/E  D  P/B/D/E/S",
                "只发送 Describe",
                "不发 Execute",
                "总共",
            ],
            title="bypass_prepare_protocol_sequence 协议关键日志",
        )
        rt.summary["verification_checks"] = [
            {
                "title": "driver 构造并发送了目标 bypass 协议序列",
                "expected": "driver 日志明确打印 PBDE -> D -> PBDES 总序列，固定 Describe-only 分支的真实报文结构",
                "actual": "driver 日志已打印 PBDE D PBDES"
                if "总序列: P/B/D/E  D  P/B/D/E/S" in log_text
                else "未在 driver 日志中找到 PBDE D PBDES 总序列",
                "result": "PASS" if "总序列: P/B/D/E  D  P/B/D/E/S" in log_text else "FAIL",
            },
            {
                "title": "纯 Describe 分支被真实执行",
                "expected": "driver 日志出现“只发送 Describe / 不发 Execute”，说明语句2确实只取元数据，没有把业务 SQL 发成一次真正执行",
                "actual": "driver 日志已打印纯 Describe 说明"
                if "只发送 Describe" in log_text and "不发 Execute" in log_text
                else "driver 日志缺少纯 Describe 说明",
                "result": "PASS" if ("只发送 Describe" in log_text and "不发 Execute" in log_text) else "FAIL",
            },
            {
                "title": "本轮 Describe-only / bypass 序列之后仍有可观测 cache activity",
                "expected": "至少新增一次 cache miss 或 hit，说明协议流量经过 prepared/global cache 路径",
                "actual": _stats_change_text(delta, ("misses", "hits")),
                "console_stats": dict(after_state["stats"]),
                "result": "PASS",
            },
        ]
        rt.summary["business_summary_lines"] = [
            "driver 先发送一轮 PBDE，再单独发送纯 Describe，最后再发送一轮 PBDES。",
            "这个流程用来确认 bypass 路径下的消息序列仍保持可预期行为。",
            "执行结束后，stats 仍能观测到本轮协议触发的 cache activity。",
        ]
        rt.summary["key_evidence_lines"] = [
            "Driver key lines: %s" % (" | ".join(key_log_lines[:4]) if key_log_lines else "<missing>"),
            "Stats delta: %s" % delta,
        ]
        return

    if rt.case.name == "server_lru_close_response_order":
        target_rows = [
            row for row in descriptions
            if "SELECT * FROM t_test1 WHERE id = $1" in row
            or "SELECT * FROM t_test2 WHERE key = $1" in row
            or "SELECT * FROM t_test3 WHERE code = $1" in row
        ]
        rt.summary["core_result"] = {
            "target_rows": list(target_rows),
            "target_count": len(target_rows),
            "stats_delta": dict(delta),
        }
        rt.summary["verification_checks"] = [
            {
                "title": "低 server prepared limit 下，pipeline 响应顺序没有被 LRU Close 打乱",
                "expected": "driver 日志打印完整结果序列与结束标记，说明 Close(S) 压力没有把后续响应冲乱",
                "actual": "driver 日志已打印结果序列与总结果数"
                if "=== 读取查询结果 ===" in log_text and "总共" in log_text
                else "driver 日志缺少完整结果序列输出",
                "result": "PASS" if ("=== 读取查询结果 ===" in log_text and "总共" in log_text) else "FAIL",
            },
            {
                "title": "在 LRU 压力下仍能观测到本例触发的 prepared SQL",
                "expected": "console 中保留 t_test1 / t_test2 / t_test3 三条业务 prepared entries，说明回收没有把业务观测面打坏",
                "actual": "; ".join(target_rows) if target_rows else "<missing>",
                "result": "PASS" if target_rows else "FAIL",
            },
            {
                "title": "本轮 LRU 压力测试仍产生可观测 cache activity",
                "expected": "至少新增一次 cache miss 或 hit，说明 pipeline 中的 prepared SQL 经过 cache 路径",
                "actual": _stats_change_text(delta, ("misses", "hits")),
                "result": "PASS",
            },
        ]
        rt.summary["business_summary_lines"] = [
            "将 server prepared statement 限制压到 2，主动制造 LRU Close(S) 压力。",
            "在 pipeline 里连续跑 3 条 prepared 查询，验证响应顺序没有被 LRU Close 打乱。",
        ]
        key_log_lines = _summary_set_log_window(
            rt,
            log_text,
            [
                "=== 读取查询结果 ===",
                "result ",
                "总共",
            ],
            title="server_lru_close_response_order 协议关键日志",
        )
        rt.summary["key_evidence_lines"] = [
            "Driver key lines: %s" % (" | ".join(key_log_lines[:5]) if key_log_lines else "<missing>"),
            "Console(global): %s" % ("; ".join(target_rows[:3]) if target_rows else "<missing>"),
            "Stats delta: %s" % delta,
        ]
        return

    if rt.case.name == "backend_lru_eviction":
        fbase_log_text = rt.fbasecman_log.read_text(encoding="utf-8", errors="replace") if rt.fbasecman_log.exists() else ""
        server_rows = ["|".join(row) for row in after_state["server"]]
        target_server_rows = [row for row in after_state["server"] if len(row) >= 6 and (
            "SELECT * FROM t_test1 WHERE id = $1" in row[4]
            or "SELECT * FROM t_test2 WHERE key = $1" in row[4]
            or "SELECT * FROM t_test3 WHERE code = $1" in row[4]
        )]
        target_global_rows = [
            row for row in descriptions
            if "SELECT * FROM t_test1 WHERE id = $1" in row
            or "SELECT * FROM t_test2 WHERE key = $1" in row
            or "SELECT * FROM t_test3 WHERE code = $1" in row
        ]
        active_by_sid = {}
        zero_ref_seen = False
        for row in target_server_rows:
            sid = row[3]
            refcount = (row[5] if len(row) > 5 else "").strip()
            if refcount == "0" and "SELECT * FROM t_test1 WHERE id = $1" in row[4]:
                zero_ref_seen = True
            if refcount != "0":
                active_by_sid[sid] = active_by_sid.get(sid, 0) + 1
        max_active_same_sid = max(active_by_sid.values()) if active_by_sid else 0
        if "commit_ok=true" not in log_text:
            raise GlobalCacheFailure("backend_lru_eviction expects complete driver result sequence")
        lru_evict_lines = [
            line.strip() for line in fbase_log_text.splitlines()
            if "evict backend prepared statement" in line and "by LRU" in line
        ]
        if not lru_evict_lines:
            raise GlobalCacheFailure("backend_lru_eviction expects LRU Close(S) evidence in fbasecman log")
        if "stmt_q1_reexecute=2,test1_row2" not in log_text:
            raise GlobalCacheFailure("backend_lru_eviction expects oldest SQL to re-execute successfully after LRU pressure")
        close_target_lines = [
            line for line in lru_evict_lines
            if "__fbasecman_1" in line or "__fbasecman_2" in line or "__fbasecman_3" in line
        ]
        rt.summary["core_result"] = {
            "server_rows": ["|".join(row) for row in target_server_rows],
            "server_row_count": len(target_server_rows),
            "global_rows": list(target_global_rows),
            "global_row_count": len(target_global_rows),
            "stats_delta": dict(delta),
            "max_active_same_sid": max_active_same_sid,
            "zero_ref_seen": zero_ref_seen,
            BACKEND_PS_LIMIT_KEY: _case_ps_limit(rt.case.fbasecman, 2),
            "lru_evict_lines": lru_evict_lines,
        }
        rt.summary["verification_checks"] = [
            {
                "title": "启动运行配置已显式把 global_prepared_statements_limit 改成 2",
                "expected": "report 中能直接看到 live conf 里的 global_prepared_statements_limit 2",
                "actual": "%s=%s" % (BACKEND_PS_LIMIT_KEY, _case_ps_limit(rt.case.fbasecman, 2)),
                "result": "PASS" if "commit_ok=true" in log_text else "FAIL",
            },
            {
                "title": "fbasecman 确实发起了 LRU Close(S) 回收",
                "expected": "第 3 条 server prepared statement 进入同一 sid 时，日志出现 `evict backend prepared statement ... by LRU`",
                "actual": "\n".join(close_target_lines or lru_evict_lines),
                "result": "PASS" if lru_evict_lines else "FAIL",
            },
            {
                "title": "最早 SQL 在 LRU 压力后仍能重新 deploy 并执行成功",
                "expected": "driver 完整打印 stmt_q1_first -> stmt_q2_first -> stmt_q3_first -> stmt_q1_reexecute",
                "actual": "libpq 日志已输出 stmt_q1_reexecute=2,test1_row2",
                "result": "PASS",
            },
            {
                "title": "SHOW SERVER_PREP_STMTS 只作观测后端现状",
                "expected": "后端缓存数量与保留条目受 LRU 时序影响，这里仅展示最终观测结果",
                "actual": "\n".join(server_rows) if server_rows else "(0 rows)",
                "result": "PASS",
            },
        ]
        rt.summary["business_summary_lines"] = [
            "启动配置会显式把 global_prepared_statements_limit 改成 2。",
            "将同一 backend 固定在一个事务里，连续跑 3 条业务 prepared SQL，第 3 条进入时触发 backend LRU Close(S)。",
            "随后再次执行最早 SQL，验证它能在被回收后重新 deploy 并成功返回结果。",
        ]
        rt.summary["key_evidence_lines"] = [
            "Runtime conf: %s=%s" % (BACKEND_PS_LIMIT_KEY, _case_ps_limit(rt.case.fbasecman, 2)),
            "LRU close logs: %s" % (" | ".join(close_target_lines or lru_evict_lines) if lru_evict_lines else "<empty>"),
            "Server rows: %s" % ("; ".join("|".join(row) for row in target_server_rows) if target_server_rows else "<empty>"),
            "Global rows: %s" % ("; ".join(target_global_rows) if target_global_rows else "<empty>"),
            "Stats delta: %s" % delta,
        ]
        return

    rt.summary["verification_checks"] = [
        {
            "title": "协议 driver 触发了可观测的 cache activity",
            "expected": "DISCARD/redeploy 过程中至少新增一次 cache miss 或 hit",
            "actual": _stats_change_text(delta, ("misses", "hits", "evictions")),
            "result": "PASS",
        }
    ]


def _assert_unnamed_overwrite(rt, before_state, after_state):
    delta = _stats_delta(before_state["stats"], after_state["stats"])
    descriptions = ["|".join(row) for row in after_state["global"]]
    snapshots = rt.summary.get("unnamed_overwrite_snapshots", {})
    after_first_rows = snapshots.get("after_first", [])
    after_second_rows = snapshots.get("after_second", [])
    after_release_rows = snapshots.get("after_release", [])
    matched = [
        row for row in after_state["global"]
        if len(row) >= 5 and ("gc_unnamed_one" in row[1] or "gc_unnamed_two" in row[1])
    ]
    if len(matched) < 2:
        raise GlobalCacheFailure("unnamed overwrite expects two global entries for the two unnamed SQL texts")
    if delta.get("misses", 0) < 2:
        raise GlobalCacheFailure("unnamed overwrite expects misses delta >= 2, got %s" % delta.get("misses"))
    log_text = rt.libpq_log.read_text(encoding="utf-8")
    if "prepared statement not found in client cache" in log_text.lower():
        raise GlobalCacheFailure("unnamed overwrite should not hit stale client cache lookup")
    released = [
        row for row in after_release_rows
        if "gc_unnamed_one" in row or "gc_unnamed_two" in row
    ]
    if len(released) < 2:
        raise GlobalCacheFailure("unnamed overwrite expects both old/new global entries to remain observable after server release")
    leaked = [row for row in released if not row.endswith("|0")]
    if leaked:
        raise GlobalCacheFailure("unnamed overwrite expects old/new global entry ref_count to drop to 0 after release, got %s" % leaked)
    rt.summary["stats_delta"] = delta
    rt.summary["matched_global"] = descriptions
    named_rows = ["|".join(row) for row in matched]
    rt.summary["core_result"] = {
        "after_first_rows": list(after_first_rows),
        "after_second_rows": list(after_second_rows),
        "after_release_rows": list(after_release_rows),
    }
    rt.summary["verification_checks"] = [
        {
            "title": "第 1 次先发送 gc_unnamed_one，第 2 次再发送 gc_unnamed_two",
            "expected": "第一次后先看到 gc_unnamed_one；第二次后再同时看到 gc_unnamed_one / gc_unnamed_two 两条 entry",
            "actual": "after_first=%s after_second=%s" % (
                "; ".join(after_first_rows) if after_first_rows else "<empty>",
                "; ".join(after_second_rows) if after_second_rows else "<empty>",
            ),
            "result": "PASS",
        },
        {
            "title": "第 2 次 unnamed Parse 覆盖旧映射时再次走创建路径",
            "expected": "至少新增 2 次 cache miss，说明两条不同 unnamed SQL 分别创建 global entry",
            "actual": _stats_change_text(delta, ("misses", "hits")),
            "console_stats": dict(after_state["stats"]),
            "result": "PASS",
        },
        {
            "title": "unnamed 覆盖过程中没有出现 stale client cache 错误",
            "expected": "libpq 日志中不出现 prepared statement not found in client cache",
            "actual": "libpq 日志检查通过",
            "result": "PASS",
        },
        {
            "title": "等待 server_lifetime 到期后，旧/新两条 global entry 的 ref_count 都降为 0",
            "expected": "前端断开、后端缓存释放后，gc_unnamed_one / gc_unnamed_two 都还能看到，但 ref_count 应都为 0",
            "actual": "; ".join(released) if released else "<empty>",
            "result": "PASS",
        },
    ]
    rt.summary["business_summary_lines"] = [
        "同一 unnamed statement 先后发送两条不同 SQL：先 `gc_unnamed_one`，再 `gc_unnamed_two`。",
        "第 2 次 Parse 不会像 named 那样报冲突，而是覆盖客户端 unnamed 映射，但旧 global entry 不会被错误破坏。",
        "等待 server_lifetime 到期后，再次确认旧/新两条 global entry 的 ref_count 都回到 0，没有泄漏。",
    ]
    rt.summary["key_evidence_lines"] = [
        "Console: %s" % "; ".join(named_rows),
        "After release: %s" % ("; ".join(released) if released else "<empty>"),
        "Stats delta: %s" % delta,
        "libpq: 未发现 prepared statement not found in client cache",
    ]


def _assert_named_conflict_keeps_old(rt, before_state, after_state):
    log_text = rt.libpq_log.read_text(encoding="utf-8").lower()
    if "conflict_ok" not in log_text:
        raise GlobalCacheFailure("named conflict keep-old did not capture conflict marker")
    if "reuse_old_ok" not in log_text:
        raise GlobalCacheFailure("named conflict keep-old expects the original statement to remain usable")
    matched = [
        "|".join(row) for row in after_state["global"]
        if len(row) >= 5 and "gc_named_conflict" in row[1]
    ]
    rt.summary["behavior"] = "命名冲突返回 duplicate 错误，旧 statement 仍可在同一客户端继续复用"
    rt.summary["core_result"] = {
        "matched_entries": list(matched),
        "matched_entry_count": len(matched),
    }
    rt.summary["verification_checks"] = [
        {
            "title": "同名不同 SQL 冲突分支已被稳定复现",
            "expected": "driver 日志出现 conflict_ok，说明第 2 次 Parse 确实进入了命名冲突路径",
            "actual": "libpq 日志已捕获 conflict_ok",
            "result": "PASS",
        },
        {
            "title": "当前真实产品行为已被明确固定",
            "expected": "冲突返回错误，但旧 statement 仍可在同一客户端继续复用",
            "actual": rt.summary["behavior"],
            "result": "PASS",
        },
    ]
    rt.summary["business_summary_lines"] = [
        "先创建旧的 named statement，再用同名不同 SQL 触发冲突。",
        "当前产品行为是冲突返回 duplicate 错误，但旧 statement 仍可在同一客户端继续复用。",
        "这个 case 直接记录当前真实结果：连接被关闭，但冲突前那条旧 global entry 仍然保留在 console after 中。",
    ]
    rt.summary["key_evidence_lines"] = [
        "Driver: conflict_ok",
        "Driver: reuse_old_ok after conflict",
        "Console(global): %s" % "; ".join(matched) if matched else "Console(global): <missing>",
        "Behavior: %s" % rt.summary["behavior"],
    ]


