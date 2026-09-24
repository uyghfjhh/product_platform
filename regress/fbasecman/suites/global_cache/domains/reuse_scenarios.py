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
def _assert_basic_reuse(rt, before_state, after_state):
    delta = _stats_delta(before_state["stats"], after_state["stats"])
    if delta.get("misses", 0) < 1:
        raise GlobalCacheFailure("basic_reuse expects misses delta >= 1, got %s" % delta.get("misses"))
    descriptions = ["|".join(row) for row in after_state["global"]]
    if not any("gc_basic_reuse" in row for row in descriptions):
        raise GlobalCacheFailure("basic_reuse target SQL not found in console global cache output")
    server_rows = ["|".join(row) for row in after_state["server"]]
    if not any("gc_basic_reuse" in row and row.endswith("|3") for row in server_rows):
        raise GlobalCacheFailure("basic_reuse expects one server prepared statement with refcount=3")
    rt.summary["stats_delta"] = delta
    matched = [row for row in descriptions if "gc_basic_reuse" in row]
    matched_server = [row for row in server_rows if "gc_basic_reuse" in row]
    rt.summary["core_result"] = {
        "statement": "select name from test where id = ? /* gc_basic_reuse */",
        "execute_count": 3,
        "global_entry": matched[0] if matched else "",
        "server_entry": matched_server[0] if matched_server else "",
        "stats_delta": dict(delta),
    }
    _summary_set_report_blocks(
        rt,
        verification_checks=[
        {
            "title": "第 1 次执行创建 gc_basic_reuse 对应 global entry",
            "expected": "同一连接首轮 prepared execute 后，console 中出现 gc_basic_reuse 对应 global entry",
            "actual": matched[0] if matched else "<missing>",
            "result": "PASS",
        },
        {
            "title": "首轮执行确实走了创建路径",
            "expected": "首次执行至少新增 1 次 cache miss，证明走过创建路径",
            "actual": _stats_change_text(delta, ("misses", "hits")),
            "console_stats": dict(after_state["stats"]),
            "result": "PASS",
        },
        {
            "title": "后 2 次继续复用同一条 server prepared statement",
            "expected": "3 次业务执行最终汇总到同一条 server prepared statement，refcount=3",
            "actual": matched_server[0] if matched_server else "<missing>",
            "result": "PASS",
        },
        ],
        business_summary=[
            "同一 JDBC 连接内连续 3 次执行同一条 PreparedStatement。",
            "第 1 次执行创建 global entry，并在当前 backend 上部署 server prepared statement。",
            "第 2/3 次不再新建业务 entry，而是继续复用同一条 server prepared statement，所以最终 refcount 累加到 3。",
        ],
        key_evidence=[
            "Statement: select name from test where id = ? /* gc_basic_reuse */",
            "Console(global): %s" % matched[0],
            "Console(server): %s" % (matched_server[0] if matched_server else "<missing>"),
            "Stats delta: %s" % delta,
        ],
    )


def _assert_cross_client_reuse(rt, before_state, after_state):
    delta = _stats_delta(before_state["stats"], after_state["stats"])
    if delta.get("hits", 0) < 1:
        raise GlobalCacheFailure("cross_client_reuse expects hits delta >= 1, got %s" % delta.get("hits"))
    descriptions = ["|".join(row) for row in after_state["global"]]
    if not any("gc_cross_client_reuse" in row for row in descriptions):
        raise GlobalCacheFailure("cross_client_reuse target SQL not found in console global cache output")
    rt.summary["stats_delta"] = delta
    matched = [row for row in descriptions if "gc_cross_client_reuse" in row]
    rt.summary["core_result"] = {
        "statement": "select name from test where id = ? /* gc_cross_client_reuse */",
        "matched_entries": list(matched),
        "matched_entry_count": len(matched),
        "stats_delta": dict(delta),
    }
    _summary_set_report_blocks(
        rt,
        verification_checks=[
        {
            "title": "第 1 个客户端先创建 gc_cross_client_reuse 对应 global entry",
            "expected": "第 1 个 JDBC 连接执行后，console 中出现 gc_cross_client_reuse 对应 global entry",
            "actual": matched[0] if matched else "<missing>",
            "result": "PASS",
        },
        {
            "title": "第 2 个客户端命中已有 global entry",
            "expected": "第 2 个连接执行时至少新增 1 次 cache hit，而不是创建重复 entry",
            "actual": _stats_change_text(delta, ("hits", "misses")),
            "console_stats": dict(after_state["stats"]),
            "result": "PASS",
        },
        {
            "title": "跨客户端复用没有额外创建重复业务 entry",
            "expected": "两次业务执行之后，console 中仍只保留 1 条 gc_cross_client_reuse global entry",
            "actual": matched[0] if matched else "<missing>",
            "result": "PASS" if len(matched) == 1 else "FAIL",
        },
        ],
        business_summary=[
            "第一个客户端先执行目标 PreparedStatement，负责创建 global entry。",
            "第二个客户端重新连接后执行相同 SQL，应直接命中已有 global entry，而不是再新建一条。",
            "最终 console 中只保留 1 条业务 entry，命中统计上升，说明跨客户端复用链路成立。",
        ],
        key_evidence=[
            "Statement: select name from test where id = ? /* gc_cross_client_reuse */",
            "Console(global): %s" % matched[0],
            "Stats delta: %s" % delta,
        ],
    )


def _assert_close_unref(rt, before_state, after_state):
    delta = _stats_delta(before_state["stats"], after_state["stats"])
    snapshots = rt.summary.get("close_unref_snapshots", {})
    close_before = snapshots.get("close_before", [])
    close_after_close = snapshots.get("close_after_close", [])
    close_final = snapshots.get("close_final", [])
    disconnect_before = snapshots.get("disconnect_before", [])
    disconnect_after_disconnect = snapshots.get("disconnect_after_disconnect", [])
    disconnect_final = snapshots.get("disconnect_final", [])

    if not close_before or not close_after_close or not close_final:
        raise GlobalCacheFailure("close_and_disconnect_unref missing close-path snapshots")
    if not disconnect_before or not disconnect_after_disconnect or not disconnect_final:
        raise GlobalCacheFailure("close_and_disconnect_unref missing disconnect-path snapshots")

    close_before_row = next((row for row in close_before if "gc_close_unref_close" in row), "")
    close_after_close_row = next((row for row in close_after_close if "gc_close_unref_close" in row), "")
    close_final_row = next((row for row in close_final if "gc_close_unref_close" in row), "")
    disconnect_before_row = next((row for row in disconnect_before if "gc_close_unref_disconnect" in row), "")
    disconnect_after_disconnect_row = next((row for row in disconnect_after_disconnect if "gc_close_unref_disconnect" in row), "")
    disconnect_final_row = next((row for row in disconnect_final if "gc_close_unref_disconnect" in row), "")

    def _record_close_unref_failure(title, expected, actual_rows):
        rt.record_step(
            title,
            output=(
                "expected: %s\n"
                "actual:\n%s"
            ) % (
                expected,
                "\n".join(actual_rows) if actual_rows else "<empty>",
            ),
        )

    if (
        not close_before_row or not close_after_close_row or not close_final_row
        or not disconnect_before_row or not disconnect_after_disconnect_row or not disconnect_final_row
    ):
        _record_close_unref_failure(
            "close/disconnect 目标 entry 快照不完整",
            "close/disconnect 两条路径都应在 before / 中间态 / final 三个阶段观测到目标 entry",
            [
                "close_before: %s" % ("; ".join(close_before) if close_before else "<empty>"),
                "close_after_close: %s" % ("; ".join(close_after_close) if close_after_close else "<empty>"),
                "close_final: %s" % ("; ".join(close_final) if close_final else "<empty>"),
                "disconnect_before: %s" % ("; ".join(disconnect_before) if disconnect_before else "<empty>"),
                "disconnect_after_disconnect: %s" % ("; ".join(disconnect_after_disconnect) if disconnect_after_disconnect else "<empty>"),
                "disconnect_final: %s" % ("; ".join(disconnect_final) if disconnect_final else "<empty>"),
            ],
        )
        raise GlobalCacheFailure("close_and_disconnect_unref cannot find target close/disconnect entries in snapshots")
    if not close_before_row.endswith("|2"):
        _record_close_unref_failure(
            "close-path before 快照 ref_count 不符合预期",
            "close-path entry ref_count=2 before explicit close",
            [close_before_row],
        )
        raise GlobalCacheFailure("close_and_disconnect_unref expects close-path entry ref_count=2 before explicit close")
    if not close_after_close_row.endswith("|1"):
        _record_close_unref_failure(
            "close-path 显式 Close 后快照 ref_count 不符合预期",
            "close-path entry ref_count=1 right after explicit close",
            [close_after_close_row],
        )
        raise GlobalCacheFailure("close_and_disconnect_unref expects close-path entry ref_count=1 right after explicit close")
    if not close_final_row.endswith("|0"):
        _record_close_unref_failure(
            "close-path 最终快照 ref_count 不符合预期",
            "close-path entry ref_count=0 after server_lifetime release",
            [close_final_row],
        )
        raise GlobalCacheFailure("close_and_disconnect_unref expects close-path entry ref_count=0 after server_lifetime release")
    if not disconnect_before_row.endswith("|2"):
        _record_close_unref_failure(
            "disconnect-path before 快照 ref_count 不符合预期",
            "disconnect-path entry ref_count=2 before disconnect",
            [disconnect_before_row],
        )
        raise GlobalCacheFailure("close_and_disconnect_unref expects disconnect-path entry ref_count=2 before disconnect")
    if not disconnect_after_disconnect_row.endswith("|1"):
        _record_close_unref_failure(
            "disconnect-path 断连后即时快照 ref_count 不符合预期",
            "disconnect-path entry ref_count=1 right after disconnect",
            [disconnect_after_disconnect_row],
        )
        raise GlobalCacheFailure("close_and_disconnect_unref expects disconnect-path entry ref_count=1 right after disconnect")
    if not disconnect_final_row.endswith("|0"):
        _record_close_unref_failure(
            "disconnect-path 最终快照 ref_count 不符合预期",
            "disconnect-path entry ref_count=0 after server_lifetime release",
            [disconnect_final_row],
        )
        raise GlobalCacheFailure("close_and_disconnect_unref expects disconnect-path entry ref_count=0 after server_lifetime release")

    rt.summary["stats_delta"] = delta
    rt.summary["core_result"] = {
        "close_before": close_before_row,
        "close_after_close": close_after_close_row,
        "close_final": close_final_row,
        "disconnect_before": disconnect_before_row,
        "disconnect_after_disconnect": disconnect_after_disconnect_row,
        "disconnect_final": disconnect_final_row,
        "stats_delta": dict(delta),
    }
    _summary_set_report_blocks(
        rt,
        verification_checks=[
        {
            "title": "显式 Close 只释放 client 持有的 1 个引用",
            "expected": "gc_close_unref_close 在 Close 前 ref_count=2，Close 后 ref_count=1；后端引用仍然存在",
            "actual": "before=%s | after_close=%s" % (close_before_row, close_after_close_row),
            "result": "PASS",
        },
        {
            "title": "断开连接也只释放 client 持有的 1 个引用",
            "expected": "gc_close_unref_disconnect 在断开前 ref_count=2，断开后 ref_count=1；后端引用仍然存在",
            "actual": "before=%s | after_disconnect=%s" % (disconnect_before_row, disconnect_after_disconnect_row),
            "result": "PASS",
        },
        {
            "title": "等待 server_lifetime 到期后，后端引用释放，两条 entry 都回到 0",
            "expected": "最终 `gc_close_unref_close / gc_close_unref_disconnect` 都仍可观测，且 ref_count 都为 0",
            "actual": "Close 最终=%s；disconnect 最终=%s；%s" % (
                close_final_row,
                disconnect_final_row,
                _stats_change_text(delta, ("referenced_entries", "unreferenced_entries")),
            ),
            "console_stats": dict(after_state["stats"]),
            "result": "PASS",
        }
        ],
        business_summary=[
            "第 1 条 prepared SQL `gc_close_unref_close` 用来验证显式 Close：建好后同时被 client/server 持有，所以先看到 ref_count=2；Close 之后只释放 client 引用，因此降到 1。",
            "第 2 条 prepared SQL `gc_close_unref_disconnect` 用来验证 disconnect：不断开前同样是 ref_count=2；断开后只释放 client 引用，因此也先降到 1。",
            "最后等待 server_lifetime 到期，让后端缓存释放剩余引用，两条 entry 才都会从 1 再降到 0。",
        ],
        key_evidence=[
            "Close path: %s -> %s -> %s" % (close_before_row, close_after_close_row, close_final_row),
            "Disconnect path: %s -> %s -> %s" % (disconnect_before_row, disconnect_after_disconnect_row, disconnect_final_row),
            "Stats delta: %s" % delta,
        ],
    )


def _assert_shared_global_entry_disconnect_one_client_reuse(rt, before_state, after_state):
    delta = _stats_delta(before_state["stats"], after_state["stats"])
    snapshots = rt.summary.get("shared_disconnect_reuse_snapshots", {})
    both_connected = snapshots.get("both_connected", [])
    after_disconnect1 = snapshots.get("after_disconnect1", [])
    after_reuse2 = snapshots.get("after_reuse2", [])
    after_disconnect2 = snapshots.get("after_disconnect2", [])

    row_before = next((row for row in both_connected if "gc_shared_disconnect_reuse" in row), "")
    row_after_disconnect1 = next((row for row in after_disconnect1 if "gc_shared_disconnect_reuse" in row), "")
    row_after_reuse2 = next((row for row in after_reuse2 if "gc_shared_disconnect_reuse" in row), "")
    row_after_disconnect2 = next((row for row in after_disconnect2 if "gc_shared_disconnect_reuse" in row), "")

    if not row_before or not row_after_disconnect1 or not row_after_reuse2 or not row_after_disconnect2:
        raise GlobalCacheFailure("shared_global_entry_disconnect_one_client_other_client_reuse_still_ok missing target snapshots")
    if not row_before.endswith("|3"):
        raise GlobalCacheFailure("shared_global_entry_disconnect_one_client_other_client_reuse_still_ok expects shared entry ref_count=3 when two clients are connected")
    if not row_after_disconnect1.endswith("|2"):
        raise GlobalCacheFailure("shared_global_entry_disconnect_one_client_other_client_reuse_still_ok expects shared entry ref_count=2 after disconnecting one client")
    if not row_after_reuse2.endswith("|2") and not row_after_reuse2.endswith("|3"):
        raise GlobalCacheFailure("shared_global_entry_disconnect_one_client_other_client_reuse_still_ok expects shared entry to remain referenced after remaining client reuses the same SQL")
    if not row_after_disconnect2.endswith("|0"):
        raise GlobalCacheFailure("shared_global_entry_disconnect_one_client_other_client_reuse_still_ok expects shared entry ref_count=0 after both clients disconnect and server refs release")

    libpq_text = rt.libpq_log.read_text(encoding="utf-8", errors="replace") if rt.libpq_log.exists() else ""
    if "conn2_second_ok rows=" not in libpq_text:
        raise GlobalCacheFailure("shared_global_entry_disconnect_one_client_other_client_reuse_still_ok expects remaining client reuse success marker")

    rt.summary["stats_delta"] = delta
    rt.summary["core_result"] = {
        "both_connected_entry": row_before,
        "after_disconnect1_entry": row_after_disconnect1,
        "after_reuse_entry": row_after_reuse2,
        "after_disconnect2_entry": row_after_disconnect2,
        "stats_delta": dict(delta),
    }
    _summary_set_report_blocks(
        rt,
        verification_checks=[
            {
                "title": "两个客户端先共享命中同一 global entry",
                "expected": "两边都执行成功后，共享条目 ref_count=3",
                "actual": row_before,
                "result": "PASS",
            },
            {
                "title": "断开第一个客户端后，共享条目仍保留",
                "expected": "共享条目 ref_count 从 3 降到 2，而不是被误删",
                "actual": row_after_disconnect1,
                "result": "PASS",
            },
            {
                "title": "剩余客户端继续复用同一 SQL 仍成功",
                "expected": "libpq 输出 `conn2_second_ok rows=<n>`，且共享条目在 after_reuse2 快照里仍可见",
                "actual": "libpq=conn2_second_ok rows=<n> | after_reuse2=%s" % row_after_reuse2,
                "result": "PASS",
            },
            {
                "title": "两个客户端都断开并等待 server_lifetime 后，剩余引用才全部释放",
                "expected": "最终共享条目 ref_count 降到 0",
                "actual": row_after_disconnect2,
                "result": "PASS",
            },
        ],
        business_summary=[
            "两个客户端先分别执行同一条业务 SQL `gc_shared_disconnect_reuse`，共同命中同一 global entry。",
            "先断开第一个客户端，不只看 ref_count 从 3 到 2，而是继续让第二个客户端再次执行同一 SQL。",
            "第二个客户端这次复用仍成功，说明断开一个客户端不会把剩余客户端仍在使用的 shared global entry / backend prepared state 搞坏。",
        ],
        key_evidence=[
            "Both connected: %s" % row_before,
            "After disconnect1: %s" % row_after_disconnect1,
            "libpq: conn2_second_ok rows=<n>",
            "After reuse2: %s" % row_after_reuse2,
            "After disconnect2: %s" % row_after_disconnect2,
            "Stats delta: %s" % delta,
        ],
    )


def _run_shared_global_entry_disconnect_one_client_reuse_case(rt):
    start_conf = rt.render_runtime_conf(
        [
            ('server_lifetime  3600', 'server_lifetime  3'),
            ('server_lifetime 3600', 'server_lifetime 3'),
        ],
        stem="shared_disconnect_reuse_runtime.conf",
    )
    start_conf_text = start_conf.read_text(encoding="utf-8", errors="replace")
    rt.start_fbasecman(conf=start_conf)
    before_state = rt.capture_console_state("before")
    rt.summary["before_stats"] = before_state["stats"]
    rt.record_step(
        "运行配置: server_lifetime 3",
        output="conf : %s\n%s" % (start_conf, "\n".join(_conf_lines_by_keys(start_conf_text, ["server_lifetime"]))),
    )

    driver, command, logfile = _start_phased_libpq(rt, "GC_shared_disconnect_reuse")

    def observe(phase, marker):
        if phase == "both_connected":
            rt.record_step(
                "执行 libpq driver: 两客户端共享同一 global entry",
                command=" ".join(command), logfile=logfile,
                output="两个客户端均已完成首次 prepared SQL，保持连接等待断开第一个客户端。",
            )
            return rt.capture_console_state("shared_reuse_both_connected")
        if phase == "after_disconnect1":
            return rt.capture_console_state("shared_reuse_after_disconnect1")
        if phase == "after_reuse2":
            rt.record_step(
                "第 2 个客户端继续执行同一业务 SQL",
                command="reuse2", logfile=logfile,
                output="第 2 个客户端已在第 1 个客户端断开后再次成功执行同一 prepared SQL。",
            )
            return rt.capture_console_state("shared_reuse_after_reuse2")
        return marker

    observations, rc, output = observe_phases(
        driver,
        [
            PhaseAction("both_connected", "READY_BOTH_CONNECTED", "disconnect1"),
            PhaseAction("after_disconnect1", "READY_AFTER_DISCONNECT1", "reuse2"),
            PhaseAction("after_reuse2", "READY_AFTER_REUSE2", "disconnect2"),
            PhaseAction("after_disconnect2", "READY_AFTER_DISCONNECT2", None),
        ],
        observe,
        timeout=30,
        finish_timeout=10,
    )
    if rc != 0:
        raise GlobalCacheFailure("shared disconnect reuse driver failed with rc=%s" % rc)
    both_connected_state = observations["both_connected"]
    after_disconnect1_state = observations["after_disconnect1"]
    after_reuse2_state = observations["after_reuse2"]

    rt.record_step(
        "等待第二个客户端释放后的 server 引用清理",
        output="第二个客户端也断开后，再等待 server_lifetime 到期，确认共享 global entry 的剩余 server 引用回到 0",
    )
    after_wait_state, matched_unref = _wait_target_entries_unref(rt, "gc_shared_disconnect_reuse", 1, 15)
    after_disconnect2_rows = ["|".join(row) for row in after_wait_state["global"] if len(row) >= 2 and "gc_shared_disconnect_reuse" in row[1]]

    rt.summary["shared_disconnect_reuse_snapshots"] = {
        "both_connected": ["|".join(row) for row in both_connected_state["global"] if len(row) >= 2 and "gc_shared_disconnect_reuse" in row[1]],
        "after_disconnect1": ["|".join(row) for row in after_disconnect1_state["global"] if len(row) >= 2 and "gc_shared_disconnect_reuse" in row[1]],
        "after_reuse2": ["|".join(row) for row in after_reuse2_state["global"] if len(row) >= 2 and "gc_shared_disconnect_reuse" in row[1]],
        "after_disconnect2": after_disconnect2_rows,
    }
    _append_driver_log(rt.libpq_log, output)
    rt.summary["shared_disconnect_reuse_waited_entries"] = ["|".join(row) for row in matched_unref]
    after_state = rt.capture_console_state("after")
    rt.summary["after_stats"] = after_state["stats"]
    _assert_shared_global_entry_disconnect_one_client_reuse(rt, before_state, after_state)
    return after_state


def _run_close_unref_case(rt):
    start_conf = rt.render_runtime_conf(
        [
            ('server_lifetime  3600', 'server_lifetime  10'),
            ('server_lifetime 3600', 'server_lifetime 10'),
        ],
        stem="close_unref_runtime.conf",
    )
    start_conf_text = start_conf.read_text(encoding="utf-8", errors="replace")
    rt.summary["close_unref_runtime_conf"] = str(start_conf)
    rt.summary["close_unref_runtime_conf_text"] = start_conf_text
    rt.start_fbasecman(conf=start_conf)
    before_state = rt.capture_console_state("before")
    rt.summary["before_stats"] = before_state["stats"]
    rt.record_step(
        "运行配置: server_lifetime 10",
        output="conf : %s\n%s" % (start_conf, "\n".join(_conf_lines_by_keys(start_conf_text, ["server_lifetime"]))),
    )
    binary = _build_libpq_asset(
        rt, rt.logs_dir / "GC_close_and_disconnect_unref.gcc.log"
    )
    close_driver, close_command, close_log = _start_phased_libpq(
        rt, "GC_close_and_disconnect_unref_close", ["close"],
        include_rw_method=False, binary=binary,
    )

    def observe_close(phase, marker):
        if phase == "before_close":
            rt.record_step(
                "执行 libpq driver: close-path prepared statement",
                command=" ".join(close_command), logfile=close_log,
                output="prepared SQL 已执行，客户端连接保持，等待发送显式 Close。",
            )
            return rt.capture_console_state("after_close_before")
        return rt.capture_console_state("after_close_after")

    close_observations, close_rc, close_output = observe_phases(
        close_driver,
        [
            PhaseAction("before_close", "READY_CLOSE_BEFORE", "close"),
            PhaseAction("after_close", "READY_CLOSE_AFTER", "exit"),
        ],
        observe_close,
        timeout=30,
        finish_timeout=10,
    )
    if close_rc != 0:
        raise GlobalCacheFailure("close path driver failed with rc=%s" % close_rc)
    _append_driver_log(rt.libpq_log, close_output)
    close_before_state = close_observations["before_close"]
    close_before_rows = ["|".join(row) for row in close_before_state["global"] if len(row) >= 2 and "gc_close_unref_close" in row[1]]
    close_after_close_state = close_observations["after_close"]
    close_after_close_rows = ["|".join(row) for row in close_after_close_state["global"] if len(row) >= 2 and "gc_close_unref_close" in row[1]]

    rt.record_step(
        "等待 gc_close_unref_close 后端引用释放",
        output="显式 Close 后，client 引用已释放；继续等待 server_lifetime 到期，并确认 gc_close_unref_close 的 ref_count 从 1 再降到 0",
    )
    rt.summary["close_unref_close_output"] = close_output
    close_final_state, close_final_matched = _wait_target_entries_unref(rt, "gc_close_unref_close", 1, 15)
    close_final_rows = ["|".join(row) for row in close_final_matched]

    disconnect_driver, disconnect_command, disconnect_log = _start_phased_libpq(
        rt, "GC_close_and_disconnect_unref_disconnect", ["disconnect"],
        include_rw_method=False, binary=binary,
    )

    def observe_disconnect(phase, marker):
        rt.record_step(
            "执行 libpq driver: disconnect-path prepared statement",
            command=" ".join(disconnect_command), logfile=disconnect_log,
            output="prepared SQL 已执行，客户端连接保持，等待断开连接。",
        )
        return rt.capture_console_state("after_disconnect_before")

    disconnect_observations, disconnect_rc, disconnect_output = observe_phases(
        disconnect_driver,
        [PhaseAction("before_disconnect", "READY_DISCONNECT_BEFORE", "disconnect")],
        observe_disconnect,
        timeout=30,
        finish_timeout=10,
    )
    if disconnect_rc != 0:
        raise GlobalCacheFailure("disconnect path driver failed with rc=%s" % disconnect_rc)
    _append_driver_log(rt.libpq_log, disconnect_output)
    disconnect_before_state = disconnect_observations["before_disconnect"]
    disconnect_before_rows = ["|".join(row) for row in disconnect_before_state["global"] if len(row) >= 2 and "gc_close_unref_disconnect" in row[1]]
    rt.summary["close_unref_disconnect_output"] = disconnect_output
    disconnect_after_disconnect_state = rt.capture_console_state("after_disconnect_after")
    disconnect_after_disconnect_rows = ["|".join(row) for row in disconnect_after_disconnect_state["global"] if len(row) >= 2 and "gc_close_unref_disconnect" in row[1]]
    rt.record_step(
        "等待 gc_close_unref_disconnect 引用释放",
        output="等待连接断开且 server_lifetime 到期，并确认 gc_close_unref_disconnect entry 的 ref_count 回到 0",
    )
    waited_state, matched_unref = _wait_target_entries_unref(rt, "gc_close_unref_disconnect", 1, 15)
    disconnect_after_rows = ["|".join(row) for row in waited_state["global"] if len(row) >= 2 and "gc_close_unref_disconnect" in row[1]]

    rt.summary["close_unref_snapshots"] = {
        "close_before": close_before_rows,
        "close_after_close": close_after_close_rows,
        "close_final": close_final_rows,
        "disconnect_before": disconnect_before_rows,
        "disconnect_after_disconnect": disconnect_after_disconnect_rows,
        "disconnect_final": disconnect_after_rows,
    }
    rt.summary["close_unref_waited_entries"] = ["|".join(row) for row in matched_unref]
    after_state = rt.capture_console_state("after")
    rt.summary["after_stats"] = after_state["stats"]
    _assert_close_unref(rt, before_state, after_state)
    return after_state


