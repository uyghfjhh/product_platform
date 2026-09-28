"""Native fbasecman process and protocol case implementation.

The renderer consumes only the platform injected pgcluster topology.  It does
not read the legacy regression framework or its test_context file.
"""

from __future__ import annotations

import json
import re
import socket
import struct
import sys
from pathlib import Path

from platform_regress import Blocked, CaseContext
from platform_regress.clients import jdbc as jdbc_client


def render_config(context: CaseContext, path: Path, *, mode: str = "sql_parse") -> int:
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
    ]
    for name, node in selected_nodes.items():
        cluster = "pg_cluster_1" if name in {"pg_1", "pg_3"} else "pg_cluster_2"
        app_name = "pg_240" if name == "pg_3" else ("pg_250" if name == "pg_4" else None)
        data += [f'datasources "{name}" {{', f'    host "{node["host"]}"',
                 f'    port {node["port"]}', f'    cluster_name "{cluster}"',
                 '    weight 10', '    status "active"',
                 *([f'    application_name "{app_name}"'] if app_name else []),
                 f'    system_identifier "{identifiers[name]}"', '    tls "disable"', '}',]
    data += ['user "postgres" {', '    group_names "mmr_group"',
             '    authentication "none"', '    storage_user "postgres"',
             '    pool "transaction"', '    pool_size 20', '    pool_discard no',
             '    pool_reserve_prepared_statement yes',
             f'    rw_split_method "{mode}"', '}',
             'user "admin" {', '    authentication "none"', '    pool "session"',
             '    role "admin"', '}']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(data) + "\n", encoding="utf-8")
    return port


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
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
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
                              ready_host="127.0.0.1", ready_port=port, timeout_seconds=30)
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
        backend_check = context.command([psql, "-X", "-A", "-t", "-h", "127.0.0.1",
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
                              ready_host="127.0.0.1", ready_port=port, timeout_seconds=30)
        asset = Path(context.environment.get("sql_parse_java_asset", ""))
        jar = Path(context.environment.get("jdbc_jar", ""))
        if not asset.is_file() or not jar.is_file():
            raise Blocked("缺少 SQL_PARSE JDBC 测试资产或驱动")
        context.command(jdbc_client.javac_argv(jar, asset, dest_dir=context.output_dir),
                        cwd=context.output_dir, timeout_seconds=60)
        url = jdbc_client.build_url(
            "127.0.0.1", port, "mmr_group",
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
                              ready_host="127.0.0.1", ready_port=port, timeout_seconds=30)
        context.command(jdbc_client.javac_argv(jar, asset, dest_dir=context.output_dir),
                        cwd=context.output_dir, timeout_seconds=60)
        nodes = context.environment.get("nodes") or {}
        ports = [str(nodes[name]["port"]) for name in ("mmr1", "mmr2") if name in nodes]
        extra = context.environment.get("extra_nodes") or {}
        ports += [str(extra[name]["port"]) for name in ("pg_3",) if name in extra]
        if len(ports) < 3:
            raise Blocked("HA JDBC 用例缺少 MMR 主节点或备节点")
        snapshots = context.output_dir / "jdbc-config-snapshots"
        urls = [jdbc_client.build_url("127.0.0.1", port, db,
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
                              ready_host="127.0.0.1", ready_port=port, timeout_seconds=30)
        psql = context.environment.get("psql_bin", "/usr/bin/psql")
        def query(sql):
            return context.command([psql, "-X", "-A", "-t", "-h", "127.0.0.1",
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
                              ready_host="127.0.0.1", ready_port=port, timeout_seconds=30)
        psql = context.environment.get("psql_bin", "/usr/bin/psql")
        def q(sql):
            return context.command([psql, "-X", "-A", "-t", "-h", "127.0.0.1", "-p", str(port),
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
                              ready_host="127.0.0.1", ready_port=port, timeout_seconds=30)
        psql = context.environment.get("psql_bin", "/usr/bin/psql")
        def q(sql):
            return context.command([psql, "-X", "-A", "-t", "-h", "127.0.0.1", "-p", str(port),
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
        context.start_process([binary, str(config)], ready_host="127.0.0.1", ready_port=port,
                              timeout_seconds=30)
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
