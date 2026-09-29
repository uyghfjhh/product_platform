"""Native fbasecman process and protocol case implementation.

The renderer consumes only the platform injected pgcluster topology.  It does
not read the legacy regression framework or its test_context file.
"""

from __future__ import annotations
import os

import json
import re
import socket
import struct
import sys
import time
from pathlib import Path

from platform_regress import Blocked, CaseContext
from platform_regress.clients import jdbc as jdbc_client


def render_config(context: CaseContext, path: Path, *, mode: str = "sql_parse",
                  transform=None) -> int:
    env = context.environment
    binary = env.get("fbasecman_bin")
    license_dir = env.get("license_dir")
    nodes = env.get("nodes") or {}
    if not binary or not license_dir or not nodes:
        raise Blocked("平台上下文缺少 fbasecman 二进制、license、拓扑或 MMR group UUID")
    port = int(env.get("proxy_port", 0))
    if not 1024 <= port <= 65535:
        raise Blocked("平台上下文缺少有效 fbasecman 监听端口")
    first = next((name for name in ("mmr1", "primary", "node1") if name in nodes), None)
    second = next((name for name in ("mmr2", "node2") if name in nodes), None)
    if not first or not second:
        raise Blocked("pgcluster 拓扑缺少两个 MMR 主节点")
    group_uuid_rows = context.sql(first, "SELECT group_uuid::text FROM fdd.mmr_group WHERE group_name='g1'").rows
    if not group_uuid_rows or not group_uuid_rows[0][0]:
        raise Blocked("数据库没有可用的 MMR g1 group UUID")
    group_uuid = group_uuid_rows[0][0]
    selected_nodes = {"pg_1": nodes[first], "pg_2": nodes[second]}
    selected_nodes.update(env.get("extra_nodes") or {})
    identifiers = {}
    for alias, node in selected_nodes.items():
        query_node = first if alias in {"pg_1", "pg_3"} else second
        rows = context.sql(query_node,
                           "SELECT system_identifier::text FROM pg_control_system()").rows
        if not rows or not rows[0][0]:
            raise Blocked(f"无法读取 {alias} system_identifier")
        identifiers[alias] = rows[0][0]
    data = [
        f'pid_file "{path.with_suffix(".pid")}"',
        'daemonize no', 'unix_socket_dir "/tmp"', 'unix_socket_mode "0644"',
        f'locks_dir "{path.parent / "locks"}"', f'license_dir "{license_dir}"',
        'priority 0', 'log_to_stdout no', 'log_syslog no',
        'log_format "%p %t %l [%i %s] (%c) %m\\n"',
        'log_syslog_ident "fbasecman"', 'log_syslog_facility "daemon"',
        'log_debug yes', 'log_config yes', 'log_session yes', 'log_query no',
        'log_stats yes', 'stats_interval 60',
        f'log_file "{path.with_suffix(".log")}"', 'log_min_messages "info"',
        'server_login_retry 5', 'cache_msg_gc_size 0', 'cache_coroutine 108',
        'coroutine_stack_size 16', 'workers 8', 'resolvers 1', 'readahead 8192',
        'nodelay yes', 'log_general_stats_prom no', 'log_route_stats_prom no',
        'graceful_die_on_errors yes', 'enable_online_restart no',
        'bindwith_reuseport yes', 'keepalive 15', 'keepalive_keep_interval 75',
        'keepalive_probes 9', 'keepalive_usr_timeout 0', 'host "*"',
        'backlog 128', 'compression yes', 'tls "disable"',
        'heartbeat_request "select 1"', 'admin_database "console"',
        'enable_guc_sync yes', f'ports "{port}"', f'promhttp_server_port {port + 2}',
        'monitor_enabled yes', 'monitor_period 10', 'monitor_recovery_period 10',
        'monitor_retry_period_ms 1000', 'monitor_max_retries 3',
        'monitor_recovery_max_retries 3', 'monitor_timeout 5',
        'group "mmr_group" {', '    group_mode "mmr"',
        '    storage_db "postgres"', '    backend_clusters "pg_cluster_1,pg_cluster_2"',
        '    write_cluster "pg_cluster_2"', '    promoted_cluster "pg_cluster_1"',
        '    real_group_name "g1"', f'    group_uuid "{group_uuid}"',
        '    check "auto"', '}',
        'group "rep_group" {', '    group_mode "replication"',
        '    storage_db "postgres"', '    backend_clusters "pg_cluster_1"',
        '    check "auto"', '}',
        'group "balance_group" {', '    group_mode "balance"',
        '    storage_db "postgres"', '    access_mode "read_write"',
        '    backend_clusters "pg_cluster_1,pg_cluster_2"',
        '    check "auto"', '}',
        'group "single_group" {', '    group_mode "single"',
        '    storage_db "postgres"', '    access_mode "read_write"',
        '    backend_clusters "pg_cluster_1"', '    check "auto"', '}',
    ]
    for name, node in selected_nodes.items():
        cluster = "pg_cluster_1" if name in {"pg_1", "pg_3"} else "pg_cluster_2"
        app_name = "pg_240" if name == "pg_3" else ("pg_250" if name == "pg_4" else None)
        data += [f'datasources "{name}" {{', f'    host "{node["host"]}"',
                 f'    port {node["port"]}', f'    cluster_name "{cluster}"',
                 '    weight 10', '    status "active"',
                 *([f'    application_name "{app_name}"'] if app_name else []),
                 f'    system_identifier "{identifiers[name]}"', '    tls "disable"', '}',]
    # mode "none" cases (HA console/JDBC) exercise every route group; sql_parse
    # and other split modes scope the user to mmr_group only — fbasecman
    # rejects single/balance groups under a non-none rw_split_method.
    group_names = ("mmr_group,rep_group,balance_group,single_group"
                   if mode == "none" else "mmr_group")
    data += ['user "postgres" {',
             f'    group_names "{group_names}"',
             '    authentication "none"', '    storage_user "postgres"',
             '    pool "transaction"', '    pool_size 20', '    pool_discard no',
             '    pool_reserve_prepared_statement yes',
             f'    rw_split_method "{mode}"', '}',
             'user "admin" {', '    authentication "none"', '    pool "session"',
             '    role "admin"', '}']
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(data) + "\n"
    if transform is not None:
        content = transform(content)
    path.write_text(content, encoding="utf-8")
    return port


def _console_query(context: CaseContext, psql: str, port: int, sql: str):
    """console 管理面 psql：admin/console，合并 stderr（对齐 run_logged_command）。"""
    return context.command(
        [psql, "-h", context.environment.get("local_host", "127.0.0.1"), "-p", str(port), "-U", "admin",
         "-d", "console", "-c", sql],
        cwd=context.output_dir, timeout_seconds=15, merge_stderr=True)


def _business_query(context: CaseContext, psql: str, port: int, sql: str,
                    group: str = "mmr_group"):
    """业务面 psql：postgres/<group>，走代理业务路由。"""
    return context.command(
        [psql, "-h", context.environment.get("local_host", "127.0.0.1"), "-p", str(port), "-U", "postgres",
         "-d", group, "-c", sql],
        cwd=context.output_dir, timeout_seconds=15, merge_stderr=True)


def _expect(context: CaseContext, key: str, title: str, expected: str,
            predicate, query, retry_seconds: float = 0.0) -> str:
    """运行一条查询并按谓词断言，可选收敛重试窗口（对齐旧 runtime 语义）。"""
    deadline = time.monotonic() + retry_seconds
    started = time.monotonic()
    attempt = 0
    output = ""
    passed = False
    rc = -1
    while True:
        attempt += 1
        result = query()
        output = (result.stdout or "").rstrip() or "<empty>"
        rc = result.returncode
        passed = rc == 0 and predicate(output)
        if passed or time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    actual = "returncode=%s attempts=%s elapsed=%.2fs" % (
        rc, attempt, time.monotonic() - started)
    context.step(key, title, status="PASS" if passed else "FAIL",
                 details={"expected": expected, "actual": actual,
                          "output": output})
    if not passed:
        raise AssertionError("%s: %s" % (title, actual))
    return output


def _console_expect(context: CaseContext, psql: str, port: int, sql: str,
                    key: str, title: str, expected: str, predicate) -> str:
    # 旧 rt.psql 对 SHOW 命令给 30s 收敛窗口
    retry = 30.0 if sql.lstrip().upper().startswith("SHOW ") else 0.0
    return _expect(context, key, title, expected, predicate,
                   lambda: _console_query(context, psql, port, sql), retry)


def _wait_mmr_routing(context: CaseContext, port: int, psql: str,
                      timeout_seconds: float = 30.0) -> str:
    """等 group_checker 收敛：mmr_group 路由表出现 active 写目标行。

    ``context.start_process`` 的 ready 探针只等监听端口可连；fbasecman 的
    monitor/group_checker 异步探测 mmr_role 需要若干秒。收敛前的输出有
    两种形态：候选行标 UNKNOWN，或 pg_cluster_2 的行整体缺席、写路由
    落到 promoted_cluster 成员上（write-leader + promoted）——后者不含
    UNKNOWN 字样，故必须等"is_write_target=true 且 effective_state=active"
    的行出现才算收敛。历史用例经 console-ready 探针隐式覆盖了这段窗口，
    native 入口必须显式等待同一收敛状态再断言。
    """
    deadline = time.monotonic() + timeout_seconds
    query = [psql, "-X", "-A", "-t", "-h", context.environment.get("local_host", "127.0.0.1"), "-p", str(port),
             "-U", "admin", "-d", "console", "-c", "SHOW GROUP_ROUTING mmr_group;"]
    last = ""
    while time.monotonic() < deadline:
        result = context.command(query, timeout_seconds=10)
        last = result.stdout or ""
        if result.returncode != 0 or "pg_" not in last:
            time.sleep(0.5)
            continue
        if _routing_converged(last):
            return last
        time.sleep(0.5)
    raise Blocked("MMR 路由探测未在 %ss 内收敛" % timeout_seconds)


def _routing_converged(output: str) -> bool:
    # ``psql -A -t`` 输出无表头；列序为 SHOW GROUP_ROUTING 固定契约：
    # group_name|group_mode|user_name|cluster_name|current_primary|
    # candidate_node|candidate_type|effective_grouprole|effective_state|
    # is_write_target|write_source|fallback_reason|route_status|...
    # 收敛前有两种形态：grouprole 列仍为 UNKNOWN（monitor 探测未完成），
    # 或 pg_cluster_2 行整体缺席、写路由落到 promoted_cluster 成员
    # （effective_state=promoted）。两者都必须继续等。
    if "UNKNOWN" in output:
        return False
    for line in output.splitlines():
        cols = [col.strip() for col in line.split("|")]
        if len(cols) > 9 and cols[9] == "true" and cols[8] == "active":
            return True
    return False


def _read_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise RuntimeError("proxy closed during PostgreSQL protocol exchange")
        data.extend(chunk)
    return bytes(data)


def _message(sock):
    kind = _read_exact(sock, 1).decode("ascii")
    size = struct.unpack("!I", _read_exact(sock, 4))[0]
    if size < 4 or size > 16 * 1024 * 1024:
        raise RuntimeError(f"invalid PostgreSQL message size: {size}")
    return kind, _read_exact(sock, size - 4)


def _send(sock, kind, payload=b""):
    sock.sendall(kind.encode("ascii") + struct.pack("!I", len(payload) + 4) + payload)


def _error_fields(payload):
    return {part[:1].decode("ascii", "replace"): part[1:].decode("utf-8", "replace")
            for part in payload.split(b"\0") if len(part) > 1}


def _first_column(payload):
    count = struct.unpack("!H", payload[:2])[0]
    if not count:
        return None
    size = struct.unpack("!i", payload[2:6])[0]
    return payload[6:6 + size].decode("utf-8", "replace") if size >= 0 else None


def _protocol_connect(port):
    sock = socket.create_connection((os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1"), port), timeout=10)
    sock.settimeout(10)
    body = struct.pack("!I", 196608) + b"user\0postgres\0database\0mmr_group\0\0"
    sock.sendall(struct.pack("!I", len(body) + 4) + body)
    while True:
        kind, payload = _message(sock)
        if kind == "E":
            raise RuntimeError(f"startup failed: {_error_fields(payload)}")
        if kind == "R" and struct.unpack("!I", payload[:4])[0] != 0:
            raise RuntimeError("proxy requires unsupported authentication")
        if kind == "Z":
            if payload != b"I":
                raise RuntimeError("unexpected startup transaction status")
            return sock


def _execute(sock, variant, label, sql):
    empty = b"\0"
    packets = (("P", empty + sql.encode() + empty + struct.pack("!H", 0)),
               ("B", empty + empty + struct.pack("!HHH", 0, 0, 0)),
               ("D", b"P" + empty), ("E", empty + struct.pack("!I", 0)),
               ("S", b""))
    for kind, payload in packets:
        _send(sock, kind, payload)
    result = {"variant": variant, "step": label, "sql": sql, "sent": "P/B/D/E/S",
              "received": [], "sqlstate": None, "ready": None,
              "command_tag": None, "value": None}
    while True:
        kind, payload = _message(sock)
        result["received"].append(kind)
        if kind == "E":
            fields = _error_fields(payload)
            result["sqlstate"], result["error"] = fields.get("C"), fields.get("M")
        elif kind == "D":
            result["value"] = _first_column(payload)
        elif kind == "C":
            result["command_tag"] = payload.rstrip(b"\0").decode("utf-8", "replace")
        elif kind == "Z":
            result["ready"] = payload.decode("ascii")
            return result


def _run_protocol(port):
    records = []
    for variant, extra_failure in (("direct_recovery", False), ("after_local_25p02", True)):
        with _protocol_connect(port) as sock:
            steps = [("begin", "BEGIN"), ("savepoint", "SAVEPOINT s4"),
                     ("division", "SELECT 1/0")]
            if extra_failure:
                steps.append(("aborted_select", "SELECT 1"))
            steps.extend((("rollback_to", "ROLLBACK TO SAVEPOINT s4"),
                          ("recovery_select", "SELECT 9"), ("cleanup", "ROLLBACK")))
            for label, sql in steps:
                records.append(_execute(sock, variant, label, sql))
    return records


def _heartbeat_probe(port, mode):
    """Run the legacy heartbeat Parse/Bind/Execute message sequence."""
    with _protocol_connect(port) as sock:
        statement = b"hb\0SELECT 1\0" + struct.pack("!H", 0)
        _send(sock, "P", statement)
        _send(sock, "S")
        parse_messages = []
        while True:
            kind, _ = _message(sock)
            parse_messages.append(kind)
            if kind == "Z":
                break
        bind = b"\0hb\0" + struct.pack("!H", 0) + struct.pack("!H", 0)
        if mode == "malformed":
            pass
        elif mode == "binary":
            bind += struct.pack("!H", 1) + struct.pack("!H", 1)
        else:
            bind += struct.pack("!H", 0)
        _send(sock, "B", bind)
        _send(sock, "E", b"\0" + struct.pack("!I", 0))
        _send(sock, "S")
        messages = []
        while True:
            kind, _ = _message(sock)
            messages.append(kind)
            if kind == "Z":
                break
        return parse_messages, messages


class HeartbeatBindCase:
    """Native SQL_PARSE heartbeat Bind variants."""

    def __init__(self, mode):
        self.mode = mode

    def run(self, context: CaseContext) -> bool:
        config = context.output_dir / "fbasecman.conf"
        port = render_config(context, config, mode="sql_parse")
        text = config.read_text(encoding="utf-8")
        required = ('rw_split_method "sql_parse"',
                    'pool_reserve_prepared_statement yes', 'heartbeat_request "select 1"')
        if not all(item in text for item in required):
            raise AssertionError("SQL_PARSE heartbeat 配置不完整")
        context.start_process([context.environment["fbasecman_bin"], str(config)],
                              ready_host=context.environment.get("local_host", "127.0.0.1"), ready_port=port, timeout_seconds=30)
        _wait_mmr_routing(context, port,
                          context.environment.get("psql_bin", "/usr/bin/psql"))
        parse_messages, messages = _heartbeat_probe(port, self.mode)
        if self.mode == "malformed":
            passed = "E" in messages and "Z" in messages and "D" not in messages
        else:
            passed = all(item in messages for item in ("2", "D", "C", "Z"))
        context.attach_text("heartbeat-messages.json", json.dumps(
            {"mode": self.mode, "parse": parse_messages, "bind": messages}, indent=2))
        context.step("heartbeat-verdict", "核对 SQL_PARSE heartbeat Bind", status="PASS" if passed else "FAIL",
                     details={"mode": self.mode, "parse": parse_messages, "bind": messages})
        if not passed:
            raise AssertionError(f"heartbeat {self.mode} 响应不符合旧用例预期")
        psql = context.environment.get("psql_bin", "/usr/bin/psql")
        backend_check = context.command([psql, "-X", "-A", "-t", "-h", context.environment.get("local_host", "127.0.0.1"),
                                         "-p", str(port), "-U", "admin", "-d", "console",
                                         "-c", "SHOW SERVER_PREP_STMTS;"], timeout_seconds=15)
        rendered = backend_check.stdout
        backend_has_statement = "SELECT 1" in rendered
        expected_backend = self.mode == "binary"
        backend_ok = (backend_check.returncode == 0
                      and backend_has_statement == expected_backend)
        context.step("backend-prepared-check", "核对 heartbeat 后端 PreparedStatement 部署",
                     status="PASS" if backend_ok else "FAIL",
                     details={"contains_select_1": backend_has_statement,
                              "expected": expected_backend,
                              "returncode": backend_check.returncode,
                              "stderr": backend_check.stderr, "output": rendered})
        if not backend_ok:
            raise AssertionError("heartbeat 后端 PreparedStatement 部署状态与旧用例预期不符")
        return True


class SqlParseExtendedProtocolCase:
    """Native host for the legacy JDBC routing/recovery contract."""

    def run(self, context: CaseContext) -> bool:
        config = context.output_dir / "fbasecman.conf"
        port = render_config(context, config, mode="sql_parse")
        text = config.read_text(encoding="utf-8")
        required = ('rw_split_method "sql_parse"',
                    'pool_reserve_prepared_statement yes')
        if not all(item in text for item in required):
            raise AssertionError("sql_parse JDBC 配置不完整")
        context.start_process([context.environment["fbasecman_bin"], str(config)],
                              ready_host=context.environment.get("local_host", "127.0.0.1"), ready_port=port, timeout_seconds=30)
        _wait_mmr_routing(context, port,
                          context.environment.get("psql_bin", "/usr/bin/psql"))
        asset = Path(context.environment.get("sql_parse_java_asset", ""))
        jar = Path(context.environment.get("jdbc_jar", ""))
        if not asset.is_file() or not jar.is_file():
            raise Blocked("缺少 SQL_PARSE JDBC 测试资产或驱动")
        context.command(jdbc_client.javac_argv(jar, asset, dest_dir=context.output_dir),
                        cwd=context.output_dir, timeout_seconds=60)
        url = jdbc_client.build_url(
            context.environment.get("local_host", "127.0.0.1"), port, "mmr_group",
            {"prepareThreshold": 1, "preferQueryMode": "extended"})
        result = context.command(
            jdbc_client.java_argv(jdbc_client.classpath(context.output_dir, jar),
                                  "HaSqlParseExtended", url, "postgres", ""),
            cwd=context.output_dir, timeout_seconds=120)
        required_markers = ("ROLLBACK_RECOVERY=OK", "COMMIT_RECOVERY=OK", "PARAM_VALUE=42",
                            "READ_PORT=", "WRITE_PORT=")
        passed = result.returncode == 0 and all(marker in result.stdout for marker in required_markers)
        context.step("jdbc-verdict", "核对 SQL_PARSE JDBC 扩展协议路由和事务恢复",
                     status="PASS" if passed else "FAIL",
                     details={"output": result.stdout, "required": required_markers})
        if not passed:
            raise AssertionError("SQL_PARSE JDBC 扩展协议结果与旧用例预期不符")
        return True


class JdbcConsoleHaCommandsCase:
    """Native host for the complete JDBC HA console command matrix."""

    def run(self, context: CaseContext) -> bool:
        config = context.output_dir / "fbasecman.conf"
        port = render_config(context, config, mode="none")
        asset = Path(context.environment.get("ha_console_java_asset", ""))
        jar = Path(context.environment.get("jdbc_jar", ""))
        if not asset.is_file() or not jar.is_file():
            raise Blocked("缺少 JDBC HA 控制台资产或驱动")
        context.start_process([context.environment["fbasecman_bin"], str(config)],
                              ready_host=context.environment.get("local_host", "127.0.0.1"), ready_port=port, timeout_seconds=30)
        psql = context.environment.get("psql_bin", "/usr/bin/psql")
        _wait_mmr_routing(context, port, psql)
        context.command(jdbc_client.javac_argv(jar, asset, dest_dir=context.output_dir),
                        cwd=context.output_dir, timeout_seconds=60)
        nodes = context.environment.get("nodes") or {}
        ports = [str(nodes[name]["port"]) for name in ("mmr1", "mmr2") if name in nodes]
        extra = context.environment.get("extra_nodes") or {}
        ports += [str(extra[name]["port"]) for name in ("pg_3",) if name in extra]
        if len(ports) < 3:
            raise Blocked("HA JDBC 用例缺少 MMR 主节点或备节点")
        snapshots = context.output_dir / "jdbc-config-snapshots"
        urls = [jdbc_client.build_url(context.environment.get("local_host", "127.0.0.1"), port, db,
                                      {"preferQueryMode": "simple"})
                for db in ("console", "mmr_group", "single_group")]
        args = jdbc_client.java_argv(
            jdbc_client.classpath(context.output_dir, jar), "HaConsoleCommands",
            urls[0], "admin", "", str(config), str(snapshots), urls[1], urls[2], *ports)
        result = context.command(args, cwd=context.output_dir, timeout_seconds=180)
        markers = ("JDBC_CONNECT=OK", "SET_NODE_PARTED=OK", "SET_NODE_ACTIVE=OK",
                   "SET_NODE_WEIGHT=OK", "SET_NODE_WRITE=OK", "SET_NODE_PROMOTED=OK",
                   "SET_CLUSTER_PARTED=OK", "SET_CLUSTER_ACTIVE=OK", "REFRESH_CLUSTER=OK",
                   "ALL_HA_COMMANDS=OK")
        passed = result.returncode == 0 and all(marker in result.stdout for marker in markers)
        context.attach_text("ha-command-markers.txt", result.stdout)
        context.step("ha-command-verdict", "核对 JDBC 高可用命令矩阵和路由结果",
                     status="PASS" if passed else "FAIL",
                     details={"markers": markers, "output": result.stdout})
        if not passed:
            raise AssertionError("JDBC 高可用命令结果与旧用例预期不符")
        return True


class SetNodeWriteIdempotentCase:
    """Native platform host for the idempotent SET NODE WRITE case."""

    def run(self, context: CaseContext) -> bool:
        config = context.output_dir / "fbasecman.conf"
        port = render_config(context, config, mode="none")
        before = config.read_bytes()
        context.start_process([context.environment["fbasecman_bin"], str(config)],
                              ready_host=context.environment.get("local_host", "127.0.0.1"), ready_port=port, timeout_seconds=30)
        psql = context.environment.get("psql_bin", "/usr/bin/psql")
        _wait_mmr_routing(context, port, psql)
        def query(sql):
            return context.command([psql, "-X", "-A", "-t", "-h", context.environment.get("local_host", "127.0.0.1"),
                                    "-p", str(port), "-U", "admin", "-d", "console",
                                    "-c", sql], timeout_seconds=30)
        initial = query("SHOW GROUP_ROUTING mmr_group;")
        command = query("SET NODE WRITE pg_2 IN GROUP mmr_group;")
        after = query("SHOW GROUP_ROUTING mmr_group;")
        unchanged = config.read_bytes() == before
        output = "\n".join((initial.stdout, command.stdout, after.stdout))
        passed = (initial.returncode == 0 and command.returncode == 0 and after.returncode == 0
                  and all(item in initial.stdout for item in ("pg_cluster_2", "pg_2", "write-leader"))
                  and ("SET NODE" in command.stdout or "NO CONFIG CHANGE" in command.stdout)
                  and "ERROR" not in command.stdout and unchanged
                  and all(item in after.stdout for item in ("mmr_group", "active", "pg_cluster_2", "pg_2", "write-leader")))
        context.attach_text("set-node-write-output.txt", output)
        context.step("idempotent-verdict", "核对 SET NODE WRITE 幂等命令",
                     status="PASS" if passed else "FAIL",
                     details={"config_unchanged": unchanged, "output": output})
        if not passed:
            raise AssertionError("SET NODE WRITE 幂等用例与旧判定不一致")
        return True


class IdempotentHaCommandCase:
    """Native implementation for idempotent NODE/CLUSTER commands."""

    def __init__(self, command, initial_needles, final_needles, title,
                 initial_query="SHOW GROUP_ROUTING mmr_group;",
                 final_query="SHOW GROUP_ROUTING mmr_group;"):
        self.command, self.initial_needles = command, initial_needles
        self.final_needles, self.title = final_needles, title
        self.initial_query, self.final_query = initial_query, final_query

    def run(self, context: CaseContext) -> bool:
        config = context.output_dir / "fbasecman.conf"
        port = render_config(context, config, mode="none")
        before = config.read_bytes()
        context.start_process([context.environment["fbasecman_bin"], str(config)],
                              ready_host=context.environment.get("local_host", "127.0.0.1"), ready_port=port, timeout_seconds=30)
        psql = context.environment.get("psql_bin", "/usr/bin/psql")
        _wait_mmr_routing(context, port, psql)
        def q(sql):
            return context.command([psql, "-X", "-A", "-t", "-h", context.environment.get("local_host", "127.0.0.1"), "-p", str(port),
                                    "-U", "admin", "-d", "console", "-c", sql], timeout_seconds=30)
        initial = q(self.initial_query)
        command = q(self.command)
        final = q(self.final_query)
        unchanged = config.read_bytes() == before
        passed = (initial.returncode == command.returncode == final.returncode == 0
                  and all(x in initial.stdout for x in self.initial_needles)
                  and ("SET NODE" in command.stdout or "SET CLUSTER" in command.stdout
                       or "NO CONFIG CHANGE" in command.stdout)
                  and "ERROR" not in command.stdout and unchanged
                  and all(x in final.stdout for x in self.final_needles))
        output = "\n".join((initial.stdout, command.stdout, final.stdout))
        context.attach_text("idempotent-ha-output.txt", output)
        context.step("idempotent-verdict", self.title, status="PASS" if passed else "FAIL",
                     details={"config_unchanged": unchanged, "output": output})
        if not passed:
            raise AssertionError(self.title + " 与旧用例预期不符")
        return True


class SetNodeWeightIdempotentCase:
    """Native host for SET NODE WEIGHT pg_3=10 idempotency."""

    def run(self, context: CaseContext) -> bool:
        config = context.output_dir / "fbasecman.conf"
        port = render_config(context, config, mode="none")
        before = config.read_bytes()
        context.start_process([context.environment["fbasecman_bin"], str(config)],
                              ready_host=context.environment.get("local_host", "127.0.0.1"), ready_port=port, timeout_seconds=30)
        psql = context.environment.get("psql_bin", "/usr/bin/psql")
        _wait_mmr_routing(context, port, psql)
        def q(sql):
            return context.command([psql, "-X", "-A", "-t", "-h", context.environment.get("local_host", "127.0.0.1"), "-p", str(port),
                                    "-U", "admin", "-d", "console", "-c", sql], timeout_seconds=30)
        initial, command, final = q("SHOW NODES;"), q("SET NODE WEIGHT pg_3=10;"), q("SHOW NODES;")
        unchanged = config.read_bytes() == before
        def has_weight(result):
            return any("pg_3" in line and "10" in line for line in result.stdout.splitlines())
        passed = (initial.returncode == command.returncode == final.returncode == 0
                  and has_weight(initial) and has_weight(final) and unchanged
                  and ("SET NODE" in command.stdout or "NO CONFIG CHANGE" in command.stdout)
                  and "ERROR" not in command.stdout)
        output = "\n".join((initial.stdout, command.stdout, final.stdout))
        context.attach_text("set-node-weight-output.txt", output)
        context.step("weight-verdict", "核对 SET NODE WEIGHT 幂等命令",
                     status="PASS" if passed else "FAIL",
                     details={"config_unchanged": unchanged, "output": output})
        if not passed:
            raise AssertionError("SET NODE WEIGHT 幂等用例与旧判定不一致")
        return True


class SavepointRecoveryCase:
    """Native Extended Query savepoint recovery verification."""

    def run(self, context: CaseContext) -> bool:
        config = context.output_dir / "fbasecman.conf"
        port = render_config(context, config)
        binary = context.environment["fbasecman_bin"]
        context.start_process([binary, str(config)], ready_host=context.environment.get("local_host", "127.0.0.1"), ready_port=port,
                              timeout_seconds=30)
        _wait_mmr_routing(context, port,
                          context.environment.get("psql_bin", "/usr/bin/psql"))
        config_text = config.read_text(encoding="utf-8")
        if ('rw_split_method "sql_parse"' not in config_text or
                'pool_reserve_prepared_statement yes' not in config_text):
            raise AssertionError("SQL_PARSE 配置或 prepared statement 保留配置缺失")
        records = _run_protocol(port)
        expected = [("direct_recovery", x) for x in
                    ("begin", "savepoint", "division", "rollback_to", "recovery_select", "cleanup")]
        expected += [("after_local_25p02", x) for x in
                     ("begin", "savepoint", "division", "aborted_select", "rollback_to", "recovery_select", "cleanup")]
        if [(x.get("variant"), x.get("step")) for x in records] != expected:
            raise AssertionError("Extended Query savepoint protocol sequence mismatch")
        expected_steps = {
            "begin": (None, "T", "BEGIN", None),
            "savepoint": (None, "T", "SAVEPOINT", None),
            "division": ("22012", "E", None, None),
            "aborted_select": ("25P02", "E", None, None),
            "rollback_to": (None, "T", "ROLLBACK", None),
            "recovery_select": (None, "T", "SELECT 1", "9"),
            "cleanup": (None, "I", "ROLLBACK", None),
        }
        failures = []
        for item in records:
            expected = expected_steps[item["step"]]
            actual = (item["sqlstate"], item["ready"], item["command_tag"], item["value"])
            if actual != expected:
                failures.append({"step": item["step"], "expected": expected,
                                 "actual": actual, "error": item.get("error")})
        if failures:
            context.attach_text("protocol-failures.json", json.dumps(failures, indent=2))
            raise AssertionError("Extended Query response mismatch: " + json.dumps(failures))
        context.attach_text("protocol-records.json", json.dumps(records, ensure_ascii=False, indent=2))
        for index, item in enumerate(records, 1):
            context.step(f"protocol-{index}", f"{item['variant']} / {item['step']}",
                         details={"sqlstate": item["sqlstate"], "ready": item["ready"],
                                  "command_tag": item["command_tag"], "value": item["value"]})
        product_log = config.with_suffix(".log")
        if product_log.is_file():
            log_lines = product_log.read_text(encoding="utf-8", errors="replace").splitlines()
            context.attach_file("fbasecman.log", product_log)
            error_record = next(item for item in records
                                if item["variant"] == "after_local_25p02"
                                and item["step"] == "aborted_select")
            client_match = re.search(r"fbasecman: ([0-9a-f]+):", error_record.get("error", ""))
            client_id = client_match.group(1) if client_match else ""
            relevant = [line for line in log_lines if client_id and client_id in line
                        and any(word in line.lower() for word in
                                ("rollback", "25p02", "savepoint", "local error", "detach"))]
            internal_rollback = any("after internal rollback" in line for line in relevant)
            context.step("transaction-log-check", "检查同一 client 的保存点恢复日志",
                         status="FAIL" if internal_rollback else "PASS",
                         details={"client_id": client_id or None,
                                  "lines": relevant[-30:],
                                  "expected": "不得出现 after internal rollback"})
            if internal_rollback:
                raise AssertionError("代理在保存点恢复期间执行了内部完整 ROLLBACK")
        return True


class ReloadDisableMonitorRouteLossCase:
    """tmp.reload_disable_monitor_route_loss 的原生宿主。

    缺陷复现用例：reload 关闭监控（monitor_enabled no + check "none"）后
    业务路由应继承 reload 前的有效投影。当前产品行为存在缺陷，
    步骤 5 的业务查询预期失败——该 FAIL 判定与历史逐字同因。
    """

    def run(self, context: CaseContext) -> bool:
        config = context.output_dir / "fbasecman.conf"
        port = render_config(context, config, mode="none")
        psql = context.environment.get("psql_bin", "/usr/bin/psql")
        context.start_process([context.environment["fbasecman_bin"], str(config)],
                              ready_host=context.environment.get("local_host", "127.0.0.1"), ready_port=port,
                              timeout_seconds=30)
        _wait_mmr_routing(context, port, psql)

        _console_expect(
            context, psql, port, "SHOW CLUSTERS;",
            "step-1-1", "步骤 1.1：检查控制台状态确认拓扑正常",
            "两个 cluster 的 topology_state 均为 VALID，monitor_enabled 为 true",
            lambda output: "VALID" in output and (
                "true" in output.lower() or "| t" in output.lower()
                or "t |" in output.lower()))

        _expect(
            context, "step-1-2", "步骤 1.2：验证业务连接正常",
            "正常返回 1，路由通畅", lambda output: "1" in output,
            lambda: _business_query(context, psql, port, "SELECT 1;"))

        conf_text = config.read_text(encoding="utf-8")
        conf_text = re.sub(r'monitor_enabled\s+yes', 'monitor_enabled no', conf_text)
        conf_text = re.sub(r'check\s+"auto"', 'check "none"', conf_text)
        config.write_text(conf_text, encoding="utf-8")
        context.step(
            "step-2", "步骤 2：修改配置文件 fbasecman.conf",
            details={"expected": '修改 monitor_enabled no 与 check "none"',
                     "actual": "配置文件已更新为关闭监控并设置 check none"})

        _console_expect(
            context, psql, port, "RELOAD;",
            "step-3", "步骤 3：向控制台发送 reload 命令热加载配置",
            "返回 RELOAD",
            lambda output: "RELOAD" in output and "ERROR" not in output)

        _console_expect(
            context, psql, port, "SHOW CLUSTERS;",
            "step-4-1", "步骤 4.1：查看集群信息确认 monitor_enabled 变更",
            "monitor_enabled 变为 false，topology_state 依然保持 VALID",
            lambda output: (
                "false" in output.lower() or "| f" in output.lower()
                or "f |" in output.lower()) and "VALID" in output)

        _console_expect(
            context, psql, port, "SHOW ENDPOINT_MONITOR;",
            "step-4-2", "步骤 4.2：查看端点状态",
            "端点的 probe_state 变为 PENDING，topology_state 变为 UNINITIALIZED",
            lambda output: "PENDING" in output or "UNINITIALIZED" in output)

        # 缺陷复现点：热重载关闭监控后业务路由丢失
        # （route for '...' is not found），历史判定为 FAIL。
        _expect(
            context, "step-5",
            "步骤 5：发起业务查询（预期继承 reload 前有效业务路由，正常返回 1）",
            "正常返回 1，业务路由维持通畅",
            lambda output: "1" in output and "ERROR" not in output,
            lambda: _business_query(context, psql, port, "SELECT 1;"))
        return True


# ---------------------------------------------------------------------------
# outstanding 队列与后端 PS 缓存一致性（模式参数化，11 条用例共用）
# ---------------------------------------------------------------------------

# 每模式 (AFTER_ERROR, AFTER_RECOVERY) 两阶段的期望缓存条目数
_OUTSTANDING_PHASE_COUNTS = {
    "parse_failure_single": (0, 1),
    "parse_failure_shared_sync": (1, 3),
    "execute_failure_shared_sync": (2, 3),
    "lru_close_success": (1, 1),
    "lru_close_skipped_restore": (1, 1),
    "lru_close_multiple_restore": (1, 1),
    "lru_confirmed_multiple_restore_order": (2, 2),
    "long_statement_name_cleanup": (0, 1),
    "fragmented_close_packet": (1, 2),
    "fragmented_execute_packet": (1, 1),
    "execute_payload_validation": (1, 1),
}


def _outstanding_backend_limit(mode):
    if mode == "lru_confirmed_multiple_restore_order":
        return 2
    if mode.startswith("lru_"):
        return 1
    return 8


def _parse_pg_cache(output):
    """Parse the latest PG_CACHE block from probe output."""
    if not output:
        return {}
    sections = output.split("PG_CACHE_BEGIN")
    if len(sections) < 2:
        return {}
    latest_section = sections[-1].split("PG_CACHE_END")[0]
    result = {}
    for line in latest_section.splitlines():
        line = line.strip()
        if not line or not line.startswith("PG_CACHE|"):
            continue
        parts = line.split("|", 2)
        if len(parts) >= 3:
            result[parts[1].strip()] = parts[2].strip().lower()
    return result


def _post_disconnect_ref_state(server_output, global_cache_output, marker):
    from platform_regress.clients.psql import parse_psql_table
    server_counts = {}
    for row in parse_psql_table(server_output):
        definition = row.get("definition", "")
        if marker in definition:
            gname = row.get("global_name", "")
            if gname:
                server_counts[gname] = server_counts.get(gname, 0) + 1
    global_refs = {}
    for row in parse_psql_table(global_cache_output):
        desc = row.get("description", "") or row.get("definition", "")
        if marker in desc:
            gname = row.get("global_name", "")
            if gname:
                try:
                    global_refs[gname] = int(row.get("ref_count", "0"))
                except ValueError:
                    global_refs[gname] = 0
    consistent = (
        all(global_refs.get(k, 0) == v for k, v in server_counts.items())
        and all(v == server_counts.get(k, 0) for k, v in global_refs.items()))
    return consistent, server_counts, global_refs


class OutstandingConsistencyCase:
    """outstanding.<name> 的原生宿主：协议探针驱动的两阶段缓存一致性核对。"""

    def __init__(self, mode):
        self.mode = mode

    def run(self, context: CaseContext) -> bool:
        from platform_regress.execution.phased_process import (
            PhaseAction, PhasedProcess, observe_phases)

        mode = self.mode
        backend_limit = _outstanding_backend_limit(mode)
        config = context.output_dir / "fbasecman.conf"
        port = render_config(context, config, mode="none")
        text = config.read_text(encoding="utf-8")
        text = text.replace(
            'pool_size 20',
            'pool_size 1\n    pool_reserve_prepared_statement yes\n    pool_discard no')
        text = text.replace(
            'log_min_messages "info"',
            'log_min_messages "info"\n'
            'backend_prepared_statements_limit %d\n'
            'global_prepared_statements_limit 10000' % backend_limit)
        config.write_text(text, encoding="utf-8")
        context.start_process([context.environment["fbasecman_bin"], str(config)],
                              ready_host=context.environment.get("local_host", "127.0.0.1"), ready_port=port,
                              timeout_seconds=30)
        psql = context.environment.get("psql_bin", "/usr/bin/psql")
        _wait_mmr_routing(context, port, psql)

        context.step("step-config", "确认 outstanding 与 PS 缓存一致性配置",
                     details={"expected": "transaction pool，pool_size=1，保留 PreparedStatement，禁用 DISCARD ALL",
                              "actual": "\n".join((
                                  "backend_prepared_statements_limit %d" % backend_limit,
                                  "pool_reserve_prepared_statement yes",
                                  "pool_size 1",
                                  'rw_split_method "none"',
                                  "pool_discard no"))})

        probe = (Path(__file__).resolve().parent / "regression"
                 / "suites" / "outstanding" / "assets" / "outstanding_protocol_probe.py")
        context.step("step-probe", "确认缓存观测不会创建 PreparedStatement",
                     details={"expected": "测试流量使用原始 Extended 报文；pg_prepared_statements 观测使用 Simple Query Q",
                              "actual": "driver=raw PostgreSQL protocol; cache inspection=Simple Query Q"})

        logfile = context.output_dir / "protocol_probe.log"
        proc = PhasedProcess([sys.executable, str(probe), str(port), mode, "single_group"],
                             logfile, cwd=context.output_dir)
        phase_data = {}
        expected_counts = _OUTSTANDING_PHASE_COUNTS[mode]

        def observe(name, marker):
            pg_cache = _parse_pg_cache(proc.output)
            phase_data[name] = pg_cache
            expected_cnt = expected_counts[0] if name == "AFTER_ERROR" else expected_counts[1]
            actual_cnt = len(pg_cache)
            if actual_cnt != expected_cnt:
                raise RuntimeError(
                    "phase %s cache count mismatch: expected %s, got %s (cached: %s)"
                    % (name, expected_cnt, actual_cnt, pg_cache))
            return marker

        _observations, rc, output = observe_phases(
            proc,
            [PhaseAction("AFTER_ERROR", "PHASE_READY=AFTER_ERROR", "continue"),
             PhaseAction("AFTER_RECOVERY", "PHASE_READY=AFTER_RECOVERY", "continue")],
            observe, timeout=30, finish_timeout=30)
        if logfile.is_file():
            context.attach_file("protocol_probe.log", logfile)
        if rc != 0:
            raise RuntimeError("protocol probe failed with rc=%s:\n%s" % (rc, output[-1000:]))

        for index, name in enumerate(("AFTER_ERROR", "AFTER_RECOVERY")):
            context.step("phase-%d" % (index + 1),
                         "验证第%s阶段 (%s) 缓存%s状态" % (
                             "一" if index == 0 else "二", name,
                             "" if index == 0 else "恢复"),
                         details={"expected": "缓存条目数等于 %d" % expected_counts[index],
                                  "actual": "actual_count=%d; cache=%s" % (
                                      len(phase_data.get(name, {})),
                                      phase_data.get(name, {}))})

        marker = "outstanding_case_%s" % mode
        servers_out = _console_expect(
            context, psql, port, "SHOW SERVER_PREP_STMTS;",
            "show-server-prep", "查询后端服务器上的 Prepared Statements",
            "SHOW SERVER_PREP_STMTS 正常返回结果，无错误",
            lambda out: "ERROR" not in out and bool(out.strip()))
        global_out = _console_expect(
            context, psql, port, "SHOW GLOBAL_PREPARED_STATEMENTS;",
            "show-global-prep", "查询全局缓存中的 Prepared Statements",
            "SHOW GLOBAL_PREPARED_STATEMENTS 正常返回结果，无错误",
            lambda out: "ERROR" not in out and bool(out.strip()))
        consistent, s_counts, g_refs = _post_disconnect_ref_state(
            servers_out, global_out, marker)
        context.step("post-disconnect-consistency", "验证连接断开后引用计数与 Server 一致",
                     status="PASS" if consistent else "FAIL",
                     details={"expected": "Server 持有条目数与 Global Cache 引用计数保持一致",
                              "actual": "server_counts=%s; global_refs=%s; consistent=%s"
                                        % (s_counts, g_refs, consistent)})
        if not consistent:
            raise AssertionError(
                "post-disconnect ref state inconsistent: server=%s, global=%s"
                % (s_counts, g_refs))
        if "OUTSTANDING_TEST=OK" not in output:
            raise AssertionError("probe output did not report OUTSTANDING_TEST=OK")
        return True



# ---------------------------------------------------------------------------
# rw_toggle 读写路由（topology/route_mode/driver/scenario 参数化，14 条共用）
# ---------------------------------------------------------------------------

_RW_READ_SQL = (
    "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY; "
    "SELECT inet_server_addr(), inet_server_port(), pg_is_in_recovery();")
_RW_WRITE_SQL = (
    "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; BEGIN; "
    "CREATE TEMP TABLE rw_toggle_probe(id integer); "
    "INSERT INTO rw_toggle_probe VALUES (1); "
    "SELECT inet_server_addr(), inet_server_port(), pg_is_in_recovery(); ROLLBACK;")


def _rw_group_rows(output, group):
    """Parse the psql table returned by SHOW GROUP_ROUTING."""
    lines = [line.strip() for line in output.splitlines() if "|" in line]
    header_index = next(
        (index for index, line in enumerate(lines)
         if line.startswith("group_name") and "group_mode" in line), None)
    if header_index is None:
        return []
    headers = [item.strip() for item in lines[header_index].split("|")]
    rows = []
    for line in lines[header_index + 1:]:
        if set(line.replace("|", "").replace("-", "").strip()) == set():
            continue
        values = [item.strip() for item in line.split("|")]
        if len(values) != len(headers) or values[0] != group:
            continue
        rows.append(dict(zip(headers, values)))
    return rows


def _free_port(exclude=()):
    while True:
        sock = socket.socket()
        sock.bind((os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1"), 0))
        port = sock.getsockname()[1]
        sock.close()
        if port not in exclude:
            return port


class RwToggleCase:
    """rw_toggle.<name> 的原生宿主：读写分离路由断言与产品日志核对。"""

    def __init__(self, topology, route_mode, driver, scenario):
        self.topology = topology
        self.route_mode = route_mode
        self.driver = driver
        self.scenario = scenario

    @property
    def group(self):
        return "mmr_group" if self.topology == "mmr" else "rep_group"

    @property
    def title(self):
        return "%s %s/%s" % (self.topology.upper(), self.route_mode.upper(),
                             self.driver.upper())

    def _backend_ports(self, context):
        nodes = context.environment.get("nodes") or {}
        extra = context.environment.get("extra_nodes") or {}
        def port_of(name, source):
            node = source.get(name)
            return str(node["port"]) if node else None
        mmr1 = port_of("mmr1", nodes)
        mmr2 = port_of("mmr2", nodes)
        pg_3 = port_of("pg_3", extra)
        pg_4 = port_of("pg_4", extra)
        if self.topology == "mmr":
            return {"write": mmr2,
                    "read": tuple(p for p in (mmr1, pg_3, mmr2, pg_4) if p)}
        return {"write": mmr1, "read": tuple(p for p in (mmr1, pg_3) if p)}

    def _check(self, context, key, title, expected, actual, passed):
        context.step(key, title, status="PASS" if passed else "FAIL",
                     details={"expected": expected, "actual": actual})
        if not passed:
            raise AssertionError("%s: expected %s, actual %s" % (title, expected, actual))

    def run(self, context: CaseContext) -> bool:
        group = self.group
        config = context.output_dir / "fbasecman.conf"
        port = render_config(context, config, mode="none")
        read_port = _free_port({port, port + 2}) if self.route_mode == "port" else None
        text = config.read_text(encoding="utf-8")
        text = text.replace(
            'group_names "mmr_group,rep_group,balance_group,single_group"',
            'group_names "%s"' % group, 1)
        text = text.replace('    rw_split_method "none"',
                            '    rw_split_method "%s"' % self.route_mode, 1)
        if self.route_mode == "port":
            text = text.replace('ports "%s"' % port,
                                'ports "%s,%s"' % (port, read_port), 1)
            marker = 'group "%s" {\n' % group
            text = text.replace(marker, marker + '    write_port %s\n' % port, 1)
        config.write_text(text, encoding="utf-8")
        context.start_process([context.environment["fbasecman_bin"], str(config)],
                              ready_host=context.environment.get("local_host", "127.0.0.1"), ready_port=port,
                              timeout_seconds=30)
        psql = context.environment.get("psql_bin", "/usr/bin/psql")
        if self.topology == "mmr":
            _wait_mmr_routing(context, port, psql)

        expected_mode = "mmr" if self.topology == "mmr" else "replication"
        expected_role = "write-leader" if self.topology == "mmr" else "primary"
        output = _console_expect(
            context, psql, port, "SHOW GROUP_ROUTING %s;" % group,
            "route-loaded", "%s：检查路由配置已加载" % self.title,
            "%s group_mode=%s，且存在 active %s 候选" % (group, expected_mode, expected_role),
            lambda out: (group in out and expected_mode in out
                         and "active" in out and expected_role in out))
        rows = _rw_group_rows(output, group)
        fields = ("group_name", "group_mode", "cluster_name", "candidate_node",
                  "effective_grouprole", "effective_state", "is_write_target",
                  "route_status")
        actual_rows = ["; ".join("%s=%s" % (field, row.get(field, "<missing>"))
                                 for field in fields) for row in rows]
        all_common = bool(rows) and all(
            row.get("group_mode") == expected_mode
            and row.get("effective_state") == "active"
            and row.get("route_status") == "AVAILABLE" for row in rows)
        role_rows = [row for row in rows
                     if row.get("effective_grouprole") == expected_role
                     and row.get("is_write_target") == "true"]
        self._check(
            context, "route-fields",
            "%s：逐字段校验 SHOW GROUP_ROUTING" % self.title,
            "每行 group_name=%s、group_mode=%s、effective_state=active、"
            "route_status=AVAILABLE；至少一行 effective_grouprole=%s 且 "
            "is_write_target=true" % (group, expected_mode, expected_role),
            "返回行数=%d\n%s" % (len(rows), "\n".join(actual_rows) or "<no parsed rows>"),
            all_common and bool(role_rows))

        if self.driver == "jdbc":
            self._run_jdbc(context, psql, port, read_port)
        else:
            self._run_psql(context, psql, port, read_port)
        self._check_product_log(context, config)
        return True

    def _run_psql(self, context, psql, port, read_port):
        group = self.group
        backend = self._backend_ports(context)

        def run_read():
            target_port = read_port if self.route_mode == "port" else port
            out = _expect(
                context, "read-query", "%s：执行只读事务" % self.title,
                "只读事务返回后端地址、端口和 recovery 状态",
                lambda text: bool(re.search(r"\|\s*\d{4,5}\s*\|", text)),
                lambda: _business_query(context, psql, target_port,
                                        _RW_READ_SQL, group))
            observed = re.findall(r"\|\s*(\d{4,5})\s*\|", out)
            self._check(context, "read-backend",
                        "%s：只读请求命中合法读候选" % self.title,
                        "后端端口属于 %s" % ",".join(backend["read"]),
                        "观测后端端口=%s\n%s" % (observed or ["<none>"], out.strip()),
                        any(p in observed for p in backend["read"]))

        def run_write():
            out = _expect(
                context, "write-query", "%s：执行写事务" % self.title,
                "写事务创建临时表并返回后端地址、端口",
                lambda text: bool(re.search(r"\|\s*\d{4,5}\s*\|", text)),
                lambda: _business_query(context, psql, port,
                                        _RW_WRITE_SQL, group))
            observed = re.findall(r"\|\s*(\d{4,5})\s*\|", out)
            self._check(context, "write-backend",
                        "%s：写请求命中 write-leader" % self.title,
                        "后端端口=%s" % backend["write"],
                        "观测后端端口=%s\n%s" % (observed or ["<none>"], out.strip()),
                        backend["write"] in observed)

        if self.scenario == "read":
            run_read()
        elif self.scenario == "write":
            run_write()
        elif self.scenario == "switch":
            run_write(); run_read(); run_write()
        else:
            raise AssertionError("unsupported psql scenario: %s" % self.scenario)

    def _run_jdbc(self, context, psql, port, read_port):
        group = self.group
        asset = (Path(__file__).resolve().parent / "regression"
                 / "suites" / "rw_toggle" / "assets" / "RwToggleJdbc.java")
        jar = Path(context.environment.get("jdbc_jar", ""))
        if not asset.is_file() or not jar.is_file():
            raise Blocked("缺少 rw_toggle JDBC 资产或驱动")
        driver_dir = context.output_dir / "driver"
        driver_dir.mkdir(exist_ok=True)
        import shutil
        target = driver_dir / asset.name
        shutil.copyfile(str(asset), str(target))
        context.command(jdbc_client.javac_argv(jar, target, dest_dir=driver_dir),
                        cwd=driver_dir, timeout_seconds=60)
        url = jdbc_client.build_url(context.environment.get("local_host", "127.0.0.1"), port, group,
                                    {"preferQueryMode": "simple"})
        if self.route_mode == "port":
            read_url = jdbc_client.build_url(context.environment.get("local_host", "127.0.0.1"), read_port, group,
                                             {"preferQueryMode": "simple"})
            args = jdbc_client.java_argv(
                jdbc_client.classpath(driver_dir, jar),
                "RwToggleJdbc", read_url, "postgres", "", "port", url)
        else:
            args = jdbc_client.java_argv(
                jdbc_client.classpath(driver_dir, jar),
                "RwToggleJdbc", url, "postgres", "", self.route_mode)
        result = context.command(args, cwd=driver_dir, timeout_seconds=120)
        output = (result.stdout or "").strip()
        context.step("jdbc-run", "执行 rw_toggle JDBC 读写时序",
                     status="PASS" if result.returncode == 0 else "FAIL",
                     details={"expected": "JDBC 程序返回读写后端端口并退出成功",
                              "actual": output or "<empty>"})
        if result.returncode != 0:
            raise AssertionError("JDBC driver failed rc=%s" % result.returncode)
        backend = self._backend_ports(context)
        read_ok = any("READ_PORT=%s" % p in output for p in backend["read"])
        write_ok = "WRITE_PORT=%s" % backend["write"] in output
        reuse_ok = "READ_AGAIN=" in output and "WRITE_AGAIN=" in output
        heartbeat_ok = "HEARTBEAT=10086" in output
        self._check(context, "jdbc-verdict", "验证 JDBC 读写后端路由和连接复用",
                    "READ/WRITE 命中目标，读写事务各自再次执行成功，并完成 heartbeat",
                    output, read_ok and write_ok and reuse_ok and heartbeat_ok)

    def _check_product_log(self, context, config):
        log = config.with_suffix(".log")
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        group = self.group
        allowed = ("pg_1", "pg_2", "pg_3", "pg_4") \
            if self.topology == "mmr" else ("pg_1", "pg_3")
        route_lines = [line.strip() for line in text.splitlines()
                       if "route(" in line and (".%s.postgres)" % group) in line]
        route_ok = any("route(%s.%s.postgres)" % (node, group) in line
                       for node in allowed for line in route_lines)
        level_lines = [line.strip() for line in text.splitlines()
                       if re.search(r"\b(error|fatal|panic|crash)\b", line, re.IGNORECASE)]
        if log.is_file():
            context.attach_file("fbasecman.log", log)
        self._check(context, "product-log", "检查 fbasecman.log 的实际路由和负向日志",
                    "存在目标 group 的实际 route(...) 日志，且无 error/fatal/panic/crash",
                    "路由日志行:\n%s\n负向日志行:\n%s" % (
                        "\n".join(route_lines[-8:]) if route_lines else "<none>",
                        "\n".join(level_lines[-8:]) if level_lines else "<none>"),
                    bool(route_lines) and route_ok and not level_lines)


# ---------------------------------------------------------------------------
# guc 套件：GUC 部署/重放与前后端缓存一致性（SQL_PARSE/HINT 模式参数化）
# ---------------------------------------------------------------------------

_GUC_PROBE_SCHEMAS = ("public", "postgres", "schema1", "schema2")


def _guc_pred(*all_of, **kwargs):
    """把 legacy executor 的子串断言编译为谓词。

    ``any_of`` 组内命中任一即可；``none_of`` 全部不得出现。
    """
    any_of = tuple(kwargs.get("any_of") or ())
    none_of = tuple(kwargs.get("none_of") or ())

    def predicate(output):
        if any(needle not in output for needle in all_of):
            return False
        if any_of and not any(needle in output for needle in any_of):
            return False
        return all(needle not in output for needle in none_of)

    return predicate


def _guc_primaries(context: CaseContext):
    nodes = context.environment.get("nodes") or {}
    primaries = [name for name in ("mmr1", "mmr2") if name in nodes]
    if len(primaries) != 2:
        raise Blocked("pgcluster 拓扑缺少两个 MMR 主节点")
    return primaries


def _guc_prepare_tables(context: CaseContext):
    """在双主库创建同名探针表（对齐 legacy prepare_search_path_tables）。

    返回各节点本次新建的 schema 清单，清理时只删新建 schema。
    """
    created = {}
    for node in _guc_primaries(context):
        rows = context.sql(
            node, "SELECT nspname FROM pg_namespace "
                  "WHERE nspname IN ('postgres','schema1','schema2')").rows
        existing = {str(row[0]).strip() for row in rows}
        created[node] = [name for name in ("postgres", "schema1", "schema2")
                         if name not in existing]
        for schema in _GUC_PROBE_SCHEMAS:
            if schema != "public":
                context.sql(node, "CREATE SCHEMA IF NOT EXISTS %s" % schema)
            context.sql(node, "CREATE TABLE IF NOT EXISTS %s.guc_search_path_probe "
                              "(marker text PRIMARY KEY)" % schema)
            context.sql(node, "INSERT INTO %s.guc_search_path_probe VALUES ('%s') "
                              "ON CONFLICT (marker) DO NOTHING" % (schema, schema))
    return created


def _guc_cleanup_tables(context: CaseContext, created):
    for node in _guc_primaries(context):
        for schema in _GUC_PROBE_SCHEMAS:
            context.sql(node, "DROP TABLE IF EXISTS %s.guc_search_path_probe" % schema)
        for schema in created.get(node, ()):
            context.sql(node, "DROP SCHEMA IF EXISTS %s" % schema)


def _guc_verify_search_path(context: CaseContext, psql: str, port: int,
                            path_sql: str, expected_schema):
    """经代理以指定 search_path 解析未限定同名表（对齐 legacy
    verify_search_path_table：expected_schema=None 断言报 does not exist）。"""
    sql = ("SET search_path = %s; SELECT marker || '|' || current_schemas(true)::text "
           "FROM guc_search_path_probe;" % path_sql)
    if expected_schema is None:
        _expect(
            context, "guc-verify-empty-schemas",
            "检查空 search_path 的有效 schema 列表",
            "current_schemas(true) 不包含 public/postgres/schema1/schema2",
            _guc_pred("pg_catalog",
                      none_of=("public", "postgres", "schema1", "schema2")),
            lambda: _business_query(
                context, psql, port,
                "SET search_path = %s; SELECT current_schemas(true)::text;" % path_sql))
        result = _business_query(context, psql, port, sql)
        output = (result.stdout or "").rstrip() or "<empty>"
        passed = (result.returncode != 0 and "guc_search_path_probe" in output
                  and "does not exist" in output)
        context.step("guc-verify-empty-error",
                     "空 search_path 下未限定表名不可解析",
                     status="PASS" if passed else "FAIL",
                     details={"expected": "relation guc_search_path_probe does not exist",
                              "actual": "returncode=%s" % result.returncode,
                              "output": output})
        if not passed:
            raise AssertionError("空 search_path 下未限定表名未按预期失败")
        return
    _expect(
        context, "guc-verify-%s" % expected_schema,
        "验证 search_path 实际解析同名表",
        "未限定表名应命中 %s.guc_search_path_probe" % expected_schema,
        lambda out: any(line.strip().startswith(expected_schema + "|{")
                        and expected_schema in line
                        for line in out.splitlines()) and "(1 row)" in out,
        lambda: _business_query(context, psql, port, sql))


def _guc_log_evidence(context: CaseContext, config: Path,
                      patterns=(r"guc-sync", r"ParameterStatus", r"search_path",
                                r"fb_guc_deploy", r"fb_hint_parse_guc_batch"),
                      max_lines=8):
    """抓取代理日志中 GUC 相关片段作证（对齐 legacy extract_guc_log_evidence）。"""
    log = config.with_suffix(".log")
    if not log.is_file():
        return "日志文件尚未生成"
    regexes = [re.compile(p, re.IGNORECASE) for p in patterns]
    matched = [line.strip() for line in
               log.read_text(encoding="utf-8", errors="replace").splitlines()
               if any(rx.search(line.strip()) for rx in regexes)]
    if not matched:
        return "无匹配的 GUC 同步或解析日志"
    return "\n".join(matched[-max_lines:])


class GucSessionCase:
    """guc 套件原生宿主（对齐 legacy suites/guc/executors.py）。

    每条用例 = 启动代理（rw_split_method 参数化）→ 业务面 psql 断言序列
    → （可选）search_path 双主库同名表实解析验证。``actions`` 中 ``sql``
    为 None 的条目是纯叙述步骤（会话断开/连接复用，psql -c 单命令本身
    即一次会话）。
    """

    def __init__(self, mode, actions, verify=None):
        self.mode = mode
        self.actions = actions
        self.verify = verify

    def run(self, context: CaseContext) -> bool:
        config = context.output_dir / "fbasecman.conf"
        port = render_config(context, config, mode=self.mode)
        psql = context.environment.get("psql_bin", "/usr/bin/psql")
        text = config.read_text(encoding="utf-8")
        required = ('rw_split_method "%s"' % self.mode, "enable_guc_sync yes")
        missing = [item for item in required if item not in text]
        context.step("boot-config",
                     "启动 fbasecman 代理并加载 GUC 配置 (rw_split_method=%s)" % self.mode,
                     status="PASS" if not missing else "FAIL",
                     details={"expected": "且".join(required),
                              "actual": "缺失字段: %s" % missing if missing
                              else "配置字段已生效"})
        context.attach_text("fbasecman.conf", text)
        if missing:
            raise AssertionError("GUC 配置字段未生效: %s" % missing)
        context.start_process([context.environment["fbasecman_bin"], str(config)],
                              ready_host=context.environment.get("local_host", "127.0.0.1"), ready_port=port,
                              timeout_seconds=30)
        _wait_mmr_routing(context, port, psql)
        for index, (title, expected, sql, predicate) in enumerate(self.actions, 1):
            key = "guc-%02d" % index
            if sql is None:
                context.step(key, title,
                             details={"expected": expected,
                                      "actual": "会话级复用由连接池承接 (pool=transaction)"})
                continue
            output = _expect(context, key, title, expected, predicate,
                             lambda sql=sql: _business_query(context, psql, port, sql))
            context.attach_text("guc-log-%02d.txt" % index,
                                _guc_log_evidence(context, config))
        if self.verify:
            created = _guc_prepare_tables(context)
            context.step("guc-fixture", "双主库准备 search_path 同名表验证数据",
                         details={"schemas": list(_GUC_PROBE_SCHEMAS),
                                  "created": created})
            try:
                for path_sql, expected_schema in self.verify:
                    _guc_verify_search_path(context, psql, port,
                                            path_sql, expected_schema)
            finally:
                _guc_cleanup_tables(context, created)
                context.step("guc-fixture-cleanup", "清理 search_path 验证表")
        return True


# actions 三元组: (title, expected, sql|None, predicate|None)
_GUC_REUSE_ACTIONS = [
    ("客户端 1 设置 search_path 为 public 并即时查看",
     "SET 成功且 SHOW 返回 public，前后端 GUC 缓存更新为 public",
     "SET search_path = 'public'; SHOW search_path;",
     _guc_pred("SET", "public")),
    ("客户端 1 会话断开，客户端 2 建立新连接并复用后端连接",
     "前后端 GUC 差异触发自动重放部署",
     None, None),
    ("客户端 2 验证 search_path 恢复结果与嵌套引号防范",
     'search_path 恢复为正常 "$user", public，且无多重转义嵌套双引号',
     "SHOW search_path;",
     _guc_pred('"$user", public',
              none_of=('"""$user"", public"', "E'\"$user\", public'"))),
]

_GUC_MULTIVALUE_ACTIONS = [
    ('显式执行 SET search_path = "$user", public;',
     "返回 SET，GUC 缓存正常记录多值表达式",
     'SET search_path = "$user", public;',
     _guc_pred("SET")),
    ("校验 SHOW search_path 输出",
     '返回 "$user", public，且无嵌套引号',
     "SHOW search_path;",
     _guc_pred('"$user", public', none_of=('"""$user"", public"',))),
    ("新会话复用后端连接校验一致性",
     '依然返回 "$user", public，无污染',
     "SHOW search_path;",
     _guc_pred('"$user", public', none_of=('"""$user"", public"',))),
]

_GUC_VERIFY_REUSE = (("'public'", "public"), ('"$user", public', "postgres"))
_GUC_VERIFY_MULTIVALUE = (('"$user", public', "postgres"),)

_GUC_SPECS = {
    "search_path_reuse": (_GUC_REUSE_ACTIONS, _GUC_VERIFY_REUSE),
    "search_path_multivalue": (_GUC_MULTIVALUE_ACTIONS, _GUC_VERIFY_MULTIVALUE),
    "search_path_empty_normalize": ([
        ("客户端执行 SET search_path = '' 设置为空",
         "SET 成功且 SHOW 返回空（后端 \"\" 规范化）",
         "SET search_path = ''; SHOW search_path;",
         _guc_pred("SET", none_of=('"$user"',))),
        ("新会话复用后端连接验证恢复为默认 search_path",
         '返回 "$user", public，且无嵌套双引号',
         "SHOW search_path;",
         _guc_pred('"$user", public', none_of=('"""$user"", public"',))),
    ], (("''", None), ('"$user", public', "postgres"))),
    "search_path_mixed_quotes_cleanup": ([
        ("客户端执行多 schema 混合引号 search_path 设置",
         "SET 成功且 SHOW 返回 schema1、schema2、public",
         "SET search_path = 'schema1', \"schema2\", public; SHOW search_path;",
         _guc_pred("SET", "schema1", "schema2", "public")),
        ("新会话复用后端连接验证会话状态重置",
         '返回 "$user", public，无 schema1 残留与嵌套引号',
         "SHOW search_path;",
         _guc_pred('"$user", public',
                  none_of=("schema1", '"""$user"", public"'))),
    ], (("'schema1', \"schema2\", public", "schema1"),
        ('"schema2", public', "schema2"),
        ('"$user", public', "postgres"))),
    "reset_param": ([
        ("客户端执行 SET work_mem = '64MB'",
         "SET 成功且 SHOW 返回 64MB",
         "SET work_mem = '64MB'; SHOW work_mem;",
         _guc_pred("SET", "64MB")),
        ("客户端执行 RESET work_mem 并验证后端状态重置",
         "RESET 成功且 SHOW 返回数据库默认值 4MB",
         "RESET work_mem; SHOW work_mem;",
         _guc_pred("RESET", "4MB")),
        ("新会话复用连接验证后端无残留污染",
         "SHOW work_mem 返回 4MB 且无 64MB 残留",
         "SHOW work_mem;",
         _guc_pred("4MB", none_of=("64MB",))),
    ], None),
    "reset_all": ([
        ("客户端批量修改多个不同类型的 GUC",
         "work_mem=32MB 且 statement_timeout 生效",
         "SET work_mem = '32MB'; SET statement_timeout = '10000'; "
         "SHOW work_mem; SHOW statement_timeout;",
         _guc_pred("32MB", any_of=("10s", "10000"))),
        ("客户端执行 RESET ALL 批量重置",
         "各参数恢复数据库初始值 (4MB/0)",
         "RESET ALL; SHOW work_mem; SHOW statement_timeout;",
         _guc_pred("4MB", any_of=("0", "0ms"))),
        ("新会话复用连接验证缓存清空",
         "SHOW 返回默认值且无 32MB 残留",
         "SHOW work_mem; SHOW statement_timeout;",
         _guc_pred("4MB", any_of=("0", "0ms"), none_of=("32MB",))),
    ], None),
    "discard_all": ([
        ("客户端修改多个 GUC 参数",
         "work_mem=16MB 且 DateStyle=German",
         "SET work_mem = '16MB'; SET DateStyle = 'German, DMY'; "
         "SHOW work_mem; SHOW DateStyle;",
         _guc_pred("16MB", "German")),
        ("客户端执行 DISCARD ALL",
         "返回 DISCARD ALL",
         "DISCARD ALL;",
         _guc_pred("DISCARD ALL")),
        ("SHOW 校验所有参数恢复默认",
         "work_mem=4MB 且 DateStyle=ISO",
         "SHOW work_mem; SHOW DateStyle;",
         _guc_pred("4MB", "ISO")),
        ("新连接复用后端验证状态干净",
         "默认值且无 16MB/German 残留",
         "SHOW work_mem; SHOW DateStyle;",
         _guc_pred("4MB", "ISO", none_of=("16MB", "German"))),
    ], None),
    "set_local_transaction": ([
        ("事务内 SET LOCAL work_mem = '128MB' 并提交",
         "事务内 SHOW 返回 128MB 且 COMMIT 成功",
         "BEGIN; SET LOCAL work_mem = '128MB'; SHOW work_mem; COMMIT;",
         _guc_pred("128MB", "COMMIT")),
        ("提交后验证 work_mem 恢复事务前值",
         "SHOW 返回 4MB 且无 128MB 残留",
         "SHOW work_mem;",
         _guc_pred("4MB", none_of=("128MB",))),
        ("事务内 SET LOCAL 后 ROLLBACK 验证同样不残留",
         "ROLLBACK 成功且 SHOW 返回 4MB",
         "BEGIN; SET LOCAL work_mem = '256MB'; ROLLBACK; SHOW work_mem;",
         _guc_pred("ROLLBACK", "4MB", none_of=("256MB",))),
    ], None),
    "case_insensitive_quotes": ([
        ("SET \"TimeZone\" = 'UTC' 双引号标识符",
         "SET 成功且 SHOW TimeZone 返回 UTC",
         "SET \"TimeZone\" = 'UTC'; SHOW TimeZone;",
         _guc_pred("SET", "UTC")),
        ("SET timezone = 'Asia/Shanghai' 小写名",
         "SET 成功且返回 Asia/Shanghai",
         "SET timezone = 'Asia/Shanghai'; SHOW timezone;",
         _guc_pred("SET", "Asia/Shanghai")),
        ("SET TIMEZONE = 'PRC' 大写名覆盖",
         "SET 成功且 SHOW TimeZone 返回 PRC（key 统一规范化）",
         "SET TIMEZONE = 'PRC'; SHOW TimeZone;",
         _guc_pred("SET", "PRC")),
    ], None),
    "report_param_timezone": ([
        ("客户端执行 SET TimeZone = 'Asia/Shanghai'",
         "SET 成功且 SHOW 返回 Asia/Shanghai（ParameterStatus 同步缓存）",
         "SET TimeZone = 'Asia/Shanghai'; SHOW TimeZone;",
         _guc_pred("SET", "Asia/Shanghai")),
        ("同会话再次 SHOW 校验一致性",
         "返回 Asia/Shanghai",
         "SHOW TimeZone;",
         _guc_pred("Asia/Shanghai")),
        ("新会话复用后端连接验证 TimeZone 保持",
         "返回 Asia/Shanghai",
         "SHOW TimeZone;",
         _guc_pred("Asia/Shanghai")),
    ], None),
}


def guc_case(name):
    """按 manifest 名称构造 guc 原生用例（<stem>_<sql_parse|hint> 或无后缀）。"""
    if name.endswith("_sql_parse"):
        mode, stem = "sql_parse", name[:-len("_sql_parse")]
    elif name.endswith("_hint"):
        mode, stem = "hint", name[:-len("_hint")]
    else:
        # empty_normalize 为 sql_parse 专用，mixed_quotes_cleanup 为 hint 专用
        stem = name
        mode = "hint" if name == "search_path_mixed_quotes_cleanup" else "sql_parse"
    actions, verify = _GUC_SPECS[stem]
    return GucSessionCase(mode, actions, verify)
