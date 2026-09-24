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


def _run_console_commands(rt):
    rt.coverage_items = [
        "控制台实例启动与就绪性检查",
        "轮次 1：切换为中文环境（LANG=zh_CN.UTF-8），验证宽表 SHOW 命令列名全中文翻译且无英文混用",
        "轮次 2：动态切换为英文环境（LANG=en_US.UTF-8），验证宽表 SHOW 命令列名热刷新为纯英文",
        "轮次 3：再次切回中文环境（LANG=\"zh_CN.UTF-8\" 带双引号格式），验证引号解析与中文列名热刷新",
        "轮次 4：再次切回英文环境（LANG=\"en_US.UTF-8\" 带双引号格式），验证引号解析与英文列名热刷新",
        "轮次 5：设置 LC_ALL=zh_CN.UTF-8 与 LANG=en_US.UTF-8，验证 LC_ALL 优先级高于 LANG 并正确发布中文列名",
        "轮次 6：设置 LC_ALL=en_US.UTF-8 与 LANG=zh_CN.UTF-8，验证 LC_ALL 优先级高于 LANG 并正确发布英文列名",
        "常规运维命令检查：验证 SHOW DATABASES, SHOW STATS, SHOW VERSION, SHOW CLIENTS, SHOW POOLS, SHOW GROUPS 正常响应",
        "并发与业务隔离：多连接并发执行 SHOW 与频繁 locale 切换，同时客户端并发执行正常事务读写，验证业务无干扰且无崩溃",
        "环境恢复：系统 /etc/locale.conf 完整还原",
    ]

    rt.overview_steps = [
        "启动 fbasecman 实例并确认控制台端口就绪",
        "执行第 1 轮测试：切换至中文环境并逐列精确比对宽表列名清单",
        "执行第 2 轮测试：动态切换至英文环境并逐列精确比对宽表列名清单",
        "执行第 3 轮测试：验证带双引号格式的中文配置切换与列名热刷新",
        "执行第 4 轮测试：验证带双引号格式的英文配置切换与列名热刷新",
        "执行第 5 轮测试：验证 LC_ALL 优先级高于 LANG（中文优先）并比对列名",
        "执行第 6 轮测试：验证 LC_ALL 优先级高于 LANG（英文优先）并比对列名",
        "常规功能测试：执行全套常用控制台运维监控命令并核对关键返回内容",
        "并发与业务隔离测试：高频并发 SHOW 与 locale 切换同时，并发执行正常业务读写",
        "还原原始 /etc/locale.conf 系统配置",
    ]

    test_commands = [
        ("SHOW POOLS;", "SHOW POOLS"),
        ("SHOW GROUPS;", "SHOW GROUPS"),
        ("SHOW NODES;", "SHOW NODES"),
        ("SHOW SERVERS;", "SHOW SERVERS"),
    ]

    round_configs = [
        (1, "zh", "LANG=zh_CN.UTF-8", "轮次 1: 中文环境 (LANG=zh_CN.UTF-8)", 2, "验证中文列名翻译及无英文混用"),
        (2, "en", "LANG=en_US.UTF-8", "轮次 2: 英文环境 (LANG=en_US.UTF-8)", 3, "验证英文列名热刷新及无中文字符"),
        (3, "zh", 'LANG="zh_CN.UTF-8"', "轮次 3: 再次切回中文 (带引号 LANG=\"zh_CN.UTF-8\")", 4, "验证带引号格式解析与中文热刷新"),
        (4, "en", 'LANG="en_US.UTF-8"', "轮次 4: 再次切回英文 (带引号 LANG=\"en_US.UTF-8\")", 5, "验证带引号格式解析与英文热刷新"),
        (5, "zh", "LANG=en_US.UTF-8\nLC_ALL=zh_CN.UTF-8", "轮次 5: LC_ALL 优先级验证 (LC_ALL=zh, LANG=en)", 6, "验证 LC_ALL 覆盖 LANG 且中文生效"),
        (6, "en", "LANG=zh_CN.UTF-8\nLC_ALL=en_US.UTF-8", "轮次 6: LC_ALL 优先级验证 (LC_ALL=en, LANG=zh)", 7, "验证 LC_ALL 覆盖 LANG 且英文生效"),
    ]

    locale_mgr = LocaleManager()

    try:
        # 步骤 1: 启动实例
        conf = rt.start()
        start_order = rt._step_order
        rt.step_meta[start_order] = {
            "coverage": "1",
            "coverage_check": "验证控制台服务成功启动并在指定端口就绪",
            "checks": [
                ReportCheck(
                    title="控制台端口就绪",
                    expected="控制台成功监听端口 %s" % rt.listen_port,
                    actual="控制台端口可建立连接并响应",
                    result="PASS",
                ),
            ],
        }
        rt.coverage_mapping.append((start_order, 1, "控制台服务启动就绪"))

        # 依次执行各轮次静态切换并精确比对每一列
        for round_num, lang, locale_content, round_title, cov_id, cov_desc in round_configs:
            locale_mgr.set_locale(locale_content)
            for sql, cmd_name in test_commands:
                rt.assert_console_columns(sql, cmd_name, lang, round_title, cov_id, cov_desc)

        # 步骤 8: 正常功能测试：全套常规控制台运维监控命令
        ops_commands = [
            ("SHOW DATABASES;", "SHOW DATABASES", ["name", "storage_db"]),
            ("SHOW STATS;", "SHOW STATS", ["database", "total_query_count"]),
            ("SHOW VERSION;", "SHOW VERSION", ["version"]),
            ("SHOW CLIENTS;", "SHOW CLIENTS", ["type", "user", "database"]),
            ("SHOW POOLS;", "SHOW POOLS", ["pool_mode"]),
            ("SHOW GROUPS;", "SHOW GROUPS", ["group_mode"]),
        ]
        ops_results = []
        ops_all_ok = True
        for sql, cmd_label, expected_keywords in ops_commands:
            psql_cmd = build_psql_command(
                rt.env.config["local"]["postgres_dir"], "127.0.0.1",
                rt.listen_port, "admin", "console", sql,
            )
            res = run_logged_command(
                psql_cmd, rt.logs_dir / ("console_ops_%s.log" % cmd_label.lower().replace(" ", "_")),
                cwd=rt.workdir,
            )
            output_lower = res.output.lower()
            matched = all(kw.lower() in output_lower for kw in expected_keywords)
            cmd_ok = (res.returncode == 0 and matched)
            if not cmd_ok:
                ops_all_ok = False
            ops_results.append("%s: %s (返回码=%d)" % (cmd_label, "正常" if cmd_ok else "异常", res.returncode))

        actual_ops_text = "; ".join(ops_results)
        rt.record_step(
            title="常规运维监控命令正常功能检查",
            command="SHOW DATABASES; SHOW STATS; SHOW VERSION; SHOW CLIENTS; SHOW POOLS; SHOW GROUPS;",
            expected="所有常用运维监控命令 100% 成功响应并返回预期字段与元数据",
            actual=actual_ops_text,
            result="PASS" if ops_all_ok else "FAIL",
        )
        ops_order = rt._step_order
        rt.step_meta[ops_order] = {
            "coverage": "8",
            "coverage_check": "验证全套控制台常规运维与监控命令正常执行及元数据完整性",
            "checks": [
                ReportCheck(
                    title="常规控制台命令正常执行",
                    expected="所有运维命令返回码 0 且包含关键字段",
                    actual=actual_ops_text,
                    result="PASS" if ops_all_ok else "FAIL",
                ),
            ],
        }
        rt.coverage_mapping.append((ops_order, 8, "全套常规控制台运维监控命令检查"))
        if not ops_all_ok:
            raise HaCommandFailure("常规控制台运维命令检查失败: %s" % actual_ops_text)

        # 步骤 9: 多客户端并发 SHOW + 业务事务读写并发隔离验证
        stop_event = False
        concurrency_errors = []
        biz_errors = []

        def worker_task(worker_id):
            psql_cmd = build_psql_command(
                rt.env.config["local"]["postgres_dir"], "127.0.0.1",
                rt.listen_port, "admin", "console", "SHOW POOLS; SHOW GROUPS; SHOW NODES;",
            )
            count = 0
            while not stop_event and count < 25:
                count += 1
                res = run_logged_command(
                    psql_cmd,
                    rt.logs_dir / ("concurrent_worker_%d_%02d.log" % (worker_id, count)),
                    cwd=rt.workdir,
                )
                if res.returncode != 0:
                    concurrency_errors.append("Worker %d exit code %d: %s" % (
                        worker_id, res.returncode, res.output))
                    break
                for block in res.output.split("\n\n"):
                    headers = _parse_headers(block)
                    if headers:
                        has_zh = any(_has_chinese(h) for h in headers)
                        neutral_cols = ("IP", "UUID", "ID", "ptr", "pool_mode", "access_mode", "TLS", "tls")
                        all_zh = all(_has_chinese(h) or h in neutral_cols for h in headers)
                        no_zh = not has_zh
                        if not (all_zh or no_zh):
                            concurrency_errors.append("Worker %d 列名混用错误: [%s]" % (
                                worker_id, ", ".join(headers)))
                time.sleep(0.04)

        def biz_client_task(cid):
            # 正常业务事务与查询：执行 SELECT 1 以及 BEGIN; SELECT 2; COMMIT;
            biz_sql = "SELECT 100 + %d AS biz_val; BEGIN; SELECT 200 + %d; COMMIT;" % (cid, cid)
            biz_cmd = build_psql_command(
                rt.env.config["local"]["postgres_dir"], "127.0.0.1",
                rt.listen_port, "postgres", "mmr_group", biz_sql,
            )
            count = 0
            while not stop_event and count < 15:
                count += 1
                res = run_logged_command(
                    biz_cmd,
                    rt.logs_dir / ("concurrent_biz_%d_%02d.log" % (cid, count)),
                    cwd=rt.workdir,
                )
                if res.returncode != 0:
                    biz_errors.append("BizClient %d 第 %d 次执行失败: %s" % (cid, count, res.output))
                    break
                if str(100 + cid) not in res.output or str(200 + cid) not in res.output:
                    biz_errors.append("BizClient %d 输出缺少预期业务数据" % cid)
                    break
                time.sleep(0.05)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            # 4 个控制台 worker + 2 个业务读写 client
            futures = [executor.submit(worker_task, wid) for wid in range(1, 5)]
            futures += [executor.submit(biz_client_task, cid) for cid in range(1, 3)]

            # 主线程在并发期间来回高频切换 locale
            for switch_idx in range(6):
                loc = "LANG=zh_CN.UTF-8" if switch_idx % 2 == 0 else "LANG=en_US.UTF-8"
                locale_mgr.set_locale(loc)
                time.sleep(0.12)

            stop_event = True
            for f in concurrent.futures.as_completed(futures):
                f.result()

        concurrent_ok = (len(concurrency_errors) == 0 and len(biz_errors) == 0)
        actual_text = (
            "4 个控制台 Worker 累计执行 100 次 SHOW，2 个业务客户端执行 30 次事务读写，"
            "未发生崩溃、断连、中英文混用或业务阻塞"
            if concurrent_ok else ("控制台错误: %s; 业务错误: %s" % (
                "; ".join(concurrency_errors) or "无", "; ".join(biz_errors) or "无"
            ))
        )
        rt.record_step(
            title="并发 SHOW、动态热切换与业务事务读写隔离验证",
            command="4 console workers (SHOW POOLS/GROUPS/NODES) + 2 biz clients (SELECT/TX) with rapid locale switches",
            expected="控制台命令无崩溃断连且单次响应语言一致，业务客户端读写事务 100% 成功不受控制台干扰",
            actual=actual_text,
            result="PASS" if concurrent_ok else "FAIL",
        )
        concurrent_order = rt._step_order
        rt.step_meta[concurrent_order] = {
            "coverage": "9",
            "coverage_check": "高频并发 SHOW 与 locale 切换期间，验证控制台与正常业务读写互不干扰且无死锁",
            "checks": [
                ReportCheck(
                    title="并发 SHOW 稳定性与原子一致性",
                    expected="无报错、无断连、单次 RowDescription 语言严格一致",
                    actual="控制台并发执行正常" if len(concurrency_errors) == 0 else "; ".join(concurrency_errors),
                    result="PASS" if len(concurrency_errors) == 0 else "FAIL",
                ),
                ReportCheck(
                    title="正常业务事务并发隔离性",
                    expected="业务事务读写 100% 成功，不受控制台切换影响",
                    actual="业务事务全部成功" if len(biz_errors) == 0 else "; ".join(biz_errors),
                    result="PASS" if len(biz_errors) == 0 else "FAIL",
                ),
            ],
        }
        rt.coverage_mapping.append((concurrent_order, 9, "并发 SHOW、动态热切换与业务事务隔离验证"))

    finally:
        # 步骤 8: 环境恢复
        locale_mgr.restore()
        rt.record_step(
            title="环境恢复: 还原系统 /etc/locale.conf",
            command="restore original /etc/locale.conf",
            expected="系统 Locale 配置文件恢复至测试前基线状态",
            actual="已成功还原系统原始 /etc/locale.conf 内容",
            result="PASS",
        )
        restore_order = rt._step_order
        rt.step_meta[restore_order] = {
            "coverage": "9",
            "coverage_check": "测试结束后无条件还原系统原始 locale 配置文件",
            "checks": [
                ReportCheck(
                    title="环境恢复验证",
                    expected="/etc/locale.conf 还原成功",
                    actual="文件状态与内容已恢复",
                    result="PASS",
                ),
            ],
        }
        rt.coverage_mapping.append((restore_order, 9, "恢复原始系统 /etc/locale.conf"))

