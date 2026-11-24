"""Monitor regressions with isolated PostgreSQL and fbasecman processes."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
from tempfile import mkdtemp
import time

from platform_regress.sdk import Blocked, CaseContext, CaseFailure
from products.fbasecman.regression.suites.high_availability.console_parser import (
    ConsoleSnapshot,
)

HOST = "127.0.0.1"
USERS = ("warm_user", "cold_user")
QUERY = "SELECT inet_server_port(), pg_is_in_recovery();"
CLUSTERS = "SHOW CLUSTERS;"
ROUTING = "SHOW GROUP_ROUTING;"


def _ports():
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(3)]
        for listener in sockets:
            listener.bind((HOST, 0))
        return [listener.getsockname()[1] for listener in sockets]


def _config(root, license_dir, primary, standby, proxy, verifiers):
    lines = [
        f'pid_file "{root}/cman.pid"', 'daemonize no',
        f'unix_socket_dir "{root}"', 'unix_socket_mode "0644"',
        'log_to_stdout yes', 'log_config no',
        r'log_format "%p %t %l (%c) %m\n"',
        f'locks_dir "{root}"', f'config_backup_dir "{root}"',
        f'license_dir {json.dumps(str(license_dir))}',
        'workers 2', 'resolvers 1', 'backlog 128',
        'coroutine_stack_size 16', 'readahead 8192',
        'monitor_enabled yes', 'monitor_strict_primary_check no',
        'monitor_period 2', 'monitor_recovery_period 2',
        'monitor_max_retries 2', 'monitor_retry_period_ms 500',
        f'host "{HOST}"', f'ports "{proxy}"', 'admin_database "console"',
        'group "repro" {', '  group_mode "replication"',
        '  storage_db "postgres"', '  backend_clusters "repro_cluster"',
        '  check "auto"', '}',
    ]
    for name, port in (("old_primary", primary), ("standby", standby)):
        lines.extend([
            f'datasources "{name}" {{', f'  host "{HOST}"', f'  port {port}',
            '  cluster_name "repro_cluster"', f'  application_name "{name}"',
            '  status "active"', '  weight 10', '  tls "disable"', '}',
        ])
    lines.extend(['user "admin" {', '  authentication "none"',
                  '  role "admin"', '  pool "session"', '}'])
    for user in USERS:
        verifier = verifiers[user]
        lines.extend([
            f'user "{user}" {{', '  group_names "repro"',
            '  authentication "scram-sha-256"', f'  password "{verifier}"',
            f'  storage_user "{user}"', f'  storage_password "{verifier}"',
            '  rw_split_method "hint"', '  pool "transaction"', '}',
        ])
    return "\n".join(lines) + "\n"


def _psql(bindir, host, port, user, db, *, passfile=None):
    argv = [str(bindir / "psql"), "-X", "-w", "-A", "-P", "footer=off",
            "-v", "ON_ERROR_STOP=1"]
    if passfile is not None:
        return argv + ["-d", f"host={host} port={port} user={user} dbname={db} "
                       f"passfile={passfile} connect_timeout=4"]
    return argv + ["-h", str(host), "-p", str(port), "-U", user, "-d", db]


def _create_users(bindir, root, port, password):
    # Keeping the verifier query outside command evidence avoids archiving credentials.
    command = "SET password_encryption = 'scram-sha-256';\n"
    command += "".join(f"CREATE ROLE {user} LOGIN SUPERUSER PASSWORD '{password}';\n"
                      for user in USERS)
    command += ("SELECT rolname || ' ' || rolpassword FROM pg_authid "
                "WHERE rolname IN ('warm_user', 'cold_user');")
    result = subprocess.run(
        _psql(bindir, root, port, "postgres", "postgres") + ["-t"],
        input=command, capture_output=True, text=True, timeout=15, check=False,
    )
    verifiers = dict(line.split(" ", 1) for line in result.stdout.splitlines()
                     if line.startswith(("warm_user ", "cold_user ")))
    if result.returncode or set(verifiers) != set(USERS) or any(
        not value.startswith("SCRAM-SHA-256$") for value in verifiers.values()
    ):
        raise Blocked("无法在独立主库创建两个 SCRAM verifier 用户")
    return verifiers


def _show(context, bindir, proxy, statement):
    result = context.command(
        _psql(bindir, HOST, proxy, "admin", "console") + ["-c", statement],
        timeout_seconds=10,
    )
    return result, ConsoleSnapshot(result.stdout)


def _cluster(snapshot):
    rows = snapshot.find_rows(cluster_name="repro_cluster")
    return rows[0] if len(rows) == 1 else {}


def _display_facts(value):
    if value is None:
        return "无"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, dict):
        return "，".join(f"{name}为{_display_facts(item)}" for name, item in value.items())
    if isinstance(value, list):
        if not value:
            return "无"
        if all(item == value[0] for item in value):
            detail = _display_facts(value[0])
            return detail if len(value) == 1 else f"{len(value)} 次均为「{detail}」"
        return "\n".join(f"第 {index} 次：{_display_facts(item)}"
                         for index, item in enumerate(value, 1))
    return str(value)


def _record(context, key, title, expected, actual, passed, analysis, *,
            evidence=None, command=None, node=None, intent="verify"):
    details = {"intent": intent, "expected": _display_facts(expected),
               "actual": _display_facts(actual),
               "analysis": analysis, "evidence": evidence, "command": command, "node": node}
    if intent == "verify":
        details["assertion"] = {"type": "fields_equal", "passed": passed,
                                "expected": expected, "actual": actual}
    context.step(key, title, status="PASS" if passed else "FAIL",
                 details={key: value for key, value in details.items() if value is not None})
    return passed


def _connection_result(result):
    port, separator, recovery = result.stdout.strip().partition("|")
    if separator and port.isdigit() and recovery in ("t", "f"):
        return {"后端端口": int(port), "处于恢复模式": recovery == "t"}
    return {"退出码": result.returncode,
            "输出": (result.stderr or result.stdout).strip() or "无输出"}


def _wait_topology(context, bindir, proxy, state, primary):
    deadline = time.monotonic() + 35
    while True:
        result, snapshot = _show(context, bindir, proxy, CLUSTERS)
        row = _cluster(snapshot)
        expected = {"拓扑": state, "主库": primary}
        actual = {"拓扑": row.get("topology_state"), "主库": row.get("current_primary")}
        passed = result.returncode == 0 and actual == expected
        if passed or time.monotonic() >= deadline:
            title = "确认故障前拓扑" if state == "VALID" else "确认旧主失联后监控已发布降级拓扑"
            _record(context, f"topology-{state.lower()}", title, expected, actual, passed,
                    "按 cluster_name 定位 repro_cluster，逐项比较 topology_state 和 current_primary。",
                    evidence=result.evidence, command=CLUSTERS, node="fbasecman 控制台")
            if not passed:
                raise CaseFailure(f"{title}超时，期望 {expected}，实际 {actual}")
            return result, row
        time.sleep(1)


def _business(context, bindir, proxy, passfile, user):
    return context.command(
        _psql(bindir, HOST, proxy, user, "repro", passfile=passfile) + ["-t", "-c", QUERY],
        timeout_seconds=15,
    )


class ScramFirstConnectDegradedCase:
    def run(self, context: CaseContext) -> bool:
        env = context.environment
        binary = env.get("fbasecman_bin")
        license_dir = env.get("license_dir")
        bindir = Path(env.get("psql_bin") or shutil.which("psql") or "/usr/bin/psql").parent
        tools = ("psql", "initdb", "postgres", "pg_ctl", "pg_basebackup")
        if not binary or not os.access(binary, os.X_OK) or not license_dir or not (
            Path(license_dir) / "license.dat"
        ).is_file():
            raise Blocked("需要本机可执行的 fbasecman 和有效 license.dat")
        if os.geteuid() == 0 or any(not os.access(bindir / name, os.X_OK) for name in tools):
            raise Blocked("需要非 root 用户和本机完整 PostgreSQL 工具目录（由 psql_bin 定位）")

        root = Path(mkdtemp(prefix="cman-monitor-"))
        def cleanup():
            shutil.rmtree(root)
            _record(context, "fixture-cleanup", "清理独立测试实例的临时目录",
                    "删除本轮数据目录及临时认证配置", "临时目录已删除", not root.exists(),
                    "平台先回收用例进程，再删除临时目录。", intent="cleanup")

        context.defer_cleanup(cleanup)
        primary, standby, proxy = _ports()
        primary_data, standby_data = root / "primary", root / "standby"

        def prepare(tool, *args, action=None):
            result = context.command([str(bindir / tool), *map(str, args)], timeout_seconds=60)
            if action:
                title, node = action
                _record(context, f"pg-{args[-1]}", title, "命令退出码为 0",
                        {"退出码": result.returncode}, result.returncode == 0,
                        "根据 pg_ctl 的退出状态确认本轮故障注入动作。",
                        evidence=result.evidence, command=f"{tool} {args[-1]}",
                        node=node, intent="action")
            if result.returncode:
                raise Blocked(f"独立主备环境操作失败：{tool} 返回 {result.returncode}，见命令证据")
            return result

        def start_db(data, port):
            context.start_process([str(bindir / "postgres"), "-D", str(data),
                                   "-p", str(port)],
                                  ready_host=HOST, ready_port=port, timeout_seconds=20)

        prepare("initdb", "-D", primary_data, "-U", "postgres",
                "--auth-local=trust", "--auth-host=scram-sha-256")
        for filename, content in (
            ("postgresql.conf", f"\nlisten_addresses='{HOST}'\n"
                                f"unix_socket_directories='{root}'\nwal_level=replica\n"),
            ("pg_hba.conf", f"host replication postgres {HOST}/32 trust\n"),
        ):
            path = primary_data / filename
            old = path.read_text()
            path.write_text(content + old if filename == "pg_hba.conf" else old + content)
        start_db(primary_data, primary)
        password = secrets.token_hex(16)
        verifiers = _create_users(bindir, root, primary, password)
        prepare("pg_basebackup", "-h", HOST, "-p", primary, "-U", "postgres",
                "-D", standby_data, "-R", "-X", "stream", "-c", "fast")
        with (standby_data / "postgresql.auto.conf").open("a") as conf:
            conf.write(f"\nprimary_conninfo = 'host={HOST} port={primary} "
                       "user=postgres application_name=standby'\n")
        start_db(standby_data, standby)

        config = root / "cman.conf"
        config.write_text(_config(root, license_dir, primary, standby, proxy, verifiers))
        passfile = root / "pgpass"
        passfile.write_text("".join(f"{HOST}:{proxy}:repro:{user}:{password}\n" for user in USERS))
        passfile.chmod(0o600)
        context.start_process([binary, str(config)], ready_host=HOST, ready_port=proxy,
                              timeout_seconds=20)

        fixture = {"业务认证": "SCRAM-SHA-256 verifier", "组": "repro",
                   "监控": "开启", "严格唯一主库检查": "关闭",
                   "旧主端口": primary, "备库端口": standby, "代理端口": proxy}
        fixture_ref = context.attach_text("monitor-fixture.json",
                                          json.dumps(fixture, ensure_ascii=False, indent=2))
        _record(context, "fixture-ready", "准备独立主备环境和两个 SCRAM 用户",
                "代理与主备实例均已启动，冷用户尚未连接", fixture, True,
                "两个业务账号使用本轮生成的 verifier，先仅使用 warm_user 访问代理。",
                evidence=fixture_ref, intent="prepare")

        warm = _business(context, bindir, proxy, passfile, USERS[0])
        if not _record(context, "warm-cache", "用已有用户建立故障前的业务连接",
                       {"后端端口": primary, "处于恢复模式": False},
                       _connection_result(warm),
                       warm.returncode == 0 and warm.stdout.strip() == f"{primary}|f",
                       "用实际后端端口和恢复标志确认 warm_user 已连接旧主。",
                       evidence=warm.evidence, command=QUERY, node="warm_user"):
            raise CaseFailure("warm_user 未能建立故障前的旧主连接")
        _wait_topology(context, bindir, proxy, "VALID", "old_primary")

        prepare("pg_ctl", "-D", primary_data, "-m", "fast", "-w", "stop",
                action=("停止旧主，保留其 active 配置", "old_primary"))
        prepare("pg_ctl", "-D", standby_data, "-w", "promote",
                action=("将原备库提升为主库", "standby"))
        degraded, row = _wait_topology(context, bindir, proxy, "VALID_DEGRADED", "standby")
        member = row.get("degraded_members")
        if not _record(context, "degraded-member", "确认旧主仍列为降级成员",
                       "old_primary", member or "无降级成员", member == "old_primary",
                       "核对 SHOW CLUSTERS 的 degraded_members，确认旧主失联仍参与拓扑决议。",
                       evidence=degraded.evidence, command=CLUSTERS, node="repro_cluster"):
            raise CaseFailure("旧主没有被监控列为降级成员")
        before, routing = _show(context, bindir, proxy, ROUTING)
        before_writers = [row.get("candidate_node") for row in
                          routing.find_rows(user_name=USERS[0], is_write_target="true")]
        if not _record(context, "write-before", "建立冷用户首连前的写路由基线",
                       ["standby"], before_writers,
                       before.returncode == 0 and before_writers == ["standby"],
                       "按 warm_user 筛选写候选，要求只有提升后的 standby。",
                       evidence=before.evidence, command=ROUTING, node="warm_user"):
            raise CaseFailure("首连前 warm_user 的唯一写目标不是新主")

        cold_argv = (_psql(bindir, HOST, proxy, USERS[1], "repro", passfile=passfile)
                     + ["-t", "-c", QUERY])
        cold_process = context.start_command(cold_argv)
        warm_samples = []
        overlapping = 0
        for _ in range(3):
            overlapping += cold_process.poll() is None
            warm_samples.append(_business(context, bindir, proxy, passfile, USERS[0]))
        cold = context.finish_command(cold_argv, cold_process, timeout_seconds=15)
        cold_ref = context.attach_text("cold-first-connect.json", json.dumps(
            {"argv": cold_argv, "returncode": cold.returncode, "output": cold.stdout},
            ensure_ascii=False, indent=2))
        samples_ref = context.attach_text("warm-connection-samples.json", json.dumps(
            [{"returncode": item.returncode, "stdout": item.stdout, "stderr": item.stderr,
              "command_evidence": item.evidence} for item in warm_samples],
            ensure_ascii=False, indent=2))
        expected_route = {"后端端口": standby, "处于恢复模式": False}
        cold_ok = _record(
            context, "cold-first-connect", "验证冷用户首连到达新主",
            expected_route, _connection_result(cold),
            cold.returncode == 0 and _connection_result(cold) == expected_route,
            "核对实际后端端口与 pg_is_in_recovery()，确认冷用户首个会话命中新主。",
            evidence=cold_ref, command=QUERY, node="cold_user")
        sample_results = [_connection_result(item) for item in warm_samples]
        samples_ok = _record(
            context, "warm-concurrent", "验证冷用户首连期间已有用户仍可连接",
            {"连接结果": [expected_route] * 3, "冷连接在途时启动的采样": "至少 1 次"},
            {"连接结果": sample_results, "冷连接在途时启动的采样": overlapping},
            overlapping > 0 and sample_results == [expected_route] * 3
            and all(item.returncode == 0 for item in warm_samples),
            "三次重连均核对后端端口和恢复标志，并记录启动采样时冷连接进程是否仍在运行。",
            evidence=samples_ref, command=QUERY, node="warm_user")

        after, topology = _show(context, bindir, proxy, CLUSTERS)
        row = _cluster(topology)
        expected_topology = {"拓扑": "VALID_DEGRADED", "主库": "standby"}
        actual_topology = {"拓扑": row.get("topology_state"), "主库": row.get("current_primary")}
        topology_ok = _record(
            context, "topology-after", "核对首连后监控仍认定新主可用",
            expected_topology, actual_topology,
            after.returncode == 0 and actual_topology == expected_topology,
            "将首连后的拓扑与首连前已发布的降级拓扑对照，检查是否被改为 UNRESOLVED。",
            evidence=after.evidence, command=CLUSTERS, node="repro_cluster")

        routes, routing = _show(context, bindir, proxy, ROUTING)
        expected_writers = {user: ["standby"] for user in USERS}
        writers = {user: [item.get("candidate_node") for item in
                         routing.find_rows(user_name=user, is_write_target="true")]
                   for user in USERS}
        routes_ok = _record(
            context, "routing-after", "核对首连后两个用户仍各有一个写目标",
            expected_writers, writers, routes.returncode == 0 and writers == expected_writers,
            "分别核对两个账号的写候选数量和目标，确认共享写路由没有被清空。",
            evidence=routes.evidence, command=ROUTING, node="repro")

        warm = _business(context, bindir, proxy, passfile, USERS[0])
        warm_ok = _record(
            context, "warm-after", "验证冷用户首连完成后已有用户仍可重连",
            expected_route, _connection_result(warm),
            warm.returncode == 0 and _connection_result(warm) == expected_route,
            "另起连接，确认已有用户在首连完成后继续到达新主。",
            evidence=warm.evidence, command=QUERY, node="warm_user")
        if not all((cold_ok, samples_ok, topology_ok, routes_ok, warm_ok)):
            raise CaseFailure(f"冷用户首连后拓扑为 {actual_topology['拓扑']}，"
                              f"写目标为「{_display_facts(writers)}」。失败的连接与错配字段见验证步骤。")
        return True
