"""HA console command executors: ROUTING."""

import time
import os
try:
    import fcntl
except ImportError:
    fcntl = None
import sys
import shlex
from pathlib import Path

from framework.configuration import load_regression_config
from suites.ha_commands.runtime import HaCommandFailure, HaCommandRuntime
from suites.ha_commands.helpers import *

__all__ = ['_run_balance_read_only_route', '_run_balance_route', '_run_four_group_modes_route_visibility', '_run_mmr_hint_read_route', '_run_mmr_hint_route', '_run_mmr_port_read_route', '_run_mmr_port_write_route', '_run_mmr_sql_parse_read_write_transactions', '_run_rep_hint_write_route', '_run_rep_port_read_route', '_run_rep_port_write_route', '_run_rep_sql_parse_read_write_transactions', '_run_replication_route', '_run_single_route', '_run_sql_parse_extended_protocol']


def _run_mmr_hint_route(rt):
    port = str(rt.env.config["database"]["ports"]["mmr2"])
    _run_route_mode(
        rt, "mmr_group", "mmr",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "写 Hint 请求落到 pg_cluster_2 的 pg_2 端口",
        lambda output: port in output and "postgres" in output,
        transform=_hint_transform("mmr_group"),
    )


def _run_single_route(rt):
    port = str(rt.env.config["database"]["ports"]["mmr1"])
    _run_route_mode(
        rt, "single_group", "single",
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "single 请求固定落到 pg_cluster_1 主端口",
        lambda output: port in output and "postgres" in output,
    )


def _run_rep_hint_write_route(rt):
    primary = str(rt.env.config["database"]["ports"]["mmr1"])
    _run_route_mode(
        rt, "rep_group", "replication",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "写 Hint 请求落到 replication primary",
        lambda output: primary in output and "postgres" in output,
        transform=_hint_transform("rep_group"),
    )


def _run_rep_port_read_route(rt):
    ports = rt.env.config["database"]["ports"]
    readable = (str(ports["mmr1_standby1"]), str(ports["mmr1"]))
    _run_route_mode(
        rt, "rep_group", "replication",
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "非 write_port 请求落到 replication 读候选",
        lambda output: any(port in output for port in readable) and "postgres" in output,
        transform=_port_transform(rt, "rep_group"), port=rt.read_port,
    )


def _run_rep_port_write_route(rt):
    backend = str(rt.env.config["database"]["ports"]["mmr1"])
    _run_route_mode(
        rt, "rep_group", "replication",
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "write_port 请求落到 replication cluster 的 primary 端口",
        lambda output: backend in output and "postgres" in output,
        transform=_port_transform(rt, "rep_group"), port=rt.listen_port,
    )


def _run_balance_route(rt):
    ports = rt.env.config["database"]["ports"]
    allowed = tuple(str(ports[key]) for key in
                    ("mmr1", "mmr1_standby1", "mmr2", "mmr2_standby1"))
    _run_route_mode(
        rt, "balance_group", "balance",
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "balance 请求落到配置的后端候选端口",
        lambda output: any(port in output for port in allowed) and "postgres" in output,
    )


def _run_mmr_port_write_route(rt):
    backend = str(rt.env.config["database"]["ports"]["mmr2"])
    _run_route_mode(
        rt, "mmr_group", "mmr",
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "write_port 请求落到 MMR write cluster 的 pg_2 端口",
        lambda output: backend in output and "postgres" in output,
        transform=_port_transform(rt, "mmr_group"), port=rt.listen_port,
    )


def _run_sql_parse_extended_protocol(rt):
    rt.start(transform=_sql_parse_transform("mmr_group"))
    jar = rt.root / rt.env.config["local"]["jdbc_lib_dir"] / "postgresql-42.7.7.jar"
    source = rt.root / "suites" / "ha_commands" / "assets" / "jdbc" / "HaSqlParseExtended.java"
    if not jar.exists() or not source.exists():
        raise HaCommandFailure("missing JDBC asset or jar: %s %s" % (source, jar))
    rt.run_command(
        ["javac", "-cp", str(jar), "-d", str(rt.workdir), str(source)],
        rt.logs_dir / "HaSqlParseExtended.javac.log", cwd=rt.workdir,
        step_title="编译 sql_parse 扩展协议 JDBC driver")
    jdbc_url = (
        "jdbc:postgresql://127.0.0.1:%s/mmr_group?"
        "prepareThreshold=1&preferQueryMode=extended" % rt.listen_port)
    _, output = rt.run_command(
        ["java", "-cp", "%s:%s" % (rt.workdir, jar),
         "HaSqlParseExtended", jdbc_url, "postgres", ""],
        rt.logs_dir / "HaSqlParseExtended.log", cwd=rt.workdir,
        step_title="执行 sql_parse 扩展协议 PreparedStatement 时序")
    ports = rt.env.config["database"]["ports"]
    read_ports = tuple(str(ports[name]) for name in
                       ("mmr1", "mmr1_standby1", "mmr2", "mmr2_standby1"))
    expected_write = str(ports["mmr2"])
    read_ok = "READ_PORT=" in output and any(
        "READ_PORT=%s" % port in output for port in read_ports)
    write_ok = "WRITE_PORT=%s" % expected_write in output
    recovery_ok = all(marker in output for marker in (
        "ROLLBACK_RECOVERY=OK", "COMMIT_RECOVERY=OK", "PARAM_VALUE=42"))
    rt.check(
        "验证扩展协议 Parse/Bind/Execute 路由及失败事务恢复",
        "参数化 SELECT 命中读候选，DDL 命中当前 write-leader；失败事务经 ROLLBACK/COMMIT 后均可继续",
        output, read_ok and write_ok and recovery_ok)


def _run_rep_sql_parse_read_write_transactions(rt):
    ports = rt.env.config["database"]["ports"]
    _run_sql_parse_transactions(
        rt, "rep_group", "replication",
        (ports["mmr1_standby1"], ports["mmr1"]), ports["mmr1"])


def _run_mmr_port_read_route(rt):
    ports = rt.env.config["database"]["ports"]
    readable = tuple(str(ports[key]) for key in
                     ("mmr1", "mmr1_standby1", "mmr2_standby1"))
    _run_route_mode(
        rt, "mmr_group", "mmr",
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "非 write_port 请求落到 MMR 可读候选",
        lambda output: any(port in output for port in readable) and "postgres" in output,
        transform=_port_transform(rt, "mmr_group"), port=rt.read_port,
    )


def _run_four_group_modes_route_visibility(rt):
    rt.start()
    for group_name, mode in (
            ("mmr_group", "mmr"), ("rep_group", "rep"),
            ("balance_group", "balance"), ("single_group", "single")):
        rt.psql(
            'SHOW GROUP_ROUTING %s;' % group_name,
            "查看 %s 的运行态" % group_name,
            '%s 的 group_mode 为 %s，且存在 VALID 路由候选' % (group_name, mode),
            lambda output, group_name=group_name, mode=mode: all(value in output for value in (
                group_name, mode, "active",
            )),
        )


def _run_mmr_sql_parse_read_write_transactions(rt):
    ports = rt.env.config["database"]["ports"]
    _run_sql_parse_transactions(
        rt, "mmr_group", "mmr",
        (ports["mmr1"], ports["mmr1_standby1"], ports["mmr2"], ports["mmr2_standby1"]),
        ports["mmr2"])


def _run_balance_read_only_route(rt):
    conf = rt.start(transform=_balance_read_only_transform(rt))
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    ports = rt.env.config["database"]["ports"]
    pg5_port = str(ports["mmr1_standby2"])
    rt.psql('SHOW GROUP_ROUTING balance_read_only;',
            "查看双 replica Balance read-only 初始路由",
            "两个 replica 均 active，group 为 READ_PREFERRED/VALID",
            lambda output: all(value in output for value in
                               ("balance_read_only", "READ_PREFERRED", "pg_3", "pg_5", "active")))
    rt.psql('SET NODE WEIGHT pg_3=0;',
            "将 pg_3 权重设为 0",
            "返回 SET NODE，pg_3 不再参与新业务连接选择",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    for attempt in range(1, 5):
        rt.psql_business(
            'SELECT inet_server_port(), pg_is_in_recovery();',
            "验证 weight=0 后第 %d 次 Balance 只读路由" % attempt,
            "连接只命中 weight=10 的 pg_5 replica",
            lambda output: pg5_port in output and " t" in output,
            group="balance_read_only", user="balance_reader")
    rt.psql('SET NODE PARTED pg_3,pg_5;',
            "隔离 Balance read-only 的全部 replica",
            "返回 SET NODE，两个 replica 均进入 parted",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.psql_business(
        'SELECT inet_server_port(), pg_is_in_recovery();',
        "验证无 active replica 时 Balance read-only 回退 primary",
        "源码定义 read_only Balance 无 replica 时回退 pg_cluster_1 primary",
        lambda output: str(ports["mmr1"]) in output and " f" in output,
        group="balance_read_only", user="balance_reader")
    rt.psql('SET NODE ACTIVE pg_3,pg_5;',
            "恢复 Balance read-only 的全部 replica",
            "返回 SET NODE，两个 replica 均恢复 active",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    _wait_pg_cluster_ready(
        rt, "pg_cluster_1", "pg_1", ("pg_3", "pg_5"),
        "等待 Balance replica ACTIVE 后 monitor 投影恢复")
    rt.psql_business(
        'SELECT inet_server_port(), pg_is_in_recovery();',
        "验证 replica ACTIVE 后 Balance read-only 路由恢复",
        "连接重新命中 weight=10 的 pg_5 replica",
        lambda output: pg5_port in output and " t" in output,
        group="balance_read_only", user="balance_reader")
    rt.psql('SET NODE WEIGHT pg_3=10;',
            "恢复 pg_3 初始权重",
            "返回 SET NODE，配置恢复初始权重",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.diff(before, conf)


def _run_replication_route(rt):
    ports = rt.env.config["database"]["ports"]
    allowed = (str(ports["mmr1"]), str(ports["mmr1_standby1"]))
    _run_route_mode(
        rt, "rep_group", "replication",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY; "
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "只读请求落到 replication group 的 pg_cluster_1 主备之一",
        lambda output: any(port in output for port in allowed) and "postgres" in output,
        transform=_hint_transform("rep_group"),
    )


def _run_mmr_hint_read_route(rt):
    ports = rt.env.config["database"]["ports"]
    readable = tuple(str(ports[key]) for key in
                     ("mmr1", "mmr1_standby1", "mmr2", "mmr2_standby1"))
    _run_route_mode(
        rt, "mmr_group", "mmr",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY; "
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "只读 Hint 请求落到 MMR 合法读候选；无优先读节点时允许回退 write-leader",
        lambda output: any(port in output for port in readable) and "postgres" in output,
        transform=_hint_transform("mmr_group"),
    )


