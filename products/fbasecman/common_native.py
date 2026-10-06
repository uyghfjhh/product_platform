"""Platform SDK hosts for the fbasecman common regression suite."""

from __future__ import annotations

import concurrent.futures
import json
import re
import time

from platform_regress.sdk import CaseContext
from products.fbasecman.native import _business_query, _console_query, render_config


EXPECTED_COLUMNS = {
    "SHOW POOLS": {
        "zh": ["源节点别名", "组名", "后端数据库", "后端用户", "连接池模式", "活跃客户端数", "空闲客户端数", "活跃服务端连接数", "空闲服务端连接数", "测试服务端连接数", "总请求数", "每秒请求数", "客户端活跃率"],
        "en": ["node_name", "group_name", "database", "user", "pool_mode", "cl_active", "cl_idle", "sv_active", "sv_idle", "sv_tested", "total_requests", "request_per_sec", "cl_active_ratio"],
    },
    "SHOW GROUPS": {
        "zh": ["组名", "组模式", "后端数据库名", "用户列表", "读写分离模式列表", "access_mode", "后端集群列表", "配置检查策略", "正常写中心", "提升写中心", "多活真实组名", "组UUID", "写端口"],
        "en": ["group_name", "group_mode", "storage_db", "user_names", "rw_split_methods", "access_mode", "backend_clusters", "config_check", "write_cluster", "promoted_cluster", "real_group_name", "group_uuid", "write_port"],
    },
    "SHOW NODES": {
        "zh": ["源节点别名", "集群名", "IP", "端口", "后端数据库名", "权重", "配置状态", "有效角色", "当前主节点", "有效状态", "不可用原因"],
        "en": ["node_name", "cluster_name", "host", "port", "storage_db", "weight", "config_status", "effective_role", "current_primary", "effective_status", "unavailable_reason"],
    },
    "SHOW SERVERS": {
        "zh": ["源节点别名", "组名", "后端用户", "后端数据库", "状态", "离线", "离线原因", "规则是否过时", "IP", "端口", "连接池IP", "连接池端口", "创建时间", "最近绑定时间", "最近解绑时间", "总绑定时间", "发送字节数", "接收字节数", "读请求数", "写请求数", "TLS", "路由配置代次", "标识符"],
        "en": ["node_name", "group_name", "user", "database", "state", "offline", "offline_reason", "rule_obsolete", "addr", "port", "local_addr", "local_port", "create_time", "last_attach_time", "last_detach_time", "total_attach_time", "sent_bytes", "received_bytes", "read_request", "write_request", "tls", "route_version", "ptr"],
    },
}


def _parse_table(output):
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for index, line in enumerate(lines[:-1]):
        if lines[index + 1].startswith("-"):
            headers = [column.strip() for column in line.split("|")]
            rows = []
            for candidate in lines[index + 2:]:
                if candidate.startswith("(") and candidate.endswith(")"):
                    break
                if "|" in candidate:
                    cells = [cell.strip() for cell in candidate.split("|")]
                    if len(cells) == len(headers):
                        rows.append(dict(zip(headers, cells)))
            return headers, rows
    return [], []


def _check(context, key, title, expected, actual, passed):
    context.step(key, title, status="PASS" if passed else "FAIL",
                 details={"intent": "verify", "expected": expected, "actual": actual,
                          "analysis": "实际结果满足声明条件" if passed else "实际结果与声明条件不符"})
    if not passed:
        raise AssertionError("%s: expected %s; actual %s" % (title, expected, actual))


class CommonCase:
    def __init__(self, name):
        self.name = name

    def run(self, context: CaseContext) -> bool:
        runners = {
            "console_commands": self._console_commands,
            "err_logger_rotation": self._err_logger_rotation,
            "route_stats_quantiles": self._route_stats_quantiles,
            "worker_thread_lifecycle": self._worker_thread_lifecycle,
        }
        return runners[self.name](context)

    def _start(self, context, transform=None):
        config = context.output_dir / "fbasecman.conf"
        port = render_config(context, config, mode="none", transform=transform)
        context.start_process([context.environment["fbasecman_bin"], str(config)],
                              ready_host=context.environment.get("local_host", "127.0.0.1"), ready_port=port,
                              timeout_seconds=90)
        return config, port, context.environment.get("psql_bin", "/usr/bin/psql")

    def _console(self, context, psql, port, sql):
        return _console_query(context, psql, port, sql)

    def _console_table(self, context, psql, port, sql):
        result = self._console(context, psql, port, sql)
        headers, rows = _parse_table(result.stdout or "")
        return result, headers, rows

    def _console_commands(self, context):
        _, port, psql = self._start(context)
        locale_path = context.environment.get("locale_conf_path", "/etc/locale.conf")
        locale_contents = context.command(["cat", locale_path], timeout_seconds=10)
        original = locale_contents.stdout if locale_contents.returncode == 0 else None

        def set_locale(content):
            result = context.command(["sudo", "-n", "tee", locale_path],
                                     input_text=content.rstrip() + "\n",
                                     timeout_seconds=10)
            if result.returncode != 0:
                raise AssertionError("failed to write %s: %s" % (locale_path, result.stdout))

        def restore():
            if original is not None:
                set_locale(original)

        context.defer_cleanup(restore, priority=100)
        rounds = (("zh", "LANG=zh_CN.UTF-8"), ("en", "LANG=en_US.UTF-8"),
                  ("zh", 'LANG="zh_CN.UTF-8"'), ("en", 'LANG="en_US.UTF-8"'),
                  ("zh", "LANG=en_US.UTF-8\nLC_ALL=zh_CN.UTF-8"),
                  ("en", "LANG=zh_CN.UTF-8\nLC_ALL=en_US.UTF-8"))
        for index, (language, content) in enumerate(rounds, 1):
            set_locale(content)
            for sql in ("SHOW POOLS", "SHOW GROUPS", "SHOW NODES", "SHOW SERVERS"):
                result, headers, _ = self._console_table(context, psql, port, sql + ";")
                expected = EXPECTED_COLUMNS[sql][language]
                _check(context, "locale-%s-%s" % (index, sql.lower().replace(" ", "-")),
                       "轮次 %s：验证 %s 列名" % (index, sql), expected, headers,
                       result.returncode == 0 and headers == expected)
        for index, (sql, fields) in enumerate((
            ("SHOW DATABASES;", ("name", "storage_db")),
            ("SHOW STATS;", ("database", "total_query_count")),
            ("SHOW VERSION;", ("version",)), ("SHOW CLIENTS;", ("type", "user", "database")),
            ("SHOW POOLS;", ("pool_mode",)), ("SHOW GROUPS;", ("group_mode",)),
        ), 1):
            result = self._console(context, psql, port, sql)
            text = (result.stdout or "").lower()
            _check(context, "ops-%s" % index, "常规运维命令 %s" % sql,
                   "returncode=0 and fields=%s" % (fields,), text,
                   result.returncode == 0 and all(field in text for field in fields))
        restore()
        return True

    def _err_logger_rotation(self, context):
        def add_user(content):
            return content + ('\nuser "auth_test_user" {\n    group_names "mmr_group"\n'
                              '    authentication "md5"\n    password "correct_secret"\n'
                              '    storage_user "postgres"\n    pool "transaction"\n'
                              '    pool_size 10\n}\n')
        _, port, psql = self._start(context, add_user)
        result, headers, _ = self._console_table(context, psql, port, "SHOW ERRORS;")
        route_result, route_headers, _ = self._console_table(
            context, psql, port, "SHOW ERRORS_PER_ROUTE;")
        _check(context, "errors-schema", "核对错误统计列名",
               "error_type,count / error_type,user,database,count",
               "%s / %s" % (headers, route_headers),
               result.returncode == 0 and headers == ["error_type", "count"]
               and route_result.returncode == 0
               and route_headers == ["error_type", "user", "database", "count"])
        for group, sql in (("mmr_group", "SELECT 1; BEGIN; SELECT 2; COMMIT;"),
                           ("rep_group", "SELECT 3;"), ("balance_group", "SELECT 4;")):
            probe = _business_query(context, psql, port, sql, group)
            if probe.returncode != 0:
                raise AssertionError("normal business query failed for %s" % group)
        _, _, baseline = self._console_table(context, psql, port, "SHOW ERRORS;")
        baseline_count = sum(int(row.get("count", 0)) for row in baseline
                             if str(row.get("count", "")).isdigit())
        _check(context, "errors-baseline", "正常业务零错误基线", 0,
               baseline_count, baseline_count == 0)
        auth = context.command([psql, "-h", context.environment.get("local_host", "127.0.0.1"), "-p", str(port),
                                "-U", "auth_test_user", "-d", "postgres", "-c", "SELECT 1;"],
                               timeout_seconds=15)
        missing = _business_query(context, psql, port, "SELECT 1;", "unknown_db_9999")
        if auth.returncode == 0 or missing.returncode == 0:
            raise AssertionError("error injection unexpectedly succeeded")
        errors = []
        def inject(worker):
            for index in range(30):
                result = _business_query(context, psql, port, "SELECT 1;",
                                         "non_existent_db_%s_%s" % (worker, index))
                if result.returncode == 0:
                    errors.append("worker %s index %s" % (worker, index))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(inject, range(1, 5)))
        time.sleep(2.5)
        final, _, rows = self._console_table(context, psql, port, "SHOW ERRORS;")
        total = sum(int(row.get("count", 0)) for row in rows
                    if str(row.get("count", "")).isdigit())
        _check(context, "errors-final", "轮换后错误计数守恒", "total > 0",
               json.dumps(rows, ensure_ascii=False),
               final.returncode == 0 and total > 0 and not errors)
        return True

    def _route_stats_quantiles(self, context):
        _, port, psql = self._start(context)
        result, headers, _ = self._console_table(context, psql, port, "SHOW POOLS_EXTENDED;")
        expected_default = ["node_name", "group_name", "database", "user", "pool_mode",
                            "cl_active", "cl_idle", "sv_active", "sv_idle", "sv_tested",
                            "total_requests", "request_per_sec", "cl_active_ratio",
                            "bytes_received", "bytes_sent", "tcp_conn_count"]
        _check(context, "quantiles-default", "未配置 quantiles 的默认列",
               expected_default, headers,
               result.returncode == 0 and headers == expected_default)
        context.stop_processes()
        def add_quantiles(content):
            content = content.replace('pool_discard no',
                                      'pool_discard no\n    quantiles "0.99,0.95,0.5"')
            return content.replace('role "admin"',
                                   'role "admin"\n    quantiles "0.99,0.95,0.5"')
        _, port, psql = self._start(context, add_quantiles)
        result, headers, _ = self._console_table(context, psql, port, "SHOW POOLS_EXTENDED;")
        expected = expected_default + ["query_0.99", "tx_0.99", "query_0.95", "tx_0.95",
                                       "query_0.5", "tx_0.5"]
        _check(context, "quantiles-columns", "核对分位数扩展列", expected, headers,
               result.returncode == 0 and headers == expected)
        for sql in ("SELECT pg_sleep(0.01);", "BEGIN; SELECT pg_sleep(0.005); COMMIT;",
                    "SELECT 1;", "BEGIN; SELECT 1; COMMIT;"):
            if _business_query(context, psql, port, sql).returncode != 0:
                raise AssertionError("quantile sample query failed")
        final, _, rows = self._console_table(context, psql, port, "SHOW POOLS_EXTENDED;")
        monotonic = bool(rows)
        for row in rows:
            try:
                monotonic = monotonic and float(row["query_0.99"]) >= float(row["query_0.95"]) >= float(row["query_0.5"]) >= 0
                monotonic = monotonic and float(row["tx_0.99"]) >= float(row["tx_0.95"]) >= float(row["tx_0.5"]) >= 0
            except (KeyError, ValueError):
                monotonic = False
        _check(context, "quantiles-values", "验证分位数单调性",
               "P99 >= P95 >= P50 >= 0", json.dumps(rows, ensure_ascii=False),
               final.returncode == 0 and monotonic)
        return True

    def _worker_thread_lifecycle(self, context):
        def workers(count):
            return lambda content: re.sub(r"workers\s+\d+", "workers %s" % count, content)
        _, port, psql = self._start(context, workers(1))
        probe = _business_query(context, psql, port, "SELECT 101 AS single_worker_probe;")
        _check(context, "worker-single", "单 Worker 业务基线", "returncode=0 and 101",
               probe.stdout, probe.returncode == 0 and "101" in probe.stdout)
        context.stop_processes()
        config, port, psql = self._start(context, workers(8))
        result, _, rows = self._console_table(context, psql, port, "SHOW THREAD_STATUS;")
        ids = sorted(int(row["thread_id"]) for row in rows
                     if str(row.get("thread_id", "")).isdigit())
        _check(context, "worker-threads", "核对 8 个 worker 线程", list(range(8)), ids,
               result.returncode == 0 and ids == list(range(8)))
        errors = []
        def client(client_id):
            result = _business_query(context, psql, port, "SELECT %s AS client_id;" % client_id)
            if result.returncode != 0 or str(client_id) not in result.stdout:
                errors.append(client_id)
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            list(executor.map(client, range(1, 33)))
        after, _, after_rows = self._console_table(context, psql, port, "SHOW THREAD_STATUS;")
        totals = {int(row["thread_id"]): int(row.get("cl_total", 0))
                  for row in after_rows if str(row.get("thread_id", "")).isdigit()
                  and str(row.get("cl_total", "")).isdigit()}
        _check(context, "worker-balance", "验证 Worker 请求分发无饥饿",
               "32 clients succeed and every cl_total > 0", totals,
               after.returncode == 0 and not errors and len(totals) == 8
               and all(value > 0 for value in totals.values()))
        reload_result = self._console(context, psql, port, "RELOAD;")
        post = _business_query(context, psql, port, "SELECT 999 AS post_reload_probe;")
        _check(context, "worker-reload", "在线 RELOAD 后业务可用",
               "reload rc=0 and query 999", post.stdout,
               reload_result.returncode == 0 and post.returncode == 0 and "999" in post.stdout)
        log_path = config.with_suffix(".log")
        if log_path.is_file():
            context.attach_file("fbasecman.log", log_path)
        return True
