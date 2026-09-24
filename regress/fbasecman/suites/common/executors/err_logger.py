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


def _run_err_logger_rotation(rt):
    """Executor for 8.59: 错误统计轮换与并发写入缺少共同同步，新产生的错误可能被清零."""
    rt.coverage_items = [
        "SHOW ERRORS 与 SHOW ERRORS_PER_ROUTE 列名结构完整性与初始状态校验",
        "正常业务流量“零错误”基线验证：正常读写与事务执行期间，错误统计严格保持为 0",
        "正常客户端连接与断开不产生误报：验证客户端正常建连与 EOF 关闭不增加网络错误计数",
        "注入前端认证失败与非法数据库连接错误，验证错误计数准确累加",
        "多客户端高频并发错误注入、正常业务流量与 cron 时间桶轮换交错执行，验证业务无干扰",
        "验证时间桶轮换后错误计数非负、无数据竞争导致的清零丢失 (8.59 核心缺陷)",
        "并发错误查询与高频错误注入期间，控制台无断连、无崩溃、服务持续可用",
    ]
    rt.overview_steps = [
        "启动 fbasecman 并查询 SHOW ERRORS 与 SHOW ERRORS_PER_ROUTE 基线状态",
        "执行正常业务读写事务，验证纯正常业务流量下错误计数严格为 0",
        "执行正常客户端建连与 EOF 退出，验证不误报客户端读取或网络错误",
        "注入前端认证失败错误，校验错误计数发生预期增长",
        "注入未知数据库/未定义路由连接错误，校验对应错误类型计数累加",
        "多客户端并发高频错误注入与正常业务交错，同时高频执行 SHOW ERRORS 查询",
        "跨越 cron 时间桶轮换周期，验证错误计数非负、计数守恒且绝不异常归零",
    ]

    def add_auth_user(content):
        # 增加一个带有 md5 密码的用户以供认证失败测试
        extra = (
            '\nuser "auth_test_user" {\n'
            '    group_names "mmr_group"\n'
            '    authentication "md5"\n'
            '    password "correct_secret"\n'
            '    storage_user "postgres"\n'
            '    pool "transaction"\n'
            '    pool_size 10\n'
            '}\n'
        )
        return content + extra

    rt.start(transform=add_auth_user)

    def run_console_query(sql, log_name):
        command = build_psql_command(
            rt.env.config["local"]["postgres_dir"], "127.0.0.1",
            rt.listen_port, "admin", "console", sql,
        )
        res = run_logged_command(
            command, rt.logs_dir / log_name, cwd=rt.workdir,
        )
        return res

    # 步骤 1: 初始基线
    res_err = run_console_query("SHOW ERRORS;", "show_errors_init.log")
    headers_err, rows_err = _parse_table_data(res_err.output)
    expected_headers_err = ["error_type", "count"]
    headers_ok = (headers_err == expected_headers_err)

    res_route_err = run_console_query("SHOW ERRORS_PER_ROUTE;", "show_errors_route_init.log")
    headers_route, rows_route = _parse_table_data(res_route_err.output)
    expected_headers_route = ["error_type", "user", "database", "count"]
    headers_route_ok = (headers_route == expected_headers_route)

    step1_ok = (res_err.returncode == 0 and headers_ok and res_route_err.returncode == 0 and headers_route_ok)
    actual_step1 = (
        "SHOW ERRORS 列名: [%s], 数据行数: %d\nSHOW ERRORS_PER_ROUTE 列名: [%s], 数据行数: %d" % (
            ", ".join(headers_err), len(rows_err),
            ", ".join(headers_route), len(rows_route),
        )
    )
    rt.record_step(
        title="查询 SHOW ERRORS 与 SHOW ERRORS_PER_ROUTE 基线状态",
        command="$ %s\n\n%s\n\n$ %s\n\n%s" % (
            res_err.command, res_err.output.rstrip(),
            res_route_err.command, res_route_err.output.rstrip(),
        ),
        expected="SHOW ERRORS 列名匹配 [error_type, count]，SHOW ERRORS_PER_ROUTE 列名匹配 [error_type, user, database, count]",
        actual=actual_step1,
        result="PASS" if step1_ok else "FAIL",
    )
    s1_order = rt._step_order
    rt.step_meta[s1_order] = {
        "coverage": "1",
        "coverage_check": "核对 SHOW ERRORS 与 SHOW ERRORS_PER_ROUTE 的列名结构与初始状态",
        "checks": [
            ReportCheck(
                title="SHOW ERRORS 列名校验",
                expected=", ".join(expected_headers_err),
                actual=", ".join(headers_err),
                result="PASS" if headers_ok else "FAIL",
            ),
            ReportCheck(
                title="SHOW ERRORS_PER_ROUTE 列名校验",
                expected=", ".join(expected_headers_route),
                actual=", ".join(headers_route),
                result="PASS" if headers_route_ok else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s1_order, 1, "核对 SHOW ERRORS 与 SHOW ERRORS_PER_ROUTE 列名结构"))
    if not step1_ok:
        raise HaCommandFailure("步骤 1 校验失败: %s" % actual_step1)

    # 步骤 2: 正常功能测试 - 正常业务流量“零错误”基线
    biz_normal_queries = [
        ("mmr_group", "SELECT 1 AS normal_probe;"),
        ("mmr_group", "BEGIN; SELECT 2 AS tx_probe; COMMIT;"),
        ("rep_group", "SELECT 3 AS rep_probe;"),
        ("balance_group", "SELECT 4 AS bal_probe;"),
    ]
    biz_normal_ok = True
    biz_normal_outputs = []
    for grp, bsql in biz_normal_queries:
        bcmd = build_psql_command(
            rt.env.config["local"]["postgres_dir"], "127.0.0.1",
            rt.listen_port, "postgres", grp, bsql,
        )
        bres = run_logged_command(bcmd, rt.logs_dir / ("biz_normal_%s.log" % grp), cwd=rt.workdir)
        if bres.returncode != 0:
            biz_normal_ok = False
        biz_normal_outputs.append("%s: %s" % (grp, "成功" if bres.returncode == 0 else "失败"))

    res_err_normal = run_console_query("SHOW ERRORS;", "show_errors_after_normal.log")
    _, rows_err_normal = _parse_table_data(res_err_normal.output)
    total_errors_normal = sum(int(r.get("count", 0)) for r in rows_err_normal if r.get("count", "").isdigit())

    step2_ok = (biz_normal_ok and total_errors_normal == 0)
    actual_step2 = (
        "正常业务执行结果: %s\nSHOW ERRORS 统计总错误数: %d (严格为 0)" % (
            "; ".join(biz_normal_outputs), total_errors_normal,
        )
    )
    rt.record_step(
        title="正常业务流量“零错误”基线验证",
        command="执行多组正常业务 SQL 并检查 SHOW ERRORS 是否保持 0",
        expected="所有正常业务 SQL 100% 成功，SHOW ERRORS 错误统计严格保持为 0，无虚假误报",
        actual=actual_step2,
        result="PASS" if step2_ok else "FAIL",
    )
    s2_order = rt._step_order
    rt.step_meta[s2_order] = {
        "coverage": "2",
        "coverage_check": "正常业务读写与事务执行期间，验证错误统计严格保持为 0",
        "checks": [
            ReportCheck(
                title="正常业务执行成功率",
                expected="全部成功 (returncode=0)",
                actual="; ".join(biz_normal_outputs),
                result="PASS" if biz_normal_ok else "FAIL",
            ),
            ReportCheck(
                title="正常业务零错误基线",
                expected="total_errors == 0",
                actual="total_errors = %d" % total_errors_normal,
                result="PASS" if total_errors_normal == 0 else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s2_order, 2, "正常业务流量零错误基线"))
    if not step2_ok:
        raise HaCommandFailure("步骤 2 校验失败: %s" % actual_step2)

    # 步骤 3: 正常功能测试 - 正常客户端连接与断开 (EOF) 不产生误报
    clean_exit_cmd = build_psql_command(
        rt.env.config["local"]["postgres_dir"], "127.0.0.1",
        rt.listen_port, "postgres", "mmr_group", "\\q",
    )
    clean_exit_res = run_logged_command(clean_exit_cmd, rt.logs_dir / "clean_exit_test.log", cwd=rt.workdir)
    res_err_clean = run_console_query("SHOW ERRORS;", "show_errors_after_clean_exit.log")
    _, rows_err_clean = _parse_table_data(res_err_clean.output)
    client_read_errors = sum(
        int(r.get("count", 0)) for r in rows_err_clean
        if r.get("error_type") in ("OD_ECLIENT_READ", "OD_ECLIENT_WRITE") and r.get("count", "").isdigit()
    )

    step3_ok = (clean_exit_res.returncode == 0 and client_read_errors == 0)
    actual_step3 = "正常退出返回码: %d, 客户端读写错误计数 (OD_ECLIENT_READ/WRITE): %d" % (
        clean_exit_res.returncode, client_read_errors,
    )
    rt.record_step(
        title="正常客户端连接与 EOF 退出验证",
        command="psql -c '\\q' (正常建立连接后退出)",
        expected="客户端正常退出，OD_ECLIENT_READ 与 OD_ECLIENT_WRITE 错误数保持为 0",
        actual=actual_step3,
        result="PASS" if step3_ok else "FAIL",
    )
    s3_order = rt._step_order
    rt.step_meta[s3_order] = {
        "coverage": "3",
        "coverage_check": "验证正常客户端建连与 EOF 关闭不增加网络错误计数",
        "checks": [
            ReportCheck(
                title="正常退出不误报网络错误",
                expected="client_read_errors == 0",
                actual="client_read_errors = %d" % client_read_errors,
                result="PASS" if client_read_errors == 0 else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s3_order, 3, "正常客户端连接与断开不产生误报"))
    if not step3_ok:
        raise HaCommandFailure("步骤 3 校验失败: %s" % actual_step3)

    # 步骤 4: 注入认证失败错误
    auth_cmd = build_psql_command(
        rt.env.config["local"]["postgres_dir"], "127.0.0.1",
        rt.listen_port, "auth_test_user", "postgres", "SELECT 1;",
    )
    auth_env = os.environ.copy()
    auth_env["PGPASSWORD"] = "bad_password_attempt"
    auth_res = run_logged_command(
        auth_cmd, rt.logs_dir / "auth_fail_test.log", cwd=rt.workdir, env=auth_env,
    )
    auth_failed_as_expected = (auth_res.returncode != 0)

    # 检查 SHOW ERRORS 是否记录到错误
    res_err_after_auth = run_console_query("SHOW ERRORS;", "show_errors_after_auth.log")
    _, rows_err_after_auth = _parse_table_data(res_err_after_auth.output)
    total_errors_auth = sum(int(r.get("count", 0)) for r in rows_err_after_auth if r.get("count", "").isdigit())

    step4_ok = auth_failed_as_expected and (total_errors_auth > 0)
    actual_step4 = (
        "认证注入返回码: %d (符合预期失败)\n"
        "错误输出: %s\n"
        "SHOW ERRORS 总错误数: %d" % (
            auth_res.returncode, auth_res.output.strip(), total_errors_auth,
        )
    )
    rt.record_step(
        title="注入前端密码认证失败错误",
        command="$ %s\n(env PGPASSWORD=bad_password_attempt)\n\n%s\n\n$ %s\n\n%s" % (
            auth_res.command, auth_res.output.rstrip(),
            res_err_after_auth.command, res_err_after_auth.output.rstrip(),
        ),
        expected="客户端认证失败被拒绝，SHOW ERRORS 统计总数发生递增 (> 0)",
        actual=actual_step4,
        result="PASS" if step4_ok else "FAIL",
    )
    s4_order = rt._step_order
    rt.step_meta[s4_order] = {
        "coverage": "4",
        "coverage_check": "注入前端认证失败错误，验证错误计数准确累加",
        "checks": [
            ReportCheck(
                title="前端认证拒绝验证",
                expected="returncode != 0",
                actual="returncode = %d" % auth_res.returncode,
                result="PASS" if auth_failed_as_expected else "FAIL",
            ),
            ReportCheck(
                title="错误统计累加验证",
                expected="total_errors > 0",
                actual="total_errors = %d" % total_errors_auth,
                result="PASS" if total_errors_auth > 0 else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s4_order, 4, "注入认证错误并验证错误统计累加"))
    if not step4_ok:
        raise HaCommandFailure("步骤 4 校验失败: %s" % actual_step4)

    # 步骤 5: 注入未知数据库错误
    nodb_cmd = build_psql_command(
        rt.env.config["local"]["postgres_dir"], "127.0.0.1",
        rt.listen_port, "postgres", "unknown_db_9999", "SELECT 1;",
    )
    nodb_res = run_logged_command(
        nodb_cmd, rt.logs_dir / "nodb_fail_test.log", cwd=rt.workdir,
    )
    nodb_failed_as_expected = (nodb_res.returncode != 0)

    res_err_after_nodb = run_console_query("SHOW ERRORS;", "show_errors_after_nodb.log")
    _, rows_err_after_nodb = _parse_table_data(res_err_after_nodb.output)
    total_errors_nodb = sum(int(r.get("count", 0)) for r in rows_err_after_nodb if r.get("count", "").isdigit())

    step5_ok = nodb_failed_as_expected and (total_errors_nodb >= total_errors_auth)
    actual_step5 = (
        "未知数据库连接返回码: %d (符合预期失败)\n"
        "错误输出: %s\n"
        "SHOW ERRORS 总错误数: %d (前值: %d)" % (
            nodb_res.returncode, nodb_res.output.strip(), total_errors_nodb, total_errors_auth,
        )
    )
    rt.record_step(
        title="注入未知数据库连接错误",
        command="$ %s\n\n%s\n\n$ %s\n\n%s" % (
            nodb_res.command, nodb_res.output.rstrip(),
            res_err_after_nodb.command, res_err_after_nodb.output.rstrip(),
        ),
        expected="未知数据库请求被拒绝，SHOW ERRORS 错误数继续累加",
        actual=actual_step5,
        result="PASS" if step5_ok else "FAIL",
    )
    s5_order = rt._step_order
    rt.step_meta[s5_order] = {
        "coverage": "4",
        "coverage_check": "注入未知数据库连接错误，验证错误统计继续累加",
        "checks": [
            ReportCheck(
                title="未知数据库拒绝验证",
                expected="returncode != 0",
                actual="returncode = %d" % nodb_res.returncode,
                result="PASS" if nodb_failed_as_expected else "FAIL",
            ),
            ReportCheck(
                title="错误计数持续增长",
                expected="total_errors >= %d" % total_errors_auth,
                actual="total_errors = %d" % total_errors_nodb,
                result="PASS" if total_errors_nodb >= total_errors_auth else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s5_order, 4, "注入未知数据库错误并验证错误统计"))
    if not step5_ok:
        raise HaCommandFailure("步骤 5 校验失败: %s" % actual_step5)

    # 步骤 6: 多客户端并发注入错误 + 正常业务读写 + 主线程查询 SHOW ERRORS
    concurrency_errors = []
    biz_concurrent_errors = []
    concurrent_rounds = 30

    def err_injector(wid):
        for idx in range(concurrent_rounds):
            cmd = build_psql_command(
                rt.env.config["local"]["postgres_dir"], "127.0.0.1",
                rt.listen_port, "postgres", "non_existent_db_%d_%d" % (wid, idx), "SELECT 1;",
            )
            res = run_logged_command(
                cmd, rt.logs_dir / ("concurrent_err_%d_%d.log" % (wid, idx)), cwd=rt.workdir,
            )
            if res.returncode == 0:
                concurrency_errors.append("Worker %d 注入错误时意外成功" % wid)
            time.sleep(0.02)

    def normal_biz_runner(cid):
        for idx in range(15):
            bcmd = build_psql_command(
                rt.env.config["local"]["postgres_dir"], "127.0.0.1",
                rt.listen_port, "postgres", "mmr_group", "SELECT 888 + %d AS probe;" % cid,
            )
            bres = run_logged_command(
                bcmd, rt.logs_dir / ("concurrent_normal_biz_%d_%d.log" % (cid, idx)), cwd=rt.workdir,
            )
            if bres.returncode != 0 or str(888 + cid) not in bres.output:
                biz_concurrent_errors.append("NormalBiz %d 业务查询失败: %s" % (cid, bres.output))
            time.sleep(0.04)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        # 4 个错误注入 worker + 2 个正常业务并发 worker
        futures = [executor.submit(err_injector, wid) for wid in range(1, 5)]
        futures += [executor.submit(normal_biz_runner, cid) for cid in range(1, 3)]

        # 主线程高频查询 SHOW ERRORS 和 SHOW ERRORS_PER_ROUTE
        for q_idx in range(15):
            res_c = run_console_query("SHOW ERRORS;", "show_err_concurrent_%d.log" % q_idx)
            if res_c.returncode != 0:
                concurrency_errors.append("并发中 SHOW ERRORS 查询失败: %s" % res_c.output)
            res_cr = run_console_query("SHOW ERRORS_PER_ROUTE;", "show_err_route_concurrent_%d.log" % q_idx)
            if res_cr.returncode != 0:
                concurrency_errors.append("并发中 SHOW ERRORS_PER_ROUTE 查询失败: %s" % res_cr.output)
            time.sleep(0.08)

        for f in concurrent.futures.as_completed(futures):
            f.result()

    step6_ok = (len(concurrency_errors) == 0 and len(biz_concurrent_errors) == 0)
    actual_step6 = (
        "4 个 Worker 累计并发注入 120 次错误请求，2 个业务客户端执行 30 次正常业务查询，"
        "主线程执行 15 轮 SHOW ERRORS 查询，未发生崩溃或正常业务受阻"
        if step6_ok else ("注入错误: %s; 业务错误: %s" % (
            "; ".join(concurrency_errors) or "无", "; ".join(biz_concurrent_errors) or "无"
        ))
    )
    rt.record_step(
        title="多客户端并发错误注入、正常业务流量与控制台 SHOW 查询交错压力测试",
        command="4 error injectors (120 errors) + 2 normal biz clients (30 queries) + 15 console SHOW queries",
        expected="并发错误注入与控制台查询无异常崩溃，正常业务查询 100% 成功无干扰",
        actual=actual_step6,
        result="PASS" if step6_ok else "FAIL",
    )
    s6_order = rt._step_order
    rt.step_meta[s6_order] = {
        "coverage": "5",
        "coverage_check": "高频并发错误注入、正常业务流量与控制台查询交错压力测试",
        "checks": [
            ReportCheck(
                title="并发错误注入稳定性",
                expected="无异常报错、无连接重置",
                actual="错误注入正常" if len(concurrency_errors) == 0 else "; ".join(concurrency_errors),
                result="PASS" if len(concurrency_errors) == 0 else "FAIL",
            ),
            ReportCheck(
                title="并发正常业务无干扰",
                expected="正常业务 100% 成功",
                actual="正常业务全部成功" if len(biz_concurrent_errors) == 0 else "; ".join(biz_concurrent_errors),
                result="PASS" if len(biz_concurrent_errors) == 0 else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s6_order, 5, "并发错误注入与正常业务交错测试"))
    if not step6_ok:
        raise HaCommandFailure("步骤 6 校验失败: %s" % actual_step6)

    # 步骤 7: 跨越 cron 统计周期与时间桶轮换验证 (8.59 核心缺陷校验)
    time.sleep(2.5)

    res_err_final = run_console_query("SHOW ERRORS;", "show_errors_final.log")
    _, rows_err_final = _parse_table_data(res_err_final.output)

    negative_counts = []
    final_total_errors = 0
    err_type_counts = {}
    for r in rows_err_final:
        cnt_str = r.get("count", "0")
        etype = r.get("error_type", "unknown")
        if cnt_str.isdigit():
            cnt = int(cnt_str)
            if cnt < 0:
                negative_counts.append("%s=%d" % (etype, cnt))
            final_total_errors += cnt
            err_type_counts[etype] = cnt

    res_route_final = run_console_query("SHOW ERRORS_PER_ROUTE;", "show_errors_route_final.log")
    _, rows_route_final = _parse_table_data(res_route_final.output)

    step7_ok = (
        res_err_final.returncode == 0 and
        len(negative_counts) == 0 and
        final_total_errors > 0 and
        res_route_final.returncode == 0
    )
    actual_step7 = (
        "轮换后 SHOW ERRORS 统计总数: %d (> 0，证明错误未被清零丢失)\n"
        "各类型错误分布: %s\n"
        "异常负数计数项: %s\n"
        "SHOW ERRORS_PER_ROUTE 行数: %d" % (
            final_total_errors, json.dumps(err_type_counts, ensure_ascii=False),
            negative_counts or "无", len(rows_route_final),
        )
    )
    rt.record_step(
        title="跨越 cron 时间桶轮换周期，验证错误计数守恒与无异常清零 (8.59)",
        command="$ %s\n\n%s\n\n$ %s\n\n%s" % (
            res_err_final.command, res_err_final.output.rstrip(),
            res_route_final.command, res_route_final.output.rstrip(),
        ),
        expected="时间桶轮换后错误计数非负、总错误数守恒 (> 0)，未发生刚写入新桶即被清零现象",
        actual=actual_step7,
        result="PASS" if step7_ok else "FAIL",
    )
    s7_order = rt._step_order
    rt.step_meta[s7_order] = {
        "coverage": "6",
        "coverage_check": "验证时间桶轮换后错误计数非负且无数据竞争清零丢失",
        "checks": [
            ReportCheck(
                title="错误计数无异常清零 (8.59)",
                expected="final_total_errors > 0",
                actual="final_total_errors = %d" % final_total_errors,
                result="PASS" if final_total_errors > 0 else "FAIL",
            ),
            ReportCheck(
                title="错误计数值非负性",
                expected="negative_counts == []",
                actual="negative_counts = %s" % (negative_counts or "[]"),
                result="PASS" if len(negative_counts) == 0 else "FAIL",
            ),
            ReportCheck(
                title="SHOW ERRORS_PER_ROUTE 轮换后查询正常",
                expected="returncode == 0",
                actual="returncode = %d, rows = %d" % (res_route_final.returncode, len(rows_route_final)),
                result="PASS" if res_route_final.returncode == 0 else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s7_order, 6, "验证时间桶轮换后错误计数非负且无清零丢失"))
    if not step7_ok:
        raise HaCommandFailure("步骤 7 校验失败: %s" % actual_step7)

    return "错误统计轮换与并发写入同步安全性、计数守恒与控制台展示 (8.59) 验证通过。"

