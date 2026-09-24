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


def _run_worker_thread_lifecycle(rt):
    """Executor for 8.62: worker 线程私有对象初始化未检查两级分配结果，启动可空指针崩溃且正常退出泄漏."""
    rt.coverage_items = [
        "默认单 Worker (workers 1) 正常业务服务基线验证：单线程启动、建连与业务查询正常",
        "配置多 worker (workers 8) 启动 fbasecman，断言所有 worker 线程私有对象两级分配成功初始化 (8.62)",
        "多 Worker 负载均衡无饥饿分发：发起 32 个并发客户端连接，验证所有 8 个 Worker 均分摊处理业务流量 (cl_total > 0)",
        "在线平滑 RELOAD 正常功能验证：在线热重载配置，Worker 线程平滑保留私有对象且后续业务查询 100% 成功",
        "发送优雅停止信号，断言所有 8 个 worker 均安全 stopped，主进程以退出码 0 退出且无资源释放泄漏 (8.62)",
        "连续 3 轮高频启停循环压力测试，验证无锁或内存泄漏导致的异常挂起或崩溃",
    ]
    rt.overview_steps = [
        "启动默认单 Worker (workers 1) 实例，验证单线程常规业务服务基线",
        "生成 workers 8 配置并启动 fbasecman 实例",
        "执行 SHOW THREAD_STATUS 核对 8 个 worker 线程运行状态与两级初始化结果",
        "发起 32 个并发客户端连接，核对所有 8 个 Worker 的 cl_total > 0，验证负载均衡与无饥饿",
        "执行控制台 RELOAD 命令，验证在线平滑热加载与后续业务连通性",
        "发送 SIGTERM 执行优雅停止，验证所有 8 个 worker 安全 stopped 且无 worker 释放失败错误",
        "连续执行 2 轮额外的完整启停生命周期压力循环，验证无资源泄漏导致的启动失败或挂起",
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

    # 步骤 1: 正常功能测试 - 默认单 Worker (workers 1) 正常业务服务基线
    def set_workers_1(content):
        return re.sub(r'workers\s+\d+', 'workers 1', content)

    rt.start(transform=set_workers_1)
    single_cmd = build_psql_command(
        rt.env.config["local"]["postgres_dir"], "127.0.0.1",
        rt.listen_port, "postgres", "mmr_group", "SELECT 101 AS single_worker_probe;",
    )
    single_res = run_logged_command(single_cmd, rt.logs_dir / "single_worker_probe.log", cwd=rt.workdir)
    single_ok = (single_res.returncode == 0 and "101" in single_res.output)
    rt.process.stop()
    time.sleep(0.3)
    single_stopped = not rt.pid_file.exists()
    step1_ok = (single_ok and single_stopped)
    actual_step1 = "单 Worker 业务查询: %s, 停止退出: %s" % (
        "成功" if single_ok else "失败", "成功" if single_stopped else "失败",
    )
    rt.record_step(
        title="默认单 Worker (workers 1) 正常业务服务基线验证",
        command="启动 workers 1 -> 执行 SELECT 101 -> 优雅停止",
        expected="单 Worker 正常启动、处理业务请求并平滑退出",
        actual=actual_step1,
        result="PASS" if step1_ok else "FAIL",
    )
    s1_order = rt._step_order
    rt.step_meta[s1_order] = {
        "coverage": "1",
        "coverage_check": "默认单 Worker (workers 1) 正常业务服务基线验证",
        "checks": [
            ReportCheck(
                title="单 Worker 业务查询正常",
                expected="returncode == 0 且返回 101",
                actual="查询成功" if single_ok else "查询失败: %s" % single_res.output,
                result="PASS" if single_ok else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s1_order, 1, "单 Worker 正常业务基线验证"))
    if not step1_ok:
        raise HaCommandFailure("步骤 1 校验失败: %s" % actual_step1)

    # 步骤 2: 多 Worker (workers 8) 启动与初始化
    worker_count = 8
    def set_workers_8(content):
        return re.sub(r'workers\s+\d+', 'workers %d' % worker_count, content)

    conf_path = rt.start(transform=set_workers_8)
    w8_start_order = rt._step_order
    rt.step_meta[w8_start_order] = {
        "coverage": "2",
        "coverage_check": "配置多 worker (workers 8) 启动 fbasecman，断言所有 worker 线程私有对象两级分配成功初始化 (8.62)",
        "checks": [
            ReportCheck(
                title="8 Worker 实例启动就绪",
                expected="fbasecman 正常启动且控制台就绪",
                actual="控制台就绪",
                result="PASS",
            ),
        ],
    }
    rt.coverage_mapping.append((w8_start_order, 2, "配置多 worker (workers 8) 启动 fbasecman"))

    # 步骤 3: 控制台 SHOW THREAD_STATUS 核对 8 个 worker 线程
    res_threads = run_console_query("SHOW THREAD_STATUS;", "show_thread_status_init.log")
    headers_threads, rows_threads = _parse_table_data(res_threads.output)

    active_thread_ids = []
    for r in rows_threads:
        tid_str = r.get("thread_id", "")
        if tid_str.isdigit():
            active_thread_ids.append(int(tid_str))

    log_content = rt.proxy_log.read_text(encoding="utf-8") if rt.proxy_log.exists() else ""
    has_init_fail = "failed to init worker thread info" in log_content

    expected_thread_ids = list(range(worker_count))
    step3_ok = (
        res_threads.returncode == 0 and
        sorted(active_thread_ids) == expected_thread_ids and
        not has_init_fail
    )
    actual_step3 = (
        "SHOW THREAD_STATUS 返回 %d 个 Worker 线程: %s (预期 0~%d)\n"
        "日志是否存在 'failed to init worker thread info': %s" % (
            len(active_thread_ids), sorted(active_thread_ids), worker_count - 1,
            "是" if has_init_fail else "否",
        )
    )
    rt.record_step(
        title="执行 SHOW THREAD_STATUS 核对 8 个 worker 线程状态与初始化 (8.62)",
        command="$ %s\n\n%s" % (res_threads.command, res_threads.output.rstrip()),
        expected="SHOW THREAD_STATUS 包含 8 个活跃 worker (ID 0~7)，且日志无两级分配失败错误",
        actual=actual_step3,
        result="PASS" if step3_ok else "FAIL",
    )
    s3_order = rt._step_order
    rt.step_meta[s3_order] = {
        "coverage": "2",
        "coverage_check": "执行 SHOW THREAD_STATUS 核对 8 个 worker 线程运行状态与两级初始化",
        "checks": [
            ReportCheck(
                title="8 个 Worker 线程状态校验 (ID 0~7)",
                expected="包含 worker 0 到 %d" % (worker_count - 1),
                actual="活跃 worker 列表: %s" % sorted(active_thread_ids),
                result="PASS" if sorted(active_thread_ids) == expected_thread_ids else "FAIL",
            ),
            ReportCheck(
                title="线程私有对象初始化无错误 (8.62)",
                expected="无 'failed to init worker thread info'",
                actual="未发现初始化错误" if not has_init_fail else "发现初始化错误",
                result="PASS" if not has_init_fail else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s3_order, 2, "核对 8 个 worker 线程状态与两级初始化"))
    if not step3_ok:
        raise HaCommandFailure("步骤 3 校验失败: %s" % actual_step3)

    # 步骤 4: 发起 32 个并发客户端连接，验证多 Worker 负载均衡无饥饿
    concurrency_errors = []
    total_clients = 32

    def client_worker(cid):
        cmd = build_psql_command(
            rt.env.config["local"]["postgres_dir"], "127.0.0.1",
            rt.listen_port, "postgres", "mmr_group", "SELECT %d AS client_id;" % cid,
        )
        res = run_logged_command(
            cmd, rt.logs_dir / ("client_worker_%d.log" % cid), cwd=rt.workdir,
        )
        if res.returncode != 0:
            concurrency_errors.append("Client %d 执行查询失败: %s" % (cid, res.output))
        elif str(cid) not in res.output:
            concurrency_errors.append("Client %d 输出未包含 client_id" % cid)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(client_worker, cid) for cid in range(1, total_clients + 1)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # 再次查询 SHOW THREAD_STATUS，检查各个 Worker 的请求分发情况
    res_threads_after = run_console_query("SHOW THREAD_STATUS;", "show_thread_status_after_load.log")
    _, rows_threads_after = _parse_table_data(res_threads_after.output)
    worker_client_totals = {}
    for r in rows_threads_after:
        tid_str = r.get("thread_id", "")
        cl_tot_str = r.get("cl_total", "0")
        if tid_str.isdigit():
            worker_client_totals[int(tid_str)] = int(cl_tot_str) if cl_tot_str.isdigit() else 0

    starved_workers = [tid for tid, tot in worker_client_totals.items() if tot == 0]
    load_balance_ok = (len(concurrency_errors) == 0 and len(starved_workers) == 0)
    actual_step4 = (
        "%d 个并发客户端连接全部成功；各 Worker 累计处理连接数 (cl_total): %s\n"
        "是否存在饥饿 Worker (cl_total == 0): %s" % (
            total_clients, json.dumps(worker_client_totals, sort_keys=True),
            "是 (%s)" % starved_workers if starved_workers else "否 (全量 Worker 均参与分摊)",
        )
    )
    rt.record_step(
        title="32 个并发客户端请求分发与 Worker 负载均衡无饥饿验证",
        command="%d clients concurrent execution of 'SELECT <id>' -> SHOW THREAD_STATUS" % total_clients,
        expected="所有 32 个客户端请求全部成功，8 个 Worker 均有连接分配 (cl_total > 0)，无线程饥饿",
        actual=actual_step4,
        result="PASS" if load_balance_ok else "FAIL",
    )
    s4_order = rt._step_order
    rt.step_meta[s4_order] = {
        "coverage": "3",
        "coverage_check": "多 Worker 负载均衡无饥饿分发验证",
        "checks": [
            ReportCheck(
                title="并发客户端查询成功率",
                expected="32/32 成功",
                actual="%d/%d 成功" % (total_clients - len(concurrency_errors), total_clients),
                result="PASS" if len(concurrency_errors) == 0 else "FAIL",
            ),
            ReportCheck(
                title="所有 Worker 均参与请求处理 (无饥饿)",
                expected="starved_workers == []",
                actual="starved_workers = %s" % starved_workers,
                result="PASS" if len(starved_workers) == 0 else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s4_order, 3, "多 Worker 负载均衡与无饥饿验证"))
    if not load_balance_ok:
        raise HaCommandFailure("步骤 4 校验失败: %s" % actual_step4)

    # 步骤 5: 在线平滑 RELOAD 正常功能验证
    res_reload = run_console_query("RELOAD;", "console_reload.log")
    reload_ok = (res_reload.returncode == 0)
    # Reload 后立即执行一次业务查询，验证 Worker 线程私有对象保持有效
    post_reload_cmd = build_psql_command(
        rt.env.config["local"]["postgres_dir"], "127.0.0.1",
        rt.listen_port, "postgres", "mmr_group", "SELECT 999 AS post_reload_probe;",
    )
    post_reload_res = run_logged_command(post_reload_cmd, rt.logs_dir / "post_reload_probe.log", cwd=rt.workdir)
    post_reload_ok = (post_reload_res.returncode == 0 and "999" in post_reload_res.output)
    step5_ok = (reload_ok and post_reload_ok)
    actual_step5 = "RELOAD 命令执行: %s (返回码=%d), 热重载后业务查询: %s" % (
        "成功" if reload_ok else "失败", res_reload.returncode, "成功" if post_reload_ok else "失败",
    )
    rt.record_step(
        title="控制台 RELOAD 在线平滑热重载与业务可用性验证",
        command="psql -c 'RELOAD;' -> SELECT 999 AS post_reload_probe;",
        expected="RELOAD 命令成功响应，Worker 线程平滑保留私有对象且后续业务读写正常",
        actual=actual_step5,
        result="PASS" if step5_ok else "FAIL",
    )
    s5_order = rt._step_order
    rt.step_meta[s5_order] = {
        "coverage": "4",
        "coverage_check": "在线平滑 RELOAD 正常功能验证",
        "checks": [
            ReportCheck(
                title="RELOAD 命令响应正常",
                expected="returncode == 0",
                actual="returncode = %d" % res_reload.returncode,
                result="PASS" if reload_ok else "FAIL",
            ),
            ReportCheck(
                title="RELOAD 后业务连通性正常",
                expected="returncode == 0 且返回 999",
                actual="业务正常" if post_reload_ok else "业务异常: %s" % post_reload_res.output,
                result="PASS" if post_reload_ok else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s5_order, 4, "在线平滑 RELOAD 验证"))
    if not step5_ok:
        raise HaCommandFailure("步骤 5 校验失败: %s" % actual_step5)

    # 步骤 6: 优雅停止并断言所有 Worker 安全退出与资源释放
    rt.process.stop()
    time.sleep(0.5)

    log_content_after_stop = rt.proxy_log.read_text(encoding="utf-8") if rt.proxy_log.exists() else ""
    has_shutdown_log = "Stopping Fbasecman" in log_content_after_stop
    has_free_fail = "failed to free worker thread info" in log_content_after_stop
    process_stopped = not rt.pid_file.exists()

    step6_ok = (process_stopped and has_shutdown_log and not has_free_fail)
    actual_step6 = (
        "进程正常优雅停止: %s\n"
        "日志包含 'Stopping Fbasecman': %s\n"
        "日志是否存在 'failed to free worker thread info' (8.62 释放失败): %s" % (
            "成功" if process_stopped else "失败",
            "是" if has_shutdown_log else "否",
            "是" if has_free_fail else "否",
        )
    )
    rt.record_step(
        title="发送 SIGTERM 执行优雅停止，验证所有 8 个 Worker 退出与资源释放 (8.62)",
        command="kill -15 <fbasecman_pid>",
        expected="主进程正常优雅停止，日志记录 Stopping Fbasecman 且无 worker 释放失败错误",
        actual=actual_step6,
        result="PASS" if step6_ok else "FAIL",
    )
    s6_order = rt._step_order
    rt.step_meta[s6_order] = {
        "coverage": "5",
        "coverage_check": "发送优雅停止信号断言所有 worker 安全退出且无资源释放泄漏",
        "checks": [
            ReportCheck(
                title="进程优雅退出验证",
                expected="process_stopped == True",
                actual="process_stopped = %s" % process_stopped,
                result="PASS" if process_stopped else "FAIL",
            ),
            ReportCheck(
                title="停止关机日志记录",
                expected="包含 'Stopping Fbasecman'",
                actual="已记录 Stopping Fbasecman" if has_shutdown_log else "未找到 Stopping Fbasecman",
                result="PASS" if has_shutdown_log else "FAIL",
            ),
            ReportCheck(
                title="Worker 资源释放无错误 (8.62)",
                expected="无 'failed to free worker thread info'",
                actual="未发生释放错误" if not has_free_fail else "发生释放错误",
                result="PASS" if not has_free_fail else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s6_order, 5, "优雅停止与 worker 安全退出验证"))
    if not step6_ok:
        raise HaCommandFailure("步骤 6 校验失败: %s" % actual_step6)

    # 步骤 7: 连续 2 轮额外的完整启停生命周期压力循环
    cycle_results = []
    for cycle in range(2, 4):
        rt.start(transform=set_workers_8)
        time.sleep(0.2)
        cmd = build_psql_command(
            rt.env.config["local"]["postgres_dir"], "127.0.0.1",
            rt.listen_port, "postgres", "mmr_group", "SELECT %d;" % cycle,
        )
        res = run_logged_command(
            cmd, rt.logs_dir / ("cycle_%d_query.log" % cycle), cwd=rt.workdir,
        )
        q_ok = (res.returncode == 0)
        rt.process.stop()
        time.sleep(0.3)
        c_stopped = not rt.pid_file.exists()
        c_ok = (q_ok and c_stopped)
        cycle_results.append("轮次 %d: 查询=%s, 停止=%s" % (cycle, "成功" if q_ok else "失败", "成功" if c_stopped else "失败"))
        if not c_ok:
            break

    step7_ok = (len(cycle_results) == 2 and all("成功" in r for r in cycle_results))
    actual_step7 = "; ".join(cycle_results)
    rt.record_step(
        title="连续执行 2 轮高频启停生命周期压力循环 (第 2、3 轮)",
        command="2 additional cycles of: start(workers=8) -> query -> stop()",
        expected="每一轮启停循环均 100% 成功，无内存/互斥锁泄漏引发的段错误或死锁挂起",
        actual=actual_step7,
        result="PASS" if step7_ok else "FAIL",
    )
    s7_order = rt._step_order
    rt.step_meta[s7_order] = {
        "coverage": "6",
        "coverage_check": "连续执行高频启停生命周期循环压力测试",
        "checks": [
            ReportCheck(
                title="启停生命周期循环压力测试",
                expected="全量轮次成功启动、服务并退出",
                actual=actual_step7,
                result="PASS" if step7_ok else "FAIL",
            ),
        ],
    }
    rt.coverage_mapping.append((s7_order, 6, "连续高频启停生命周期压力循环"))
    if not step7_ok:
        raise HaCommandFailure("步骤 7 校验失败: %s" % actual_step7)

    return "worker 线程私有对象两级初始化、启停生命周期与资源释放 (8.62) 验证通过。"

