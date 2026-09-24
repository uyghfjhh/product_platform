"""Helper classes and utilities for the common test suite."""


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
from .manifest import COMMON_CASES, case_items, find_case, validate_manifest


LOCALE_CONF_PATH = Path("/etc/locale.conf")

EXPECTED_COLUMNS = {
    "SHOW POOLS": {
        "zh": [
            "源节点别名", "组名", "后端数据库", "后端用户", "连接池模式",
            "活跃客户端数", "空闲客户端数", "活跃服务端连接数", "空闲服务端连接数",
            "测试服务端连接数", "总请求数", "每秒请求数", "客户端活跃率",
        ],
        "en": [
            "node_name", "group_name", "database", "user", "pool_mode",
            "cl_active", "cl_idle", "sv_active", "sv_idle",
            "sv_tested", "total_requests", "request_per_sec", "cl_active_ratio",
        ],
    },
    "SHOW GROUPS": {
        "zh": [
            "组名", "组模式", "后端数据库名", "用户列表", "读写分离模式列表",
            "access_mode", "后端集群列表", "配置检查策略", "正常写中心",
            "提升写中心", "多活真实组名", "组UUID", "写端口",
        ],
        "en": [
            "group_name", "group_mode", "storage_db", "user_names", "rw_split_methods",
            "access_mode", "backend_clusters", "config_check", "write_cluster",
            "promoted_cluster", "real_group_name", "group_uuid", "write_port",
        ],
    },
    "SHOW NODES": {
        "zh": [
            "源节点别名", "集群名", "IP", "端口", "后端数据库名",
            "权重", "配置状态", "有效角色", "当前主节点", "有效状态", "不可用原因",
        ],
        "en": [
            "node_name", "cluster_name", "host", "port", "storage_db",
            "weight", "config_status", "effective_role", "current_primary",
            "effective_status", "unavailable_reason",
        ],
    },
    "SHOW SERVERS": {
        "zh": [
            "源节点别名", "组名", "后端用户", "后端数据库", "状态",
            "离线", "离线原因", "规则是否过时", "IP", "端口",
            "连接池IP", "连接池端口", "创建时间", "最近绑定时间", "最近解绑时间",
            "总绑定时间", "发送字节数", "接收字节数", "读请求数", "写请求数",
            "TLS", "路由配置代次", "标识符",
        ],
        "en": [
            "node_name", "group_name", "user", "database", "state",
            "offline", "offline_reason", "rule_obsolete", "addr", "port",
            "local_addr", "local_port", "create_time", "last_attach_time", "last_detach_time",
            "total_attach_time", "sent_bytes", "received_bytes", "read_request", "write_request",
            "tls", "route_version", "ptr",
        ],
    },
}


class LocaleManager(object):
    """Safely manage /etc/locale.conf backup, modification, and restoration."""

    def __init__(self):
        self.orig_exists = LOCALE_CONF_PATH.exists()
        self.orig_content = None
        if self.orig_exists:
            try:
                self.orig_content = LOCALE_CONF_PATH.read_text(encoding="utf-8")
            except Exception:
                res = subprocess.run(
                    ["sudo", "-n", "cat", str(LOCALE_CONF_PATH)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                )
                if res.returncode == 0:
                    self.orig_content = res.stdout

    def set_locale(self, content):
        script = "cat << 'EOF' > /etc/locale.conf\n%s\nEOF\n" % content.strip()
        cmd = ["sudo", "-n", "sh", "-c", script]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if res.returncode != 0:
            raise RuntimeError("failed to write /etc/locale.conf: %s" % res.stderr)
        time.sleep(0.1)

    def restore(self):
        if self.orig_exists and self.orig_content is not None:
            self.set_locale(self.orig_content)
        elif not self.orig_exists:
            subprocess.run(["sudo", "-n", "rm", "-f", str(LOCALE_CONF_PATH)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.05)


def _parse_headers(output):
    """Extract column headers from psql table output."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if i + 1 < len(lines) and lines[i + 1].startswith("-"):
            return [col.strip() for col in line.split("|")]
    return []


def _has_chinese(text):
    return any('\u4e00' <= char <= '\u9fff' for char in text)


class CommonRuntime(HaCommandRuntime):
    """Execution context and high-quality report generation for the common suite."""

    def __init__(self, root, case):
        super(CommonRuntime, self).__init__(root, case)
        self.coverage_items = []
        self.coverage_mapping = []
        self.overview_steps = []
        self.step_meta = {}

    def assert_console_columns(self, sql, cmd_name, expected_lang, round_title, cov_id, cov_desc):
        """Execute SHOW command and perform strict 1:1 assertion on column names."""
        command = build_psql_command(
            self.env.config["local"]["postgres_dir"], "127.0.0.1",
            self.listen_port, "admin", "console", sql,
        )
        step_title = "%s - 验证 %s 列名清单" % (round_title, cmd_name)
        lang_name = "中文" if expected_lang == "zh" else "纯英文"
        expected_headers = EXPECTED_COLUMNS[cmd_name][expected_lang]

        result = run_logged_command(
            command,
            self.logs_dir / ("psql_cols_%03d.log" % self._next_order()),
            cwd=self.workdir,
        )
        output = result.output.rstrip() or "<empty>"
        actual_headers = _parse_headers(output)

        passed = (result.returncode == 0 and actual_headers == expected_headers)
        if not passed:
            diff_msg = []
            if len(actual_headers) != len(expected_headers):
                diff_msg.append("列数不符: 预期 %d 列, 实际 %d 列" % (len(expected_headers), len(actual_headers)))
            missing = [c for c in expected_headers if c not in actual_headers]
            if missing:
                diff_msg.append("缺失列: %s" % missing)
            extra = [c for c in actual_headers if c not in expected_headers]
            if extra:
                diff_msg.append("多余/未翻译列: %s" % extra)
            for idx, (exp, act) in enumerate(zip(expected_headers, actual_headers)):
                if exp != act:
                    diff_msg.append("第 %d 列不匹配: 预期 '%s', 实际 '%s'" % (idx + 1, exp, act))
            actual_desc = "列名校验失败: " + "; ".join(diff_msg) + ("\n实际列名: [%s]" % ", ".join(actual_headers))
        else:
            actual_desc = "列名校验通过，全部 %d 个列名与预期严格一致:\n[%s]" % (
                len(actual_headers), ", ".join(actual_headers),
            )

        self.record_step(
            title=step_title,
            command="$ %s\n\n%s" % (result.command, output),
            expected="精确匹配预期的 %d 个%s列名:\n[%s]" % (
                len(expected_headers), lang_name, ", ".join(expected_headers),
            ),
            actual=actual_desc,
            result="PASS" if passed else "FAIL",
        )
        order = self._step_order
        self.step_meta[order] = {
            "coverage": str(cov_id),
            "coverage_check": cov_desc,
            "checks": [
                ReportCheck(
                    title="%s 列名清单精确比对 (%s)" % (cmd_name, lang_name),
                    expected=", ".join(expected_headers),
                    actual=", ".join(actual_headers),
                    result="PASS" if passed else "FAIL",
                ),
            ],
        }
        self.coverage_mapping.append((order, cov_id, "%s %s" % (round_title, cmd_name)))
        if not passed:
            raise HaCommandFailure("%s: %s" % (step_title, actual_desc))

    def write_report(self, status, reason=None):
        timeline = []
        for item in self.steps:
            order = item["order"]
            meta = self.step_meta.get(order, {})
            step = ReportStep(
                title=item["title"],
                details=item["details"],
                execution=([{"label": "实际执行", "text": item["command"]}] if item["command"] else []),
                key_expected=item["expected"],
                actual=item["actual"],
                result=item["result"],
                coverage=meta.get("coverage"),
                coverage_check=meta.get("coverage_check"),
                checks=meta.get("checks", []),
            )
            timeline.append((order, step))

        for item in self.step_journal.steps:
            order = item.get("order", 0)
            meta = self.step_meta.get(order, {})
            step = ReportStep(
                title=item["title"],
                execution=item.get("execution", []),
                intermediate=item.get("intermediate", []),
                evidence=item.get("evidence", []),
                key_expected=item.get("expected"),
                actual=item.get("actual"),
                result=item.get("result"),
                coverage=meta.get("coverage"),
                coverage_check=meta.get("coverage_check"),
                checks=meta.get("checks", []),
            )
            timeline.append((order, step))

        items = [step for _, step in sorted(timeline, key=lambda val: val[0])]

        group_clusters = {
            "mmr_group": ("pg_cluster_1", "pg_cluster_2"),
            "rep_group": ("pg_cluster_1",),
            "balance_group": ("pg_cluster_1", "pg_cluster_2"),
            "single_group": ("pg_cluster_1",),
        }
        relevant_clusters = set()
        for group in self.case.report_groups:
            relevant_clusters.update(group_clusters.get(group, ()))
        report_datasources = [
            item for item in getattr(self, "datasource_metadata", [])
            if self.case.report_all_datasources or item["cluster"] in relevant_clusters
        ]
        datasource_lines = [
            "datasource %s: host=%s port=%s cluster=%s application_name=%s "
            "system_identifier=%s status=%s" % (
                item["name"], item["host"], item["port"], item["cluster"],
                item["application_name"], item["system_identifier"],
                item["status"],
            )
            for item in report_datasources
        ]

        if self.case.name == "console_commands":
            case_configs = [
                "测试拓扑: %s" % self.case.topology,
                "控制台命令端口: %s" % self.listen_port,
                "系统 Locale 配置文件: %s" % LOCALE_CONF_PATH,
                "测试覆盖宽表命令: SHOW POOLS, SHOW GROUPS, SHOW NODES, SHOW SERVERS",
                "测试覆盖的 Locale 格式: LANG=zh_CN.UTF-8, LANG=en_US.UTF-8, 带引号格式, LC_ALL 优先级格式",
                "测试并发度: 4 个并发 Worker 持续执行 SHOW 同时由主线程驱动 Locale 动态反复热切换",
            ]
        elif self.case.name == "err_logger_rotation":
            case_configs = [
                "测试拓扑: %s" % self.case.topology,
                "控制台命令端口: %s" % self.listen_port,
                "覆盖命令: SHOW ERRORS, SHOW ERRORS_PER_ROUTE",
                "错误注入类型: 前端密码认证失败 (OD_RULE_AUTH_MD5)、未知数据库连接错误",
                "错误统计轮换机制: cron 周期性调用 od_err_logger_inc_interval() 轮换时间桶",
                "并发压力: 4 个并发 Worker 持续注入 120 次错误 + 15 轮控制台 SHOW 查询",
                "核心校验: 验证选桶递增与清零发布同一临界区 (8.59)，错误计数守恒且绝不异常清零",
            ]
        elif self.case.name == "route_stats_quantiles":
            case_configs = [
                "测试拓扑: %s" % self.case.topology,
                "控制台命令端口: %s" % self.listen_port,
                "分位数配置: quantiles \"0.99,0.95,0.5\"",
                "覆盖命令: SHOW POOLS, SHOW POOLS_EXTENDED",
                "动态分位数列: query_0.99, tx_0.99, query_0.95, tx_0.95, query_0.5, tx_0.5",
                "吞吐与连接统计列: bytes_received, bytes_sent, tcp_conn_count",
                "并发压力: 4 个并发 Worker 执行 100 次 SHOW POOLS_EXTENDED 验证直方图合并稳定性 (8.60)",
            ]
        elif self.case.name == "worker_thread_lifecycle":
            case_configs = [
                "测试拓扑: %s" % self.case.topology,
                "配置 Worker 数量: workers 8",
                "线程私有对象: od_thread_global 与 od_conn_eject_info 两级初始化 (8.62)",
                "并发服务验证: 8 个并发客户端连接分发与业务查询执行",
                "优雅退出机制: 发送 SIGTERM，验证各 worker stopped 与 od_worker_free_thread_global 释放",
                "生命周期压力: 连续 3 轮启停循环压力测试，验证无互斥锁或内存泄漏导致的崩溃或死锁",
            ]
        else:
            case_configs = [
                "测试拓扑: %s" % self.case.topology,
                "控制台命令端口: %s" % self.listen_port,
            ]

        config_lines = case_configs + [
            "手动启动命令: %s %s --console --log_to_stdout" % (
                shlex.quote(str(self.process.binary)),
                shlex.quote(str(self.active_conf or (self.workdir / (self.case.name + ".conf")))),
            ),
        ] + datasource_lines

        doc = ReportDocument(
            target=self.case.target,
            status=status,
            started_at=self.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            finished_at=(self.finished_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
            purpose=self.case.summary,
            config_lines=config_lines,
            coverage_items=self.coverage_items,
            coverage_mapping=self.coverage_mapping,
            coverage_title="测试内容",
            overview_steps=self.overview_steps,
            steps=items,
            pass_reason=reason if status == "PASS" else None,
            failure_reason=reason if status == "FAIL" else None,
        )

        from framework.persistence.atomic import atomic_write_text
        atomic_write_text(self.run_root / "report.txt", render_report(doc))

        summary_data = {
            "target": self.case.target,
            "status": status,
            "reason": reason,
            "source_sections": self.case.source_sections,
        }
        (self.run_root / "summary.json").write_text(
            json.dumps(summary_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )



def _parse_table_data(output):
    """Parse psql table output into headers and a list of row dicts."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    header_idx = -1
    for i, line in enumerate(lines):
        if i + 1 < len(lines) and lines[i + 1].startswith("-"):
            header_idx = i
            break
    if header_idx == -1:
        return [], []
    headers = [col.strip() for col in lines[header_idx].split("|")]
    rows = []
    for line in lines[header_idx + 2:]:
        if line.startswith("(") and ("row" in line or "rows" in line) and line.endswith(")"):
            break
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells)))
    return headers, rows

