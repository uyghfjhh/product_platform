import os
"""HA console command executors: ROUTING."""

from platform_regress.clients import jdbc as jdbc_client

from suites.ha_commands.runtime import HaCommandFailure
from suites.ha_commands.helpers import *

LOCAL_HOST = os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1")
__all__ = ['_run_balance_read_only_route', '_run_balance_route', '_run_four_group_modes_route_visibility', '_run_mmr_hint_read_route', '_run_mmr_hint_route', '_run_mmr_port_read_route', '_run_mmr_port_write_route', '_run_mmr_sql_parse_read_write_transactions', '_run_rep_hint_write_route', '_run_rep_port_read_route', '_run_rep_port_write_route', '_run_rep_sql_parse_read_write_transactions', '_run_replication_route', '_run_single_route', '_run_sql_parse_extended_protocol']


def _run_mmr_hint_route(context):
    ops = context.ops
    port = ops.env.config["database"]["ports"]["mmr2"]
    _run_route_mode(
        context, "mmr_group", "mmr",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "写 Hint 请求落到 pg_cluster_2 的 pg_2 端口",
        route_port=port, recovery=False,
        transform=_hint_transform("mmr_group"),
    )


def _run_single_route(context):
    ops = context.ops
    port = ops.env.config["database"]["ports"]["mmr1"]
    _run_route_mode(
        context, "single_group", "single",
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "single 请求固定落到 pg_cluster_1 主端口",
        route_port=port, recovery=False,
    )


def _run_rep_hint_write_route(context):
    ops = context.ops
    primary = ops.env.config["database"]["ports"]["mmr1"]
    _run_route_mode(
        context, "rep_group", "replication",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "写 Hint 请求落到 replication primary",
        route_port=primary, recovery=False,
        transform=_hint_transform("rep_group"),
    )


def _run_rep_port_read_route(context):
    ops = context.ops
    ports = ops.env.config["database"]["ports"]
    readable = (ports["mmr1_standby1"], ports["mmr1"])
    _run_route_mode(
        context, "rep_group", "replication",
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "非 write_port 请求落到 replication 读候选",
        route_ports=readable,
        transform=_port_transform(context, "rep_group"), port=ops.read_port,
    )


def _run_rep_port_write_route(context):
    ops = context.ops
    backend = ops.env.config["database"]["ports"]["mmr1"]
    _run_route_mode(
        context, "rep_group", "replication",
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "write_port 请求落到 replication cluster 的 primary 端口",
        route_port=backend, recovery=False,
        transform=_port_transform(context, "rep_group"), port=ops.listen_port,
    )


def _run_balance_route(context):
    ops = context.ops
    ports = ops.env.config["database"]["ports"]
    allowed = tuple(ports[key] for key in
                    ("mmr1", "mmr1_standby1", "mmr2", "mmr2_standby1"))
    _run_route_mode(
        context, "balance_group", "balance",
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "balance 请求落到配置的后端候选端口",
        route_ports=allowed,
    )


def _run_mmr_port_write_route(context):
    ops = context.ops
    backend = ops.env.config["database"]["ports"]["mmr2"]
    _run_route_mode(
        context, "mmr_group", "mmr",
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "write_port 请求落到 MMR write cluster 的 pg_2 端口",
        route_port=backend, recovery=False,
        transform=_port_transform(context, "mmr_group"), port=ops.listen_port,
    )


def _run_sql_parse_extended_protocol(context):
    ops = context.ops
    ops.start(transform=_sql_parse_transform("mmr_group"))
    jar = jdbc_client.resolve_jar(ops.root / ops.env.config["local"]["jdbc_lib_dir"], None)
    source = ops.root / "suites" / "ha_commands" / "assets" / "jdbc" / "HaSqlParseExtended.java"
    if not jar.exists() or not source.exists():
        raise HaCommandFailure("missing JDBC asset or jar: %s %s" % (source, jar))
    ops.run_command(
        ["javac", "-cp", str(jar), "-d", str(ops.workdir), str(source)],
        ops.logs_dir / "HaSqlParseExtended.javac.log", cwd=ops.workdir,
        step_title="编译 sql_parse 扩展协议 JDBC driver")
    jdbc_url = (
        f"jdbc:postgresql://{LOCAL_HOST}:{ops.listen_port}/mmr_group?"
        "prepareThreshold=1&preferQueryMode=extended")
    _, output = ops.run_command(
        ["java", "-cp", "%s:%s" % (ops.workdir, jar),
         "HaSqlParseExtended", jdbc_url, "postgres", ""],
        ops.logs_dir / "HaSqlParseExtended.log", cwd=ops.workdir,
        step_title="执行 sql_parse 扩展协议 PreparedStatement 时序")
    ports = ops.env.config["database"]["ports"]
    read_ports = tuple(str(ports[name]) for name in
                       ("mmr1", "mmr1_standby1", "mmr2", "mmr2_standby1"))
    expected_write = str(ports["mmr2"])
    read_ok = "READ_PORT=" in output and any(
        "READ_PORT=%s" % port in output for port in read_ports)
    write_ok = "WRITE_PORT=%s" % expected_write in output
    recovery_ok = all(marker in output for marker in (
        "ROLLBACK_RECOVERY=OK", "COMMIT_RECOVERY=OK", "PARAM_VALUE=42"))
    ops.check(
        "验证扩展协议 Parse/Bind/Execute 路由及失败事务恢复",
        "参数化 SELECT 命中读候选，DDL 命中当前 write-leader；失败事务经 ROLLBACK/COMMIT 后均可继续",
        output, read_ok and write_ok and recovery_ok)


def _run_rep_sql_parse_read_write_transactions(context):
    ops = context.ops
    ports = ops.env.config["database"]["ports"]
    _run_sql_parse_transactions(
        context, "rep_group", "replication",
        (ports["mmr1_standby1"], ports["mmr1"]), ports["mmr1"])


def _run_mmr_port_read_route(context):
    ops = context.ops
    ports = ops.env.config["database"]["ports"]
    readable = tuple(ports[key] for key in
                     ("mmr1", "mmr1_standby1", "mmr2_standby1"))
    _run_route_mode(
        context, "mmr_group", "mmr",
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "非 write_port 请求落到 MMR 可读候选",
        route_ports=readable,
        transform=_port_transform(context, "mmr_group"), port=ops.read_port,
    )


def _run_four_group_modes_route_visibility(context):
    ops = context.ops
    ops.start()
    ops.assert_groups(
        "查看四种 group_mode 的配置态",
        {"mmr_group": {"group_mode": "mmr"},
         "rep_group": {"group_mode": "replication"},
         "balance_group": {"group_mode": "balance"},
         "single_group": {"group_mode": "single"}})
    ops.assert_routing(
        "mmr_group", "查看 mmr_group 运行态路由",
        write_cluster="pg_cluster_2", write_leader="pg_2")
    ops.assert_routing(
        "rep_group", "查看 rep_group 运行态路由",
        write_cluster="pg_cluster_1", write_leader="pg_1")
    for group_name in ("balance_group", "single_group"):
        ops.assert_routing(group_name, "查看 %s 运行态路由候选" % group_name)


def _run_mmr_sql_parse_read_write_transactions(context):
    ops = context.ops
    ports = ops.env.config["database"]["ports"]
    _run_sql_parse_transactions(
        context, "mmr_group", "mmr",
        (ports["mmr1"], ports["mmr1_standby1"], ports["mmr2"], ports["mmr2_standby1"]),
        ports["mmr2"])


def _run_balance_read_only_route(context):
    ops = context.ops
    conf = ops.start(transform=_balance_read_only_transform(context))
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    ports = ops.env.config["database"]["ports"]
    pg5_port = ports["mmr1_standby2"]
    ops.assert_groups(
        "查看双 replica Balance read-only 初始配置",
        {"balance_read_only": {"group_mode": "balance",
                               "access_mode": "read_only",
                               "backend_clusters": "pg_cluster_1"}})
    ops.assert_members(
        "balance_read_only", "确认两个 replica 成员均 active",
        {"pg_3": {"state": "active", "weight": "10"},
         "pg_5": {"state": "active", "weight": "10"}},
        user="balance_reader", retry_timeout=30)
    ops.psql('SET NODE WEIGHT pg_3=0;',
            "将 pg_3 权重设为 0",
            "返回 SET NODE，pg_3 不再参与新业务连接选择",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    for attempt in range(1, 5):
        ops.assert_business_route(
            'SELECT inet_server_port(), pg_is_in_recovery();',
            "验证 weight=0 后第 %d 次 Balance 只读路由" % attempt,
            port=pg5_port, recovery=True,
            group="balance_read_only", user="balance_reader",
            retry_timeout=30)
    ops.psql('SET NODE PARTED pg_3,pg_5;',
            "隔离 Balance read-only 的全部 replica",
            "返回 SET NODE，两个 replica 均进入 parted",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_business_route(
        'SELECT inet_server_port(), pg_is_in_recovery();',
        "验证无 active replica 时 Balance read-only 回退 primary",
        port=ports["mmr1"], recovery=False,
        group="balance_read_only", user="balance_reader",
        retry_timeout=30)
    ops.psql('SET NODE ACTIVE pg_3,pg_5;',
            "恢复 Balance read-only 的全部 replica",
            "返回 SET NODE，两个 replica 均恢复 active",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    _wait_pg_cluster_ready(
        context, "pg_cluster_1", "pg_1", ("pg_3", "pg_5"),
        "等待 Balance replica ACTIVE 后 monitor 投影恢复")
    ops.assert_business_route(
        'SELECT inet_server_port(), pg_is_in_recovery();',
        "验证 replica ACTIVE 后 Balance read-only 路由恢复",
        port=pg5_port, recovery=True,
        group="balance_read_only", user="balance_reader",
        retry_timeout=30)
    ops.psql('SET NODE WEIGHT pg_3=10;',
            "恢复 pg_3 初始权重",
            "返回 SET NODE，配置恢复初始权重",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.diff(before, conf)


def _run_replication_route(context):
    ops = context.ops
    ports = ops.env.config["database"]["ports"]
    allowed = (ports["mmr1"], ports["mmr1_standby1"])
    _run_route_mode(
        context, "rep_group", "replication",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY; "
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "只读请求落到 replication group 的 pg_cluster_1 主备之一",
        route_ports=allowed,
        transform=_hint_transform("rep_group"),
    )


def _run_mmr_hint_read_route(context):
    ops = context.ops
    ports = ops.env.config["database"]["ports"]
    readable = tuple(ports[key] for key in
                     ("mmr1", "mmr1_standby1", "mmr2", "mmr2_standby1"))
    _run_route_mode(
        context, "mmr_group", "mmr",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY; "
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "只读 Hint 请求落到 MMR 合法读候选；无优先读节点时允许回退 write-leader",
        route_ports=readable,
        transform=_hint_transform("mmr_group"),
    )


