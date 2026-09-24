"""Common imports and helpers for common suite executors."""

import concurrent.futures
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

from framework.clients.psql import build_psql_command
from framework.configuration import load_regression_config
from framework.execution.command import run_logged_command
from framework.reporting import ReportCheck, ReportDocument, ReportStep, render_report
from suites.ha_commands.runtime import HaCommandFailure, HaCommandRuntime
from suites.common.helpers import (
    LOCALE_CONF_PATH,
    EXPECTED_COLUMNS,
    LocaleManager,
    CommonRuntime,
    _parse_headers,
    _has_chinese,
    _parse_table_data,
)


def _run_route_stats_quantiles(rt):
    """Executor for 8.60: 统计辅助对象分配失败未向上传播，半初始化路由或展示路径可触发空指针."""
    rt.coverage_items = [
        "未配置 quantiles 默认正常行为与向后兼容性验证：默认展示基础列与吞吐统计列 (16列)",
        "配置 quantiles '0.99,0.95,0.5' 启动 fbasecman，验证直方图辅助对象成功初始化",
        "多 Group 全拓扑业务路由与连接池统计正常累加：验证 MMR/Rep/Balance/Single 业务请求真实递增",
        "执行 SHOW POOLS 校验连接池基础列完整性 (13列)",
        "执行 SHOW POOLS_EXTENDED 逐列强断言动态分位数与吞吐列名 (22列: query_0.99, tx_0.99 等)",
        "注入真实耗时查询与事务，填充 tdigest 样本并验证分位数数值非负与单调性 (P99 >= P95 >= P50)",
        "高频并发执行 100 次 SHOW POOLS_EXTENDED，验证直方图合并无空指针解引用与崩溃 (8.60 核心缺陷)",
    ]
    rt.overview_steps = [
        "启动无 quantiles 配置的标准默认实例，验证向后兼容性与 16 列基础扩展展示",
        "在配置文件中注入 quantiles 参数并重启 fbasecman",
        "执行 SHOW POOLS 校验连接池基础列名清单 (13列)",
        "执行 SHOW POOLS_EXTENDED 逐列强断言全量 22 个列名与初始数据结构",
        "跨 MMR/Rep/Balance/Single 全拓扑 Group 执行正常业务读写，验证统计数据真实累加",
        "注入真实耗时查询 (SELECT pg_sleep) 与事务，验证分位数单调性 (P99 >= P95 >= P50)",
        "多客户端高频并发执行 100 次 SHOW POOLS_EXTENDED，验证直方图合并无空指针解引用与崩溃",
    ]

    def run_console_query(sql, log_name):
        command = build_psql_command(
            rt.env.config["local"]["postgres_dir"], "127.0.0.1",
            rt.listen_port, "admin", "console", sql,
        )
        res = run_logged_command(
            command, rt.logs_dir / log_name, cwd=rt.workdir,
        )
        return res

    # 步骤 1: 正常功能与向后兼容性 - 默认未配置 quantiles 时的正常行为
    rt.start()
    res_def_ext = run_console_query("SHOW POOLS_EXTENDED;", "show_pools_extended_default.log")
    headers_def_ext, rows_def_ext = _parse_table_data(res_def_ext.output)
    expected_headers_def = [
        "node_name", "group_name", "database", "user", "pool_mode",
        "cl_active", "cl_idle", "sv_active", "sv_idle", "sv_tested",
        "total_requests", "request_per_sec", "cl_active_ratio",
        "bytes_received", "bytes_sent", "tcp_conn_count",
    ]
    step1_ok = (res_def_ext.returncode == 0 and headers_def_ext == expected_headers_def)
    actual_step1 = "默认无 quantiles 配置下 SHOW POOLS_EXTENDED 列名: [%s], 行数: %d" % (
        ", ".join(headers_def_ext), len(rows_def_ext),
    )
    rt.record_step(
        title="未配置 quantiles 默认正常行为与向后兼容性验证",
        command="$ %s\n\n%s" % (res_def_ext.command, res_def_ext.output.rstrip()),
        expected="默认情况下精确返回 16 列（基础 13 列 + 3 个吞吐列，无动态分位数列），连接池与路由统计正常",
        actual=actual_step1,
        result="PASS" if step1_ok else "FAIL",
    )
    s1_order = rt._step_order
    rt.step_meta[s1_order] = {
        "coverage": "1",
        "coverage_check": "未配置 quantiles 默认正常行为与向后兼容性验证",
        "checks": [
            ReportCheck(
                title="默认 16 列向后兼容性校验",
                expected=", ".join(expected_headers_def),
                actual=", ".join(headers_def_ext),
                result="PASS" if step1_ok else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s1_order, 1, "未配置 quantiles 默认正常行为验证"))
    if not step1_ok:
        raise HaCommandFailure("步骤 1 校验失败: %s" % actual_step1)

    # 停止默认实例，准备注入 quantiles 配置
    rt.process.stop()
    time.sleep(0.3)

    # 步骤 2: 注入 quantiles 配置并启动
    def add_quantiles_conf(content):
        content = content.replace(
            'pool_discard no',
            'pool_discard no\n    quantiles "0.99,0.95,0.5"',
        )
        content = content.replace(
            'role "admin"',
            'role "admin"\n    quantiles "0.99,0.95,0.5"',
        )
        return content

    rt.start(transform=add_quantiles_conf)
    q_start_order = rt._step_order
    rt.step_meta[q_start_order] = {
        "coverage": "2",
        "coverage_check": "配置 quantiles '0.99,0.95,0.5' 启动 fbasecman，验证直方图辅助对象成功初始化",
        "checks": [
            ReportCheck(
                title="带 quantiles 配置启动就绪",
                expected="fbasecman 正常启动且控制台就绪",
                actual="控制台就绪",
                result="PASS",
            ),
        ],
    }
    rt.coverage_mapping.append((q_start_order, 2, "配置 quantiles 启动并初始化直方图辅助对象"))

    # 步骤 3: SHOW POOLS (13列)
    res_pools = run_console_query("SHOW POOLS;", "show_pools.log")
    headers_pools, rows_pools = _parse_table_data(res_pools.output)
    expected_headers_pools = [
        "node_name", "group_name", "database", "user", "pool_mode",
        "cl_active", "cl_idle", "sv_active", "sv_idle",
        "sv_tested", "total_requests", "request_per_sec", "cl_active_ratio",
    ]
    pools_ok = (res_pools.returncode == 0 and headers_pools == expected_headers_pools)
    actual_step3 = "SHOW POOLS 返回列名: [%s], 行数: %d" % (", ".join(headers_pools), len(rows_pools))
    rt.record_step(
        title="执行 SHOW POOLS 校验连接池基础列名清单 (13列)",
        command="$ %s\n\n%s" % (res_pools.command, res_pools.output.rstrip()),
        expected="SHOW POOLS 列名精确匹配 13 个基础列名",
        actual=actual_step3,
        result="PASS" if pools_ok else "FAIL",
    )
    s3_order = rt._step_order
    rt.step_meta[s3_order] = {
        "coverage": "4",
        "coverage_check": "执行 SHOW POOLS 校验连接池基础列名完整性",
        "checks": [
            ReportCheck(
                title="SHOW POOLS 列名校验",
                expected=", ".join(expected_headers_pools),
                actual=", ".join(headers_pools),
                result="PASS" if pools_ok else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s3_order, 4, "执行 SHOW POOLS 校验连接池基础列名"))
    if not pools_ok:
        raise HaCommandFailure("步骤 3 校验失败: %s" % actual_step3)

    # 步骤 4: SHOW POOLS_EXTENDED 强断言动态分位数列名 (22列)
    res_ext = run_console_query("SHOW POOLS_EXTENDED;", "show_pools_extended_init.log")
    headers_ext, rows_ext = _parse_table_data(res_ext.output)
    expected_headers_ext = [
        "node_name", "group_name", "database", "user", "pool_mode",
        "cl_active", "cl_idle", "sv_active", "sv_idle", "sv_tested",
        "total_requests", "request_per_sec", "cl_active_ratio",
        "bytes_received", "bytes_sent", "tcp_conn_count",
        "query_0.99", "tx_0.99", "query_0.95", "tx_0.95",
        "query_0.5", "tx_0.5",
    ]
    ext_ok = (res_ext.returncode == 0 and headers_ext == expected_headers_ext)
    actual_step4 = "SHOW POOLS_EXTENDED 返回列名: [%s], 行数: %d" % (", ".join(headers_ext), len(rows_ext))
    rt.record_step(
        title="执行 SHOW POOLS_EXTENDED 逐列强断言全量 22 个列名与初始数据结构",
        command="$ %s\n\n%s" % (res_ext.command, res_ext.output.rstrip()),
        expected="SHOW POOLS_EXTENDED 列名精确包含 22 个列 (含 6 个动态分位数列: query/tx_0.99, 0.95, 0.5)",
        actual=actual_step4,
        result="PASS" if ext_ok else "FAIL",
    )
    s4_order = rt._step_order
    rt.step_meta[s4_order] = {
        "coverage": "5",
        "coverage_check": "逐列强断言 SHOW POOLS_EXTENDED 动态分位数与吞吐列名清单",
        "checks": [
            ReportCheck(
                title="SHOW POOLS_EXTENDED 列名清单精确核对",
                expected=", ".join(expected_headers_ext),
                actual=", ".join(headers_ext),
                result="PASS" if ext_ok else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s4_order, 5, "强断言 SHOW POOLS_EXTENDED 动态分位数列名"))
    if not ext_ok:
        raise HaCommandFailure("步骤 4 校验失败: %s" % actual_step4)

    # 步骤 5: 多 Group 全拓扑路由业务读写与统计数据真实累加
    all_groups = ["mmr_group", "rep_group", "balance_group", "single_group"]
    group_query_results = []
    group_all_ok = True
    for g_idx, grp_name in enumerate(all_groups):
        g_sql = "SELECT %d AS group_probe_id; BEGIN; SELECT %d; COMMIT;" % (g_idx + 10, g_idx + 20)
        g_cmd = build_psql_command(
            rt.env.config["local"]["postgres_dir"], "127.0.0.1",
            rt.listen_port, "postgres", grp_name, g_sql,
        )
        g_res = run_logged_command(g_cmd, rt.logs_dir / ("group_biz_%s.log" % grp_name), cwd=rt.workdir)
        g_ok = (g_res.returncode == 0 and str(g_idx + 10) in g_res.output)
        if not g_ok:
            group_all_ok = False
        group_query_results.append("%s: %s" % (grp_name, "成功" if g_ok else "失败"))

    res_stats_after = run_console_query("SHOW STATS;", "show_stats_after_groups.log")
    _, rows_stats = _parse_table_data(res_stats_after.output)
    total_stat_requests = sum(int(r.get("total_query_count", 0)) for r in rows_stats if r.get("total_query_count", "").isdigit())

    step5_ok = (group_all_ok and total_stat_requests > 0)
    actual_step5 = (
        "各组业务执行结果: %s\n"
        "SHOW STATS 统计总查询数 (total_query_count): %d (> 0，证明业务请求被正确统计累加)" % (
            "; ".join(group_query_results), total_stat_requests,
        )
    )
    rt.record_step(
        title="跨 MMR/Rep/Balance/Single 全拓扑 Group 业务读写与统计累加验证",
        command="执行各 Group 业务读写并查询 SHOW STATS 累加状态",
        expected="全量 Group 路由业务查询全部成功，统计对象正确累加请求数 (total_query_count > 0)",
        actual=actual_step5,
        result="PASS" if step5_ok else "FAIL",
    )
    s5_order = rt._step_order
    rt.step_meta[s5_order] = {
        "coverage": "3",
        "coverage_check": "多 Group 全拓扑业务路由与连接池统计正常累加",
        "checks": [
            ReportCheck(
                title="全 Group 业务查询成功率",
                expected="全部成功 (returncode=0)",
                actual="; ".join(group_query_results),
                result="PASS" if group_all_ok else "FAIL",
            ),
            ReportCheck(
                title="业务统计数据真实累加",
                expected="total_query_count > 0",
                actual="total_query_count = %d" % total_stat_requests,
                result="PASS" if total_stat_requests > 0 else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s5_order, 3, "多 Group 业务路由与统计累加验证"))
    if not step5_ok:
        raise HaCommandFailure("步骤 5 校验失败: %s" % actual_step5)

    # 步骤 6: 注入真实耗时查询与事务，填充 tdigest 样本点并验证单调性
    biz_queries = [
        "SELECT pg_sleep(0.01);",
        "BEGIN; SELECT pg_sleep(0.005); COMMIT;",
        "SELECT 1;",
        "BEGIN; SELECT 1; COMMIT;",
    ]
    for q_idx, q_sql in enumerate(biz_queries):
        cmd = build_psql_command(
            rt.env.config["local"]["postgres_dir"], "127.0.0.1",
            rt.listen_port, "postgres", "mmr_group", q_sql,
        )
        res = run_logged_command(
            cmd, rt.logs_dir / ("biz_query_%d.log" % q_idx), cwd=rt.workdir,
        )
        if res.returncode != 0:
            raise HaCommandFailure("业务查询执行失败: %s" % res.output)

    res_ext_after = run_console_query("SHOW POOLS_EXTENDED;", "show_pools_extended_after.log")
    _, rows_ext_after = _parse_table_data(res_ext_after.output)

    quantile_checks_passed = True
    quantile_details = []
    for r in rows_ext_after:
        db = r.get("database", "")
        usr = r.get("user", "")
        try:
            q99 = float(r.get("query_0.99", 0))
            q95 = float(r.get("query_0.95", 0))
            q50 = float(r.get("query_0.5", 0))
            tx99 = float(r.get("tx_0.99", 0))
            tx95 = float(r.get("tx_0.95", 0))
            tx50 = float(r.get("tx_0.5", 0))
        except ValueError:
            quantile_checks_passed = False
            quantile_details.append("%s.%s: 分位数转换 float 失败" % (db, usr))
            continue

        monotonic_q = (q99 >= q95 >= q50 >= 0)
        monotonic_tx = (tx99 >= tx95 >= tx50 >= 0)
        if not (monotonic_q and monotonic_tx):
            quantile_checks_passed = False
            quantile_details.append(
                "%s.%s: 分位数单调性不满足 (query: %s, %s, %s; tx: %s, %s, %s)" % (
                    db, usr, q99, q95, q50, tx99, tx95, tx50,
                )
            )
        else:
            quantile_details.append(
                "%s.%s: query[P99=%s, P95=%s, P50=%s], tx[P99=%s, P95=%s, P50=%s]" % (
                    db, usr, q99, q95, q50, tx99, tx95, tx50,
                )
            )

    step6_ok = (res_ext_after.returncode == 0 and quantile_checks_passed and len(rows_ext_after) > 0)
    actual_step6 = (
        "SHOW POOLS_EXTENDED 返回 %d 行数据\n分位数核对详情:\n%s" % (
            len(rows_ext_after), "\n".join(quantile_details),
        )
    )
    rt.record_step(
        title="注入真实耗时查询并验证直方图分位数单调性 (P99 >= P95 >= P50)",
        command="执行业务耗时 SQL 并查询 SHOW POOLS_EXTENDED 提取分位数",
        expected="分位数数值有效非负，且满足 P99 >= P95 >= P50 单调性逻辑",
        actual=actual_step6,
        result="PASS" if step6_ok else "FAIL",
    )
    s6_order = rt._step_order
    rt.step_meta[s6_order] = {
        "coverage": "6",
        "coverage_check": "验证直方图分位数计算有效性与单调性",
        "checks": [
            ReportCheck(
                title="分位数单调性核对 (P99 >= P95 >= P50 >= 0)",
                expected="各 pool 分位数满足单调递增且非负",
                actual="; ".join(quantile_details),
                result="PASS" if quantile_checks_passed else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s6_order, 6, "验证直方图分位数单调性与有效性"))
    if not step6_ok:
        raise HaCommandFailure("步骤 6 校验失败: %s" % actual_step6)

    # 步骤 7: 4 个 Worker 并发执行 100 次 SHOW POOLS_EXTENDED 压力测试 (8.60 核心缺陷防崩溃)
    concurrency_errors = []
    def ext_query_worker(wid):
        for idx in range(25):
            res = run_console_query("SHOW POOLS_EXTENDED;", "concurrent_ext_%d_%d.log" % (wid, idx))
            if res.returncode != 0:
                concurrency_errors.append("Worker %d 第 %d 次 SHOW POOLS_EXTENDED 失败: %s" % (wid, idx, res.output))
            else:
                h, r = _parse_table_data(res.output)
                if h != expected_headers_ext:
                    concurrency_errors.append("Worker %d 第 %d 次列名不匹配" % (wid, idx))
            time.sleep(0.01)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(ext_query_worker, wid) for wid in range(1, 5)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    step7_ok = (len(concurrency_errors) == 0)
    actual_step7 = (
        "4 个 Worker 累计并发执行 100 次 SHOW POOLS_EXTENDED，直方图并发合并与临时对象释放正常，未发生空指针崩溃或段错误"
        if step7_ok else "; ".join(concurrency_errors)
    )
    rt.record_step(
        title="多客户端并发执行 100 次 SHOW POOLS_EXTENDED 压力测试 (8.60)",
        command="4 workers concurrent execution of 100 SHOW POOLS_EXTENDED queries",
        expected="并发直方图复制合并与临时对象销毁无空指针解引用、无段错误、无死锁",
        actual=actual_step7,
        result="PASS" if step7_ok else "FAIL",
    )
    s7_order = rt._step_order
    rt.step_meta[s7_order] = {
        "coverage": "7",
        "coverage_check": "高频并发执行 SHOW POOLS_EXTENDED 验证直方图合并无空指针解引用与崩溃",
        "checks": [
            ReportCheck(
                title="并发 SHOW POOLS_EXTENDED 稳定性",
                expected="100 次查询全部成功，无空指针崩溃",
                actual=actual_step7,
                result="PASS" if step7_ok else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s7_order, 7, "并发执行 SHOW POOLS_EXTENDED 压力测试"))
    if not step7_ok:
        raise HaCommandFailure("步骤 7 校验失败: %s" % actual_step7)

    return "统计辅助对象初始化、分位数配置与控制台扩展池展示稳定性 (8.60) 验证通过。"

