"""Executors for Chapter 10: fbasecman console commands and statistics."""

import re
import time
from suites.handover.runtime import HandoverFailure


def execute_console_group_metadata(rt):
    """10.1.1~10.1.5 控制台元数据命令输出字段全检"""
    rt.start()

    # 10.1.1 SHOW HELP
    _, out_help, _, _ = rt.console("SHOW HELP;", "10.1.1: 执行 SHOW HELP")
    required_help_keywords = [
        "SHOW HELP", "GROUPS", "GROUP_MEMBERS", "NODES", "NODE_STATUS",
        "DATASOURCES", "CLUSTERS", "GROUP_ROUTING", "NODE_MONITOR",
        "ENDPOINT_MONITOR", "POOLS", "SERVERS", "CLIENTS",
        "THREAD_STATUS", "SHOW LICENSE", "SPLITLOG", "RELOAD", "RESET",
    ]
    missing_help = [cmd for cmd in required_help_keywords if cmd not in out_help]
    rt.check("SHOW HELP 帮助完整", "包含所有控制台支持命令", "核对通过" if not missing_help else ("缺失: " + ", ".join(missing_help)), not missing_help)

    # 10.1.2 SHOW GROUPS
    expected_groups = {
        "postgres": {
            "group_mode": "mmr",
            "storage_db": "postgres",
            "backend_clusters": "mmr_cluster_1,mmr_cluster_2",
            "write_cluster": "mmr_cluster_1",
            "promoted_cluster": "mmr_cluster_2",
            "config_check": "auto",
        }
    }
    rt.assert_console_table("SHOW GROUPS;", expected_groups, title="10.1.2: 验证 SHOW GROUPS 输出字段", key="group_name")

    # 10.1.3 SHOW GROUP_MEMBERS
    expected_members = {
        "pg_220": {"group_name": "postgres", "group_mode": "mmr", "group_role": "write-leader", "state": "active"},
        "pg_240": {"group_name": "postgres", "group_mode": "mmr", "group_role": "replica", "primary": "pg_220", "state": "active"},
        "pg_230": {"group_name": "postgres", "group_mode": "mmr", "group_role": "non-write-leader", "state": "active"},
        "pg_250": {"group_name": "postgres", "group_mode": "mmr", "group_role": "replica", "primary": "pg_230", "state": "active"},
    }
    rt.assert_console_table("SHOW GROUP_MEMBERS;", expected_members, title="10.1.3: 验证 SHOW GROUP_MEMBERS 成员与角色", key="node_name")

    # 10.1.4 SHOW NODES
    expected_nodes = {
        "pg_220": {"cluster_name": "mmr_cluster_1", "effective_role": "PRIMARY", "effective_status": "WRITE_ONLY"},
        "pg_240": {"cluster_name": "mmr_cluster_1", "effective_role": "REPLICA", "effective_status": "READ_ONLY"},
        "pg_230": {"cluster_name": "mmr_cluster_2", "effective_role": "PRIMARY", "effective_status": "READ_ONLY"},
        "pg_250": {"cluster_name": "mmr_cluster_2", "effective_role": "REPLICA", "effective_status": "READ_ONLY"},
    }
    rt.assert_console_table("SHOW NODES;", expected_nodes, title="10.1.4: 验证 SHOW NODES 节点与有效状态", key="node_name")

    # 10.1.5 SHOW NODE_STATUS
    expected_node_status = {
        "pg_220": {"group_role": "write-leader", "state": "active", "is_abnormal": "OK", "detail": "OK"},
        "pg_240": {"group_role": "replica", "primary": "pg_220", "state": "active", "is_abnormal": "OK"},
        "pg_230": {"group_role": "non-write-leader", "state": "active", "is_abnormal": "OK", "detail": "OK"},
        "pg_250": {"group_role": "replica", "primary": "pg_230", "state": "active", "is_abnormal": "OK"},
    }
    rt.assert_console_table("SHOW NODE_STATUS;", expected_node_status, title="10.1.5: 验证 SHOW NODE_STATUS 运行态与健康指标", key="node_name")


def execute_console_server_maintenance(rt):
    """10.1.6~10.1.9 SPLITLOG 与 server 清理命令"""
    rt.start()

    # 10.1.6 SPLITLOG
    _, out_split, _, _ = rt.console("SPLITLOG;", "10.1.6: 执行 SPLITLOG 切换日志")
    rt.check("SPLITLOG 成功", "包含 SPLITLOG 关键字", out_split.strip(), "SPLITLOG" in out_split)

    # 产生业务请求建立后端 server
    rt.psql("SELECT 1;", title="产生业务查询以建立后端 server")
    _, out_servers, _, _ = rt.console("SHOW SERVERS;", "查询当前存在的后端 server")
    
    # 提取 server ptr
    match = re.search(r"\b(s[0-9a-fA-F]{12,16})\b", out_servers)
    if not match:
        # 兜底匹配普通十六进制或 ptr 列
        match = re.search(r"\b(0x[0-9a-fA-F]+)\b", out_servers)
    server_ptr = match.group(1) if match else None
    rt.check("成功捕获后端 server 标识符", "找到 server ptr", server_ptr or "<未找到>", bool(server_ptr))

    # 10.1.8 DROP SERVER <ptr>
    if server_ptr:
        rt.console("DROP SERVER %s;" % server_ptr, "10.1.8: 执行 DROP SERVER %s" % server_ptr)
        # 验证 SHOW SERVERS 字段变化: offline 变为 true, offline_reason 变为 MANUAL_DROP
        _, out_after_drop, _, _ = rt.console("SHOW SERVERS;", "验证 DROP SERVER 后 server 状态标记")
        expected_dropped = {
            server_ptr: {"offline": "true", "offline_reason": "MANUAL_DROP"}
        }
        rt.assert_console_table("SHOW SERVERS;", expected_dropped, title="验证 server 标记为离线与 MANUAL_DROP", key="ptr")

    # 10.1.9 DROP UNUSE_SERVERS 清理离线 server
    rt.console("DROP UNUSE_SERVERS;", "10.1.9: 执行 DROP UNUSE_SERVERS 清理离线连接")
    _, out_after_unuse, _, _ = rt.console("SHOW SERVERS;", "验证 DROP UNUSE_SERVERS 后离线 server 已被销毁")
    rt.check("离线 server 已彻底清理", "SHOW SERVERS 中不再包含 %s" % server_ptr,
             "已清理" if server_ptr not in out_after_unuse else "仍存在",
             server_ptr not in out_after_unuse)

    # 10.1.7 DROP SERVERS 清理所有 server
    # 再次产生业务查询建立 server
    rt.psql("SELECT 2;", title="再次产生业务查询以生成活跃 server")
    rt.console("DROP SERVERS;", "10.1.7: 执行 DROP SERVERS 清理全部后端连接")
    _, out_after_drop_all, _, _ = rt.console("SHOW SERVERS;", "验证 DROP SERVERS 后连接池清空")
    if "(0 rows)" not in out_after_drop_all:
        # 如有残留已离线连接，执行 DROP UNUSE_SERVERS 清理
        rt.console("DROP UNUSE_SERVERS;", "清理残留离线连接")
        _, out_after_drop_all, _, _ = rt.console("SHOW SERVERS;", "再次确认连接池已清空")
    rt.check("DROP SERVERS 清空全部 server", "SHOW SERVERS 返回 (0 rows)", out_after_drop_all.strip(), "(0 rows)" in out_after_drop_all)


def execute_console_statistics(rt):
    """10.2.1 SHOW SERVERS 统计信息验证"""
    rt.start()

    # 初始状态下查看 SERVERS 统计
    _, out_before, _, _ = rt.console("SHOW SERVERS;", "10.2.1: 查看业务请求前的 SHOW SERVERS")

    # 执行一批读写操作
    rt.psql("SELECT 1;", title="执行读业务请求")
    rt.psql("DROP TABLE IF EXISTS test_stats; CREATE TABLE test_stats(id int); INSERT INTO test_stats VALUES (1);", title="执行写业务请求")

    # 再次查看 SHOW SERVERS
    _, out_after, _, _ = rt.console("SHOW SERVERS;", "10.2.1: 查看业务请求后的 SHOW SERVERS")
    # 核对 10.2.1 文档明确要求的新增统计字段存在
    required_cols = [
        "create_time", "last_attach_time", "last_detach_time",
        "total_attach_time", "sent_bytes", "received_bytes",
        "read_request", "write_request"
    ]
    for col in required_cols:
        rt.check("SHOW SERVERS 包含统计字段: %s" % col, "包含字段 %s" % col, col if col in out_after else "<缺失>", col in out_after)

    # 进一步解析表格，核对读写计数已真实产生
    from framework.clients.psql import parse_psql_table
    rows = parse_psql_table(out_after)
    has_write = any(int(r.get("write_request", 0) or 0) > 0 for r in rows)
    rt.check("SHOW SERVERS 记录写请求统计", "write_request > 0", "写请求已记录" if has_write else "写请求为0", has_write)


def execute_console_thread_pool_statistics(rt):
    """10.2.3 SHOW THREAD_STATUS 与 10.2.4 SHOW POOLS 统计信息"""
    rt.start()

    # 10.2.3 SHOW THREAD_STATUS
    _, out_threads, _, _ = rt.console("SHOW THREAD_STATUS;", "10.2.3: 执行 SHOW THREAD_STATUS")
    required_thread_cols = ["thread_id", "cl_active", "sv_active", "read_ratio", "write_ratio", "queries_per_sec"]
    rt.check("SHOW THREAD_STATUS 包含核心指标",
             "包含 %s" % ", ".join(required_thread_cols),
             out_threads,
             all(col in out_threads for col in required_thread_cols))

    # 10.2.4 SHOW POOLS
    _, out_pools, _, _ = rt.console("SHOW POOLS;", "10.2.4: 执行 SHOW POOLS")
    required_pool_cols = ["node_name", "group_name", "database", "user", "pool_mode", "cl_active", "sv_active", "sv_idle", "total_requests", "request_per_sec"]
    rt.check("SHOW POOLS 包含连接池指标",
             "包含 %s" % ", ".join(required_pool_cols),
             out_pools,
             all(col in out_pools for col in required_pool_cols))


def execute_console_reset_statistics(rt):
    """10.2.5 RESET 统计重置验证"""
    rt.start()

    # 产生业务流量
    rt.psql("SELECT 1;", title="产生业务流量")

    # 执行 RESET 各类型
    reset_commands = [
        ("RESET ALL;", "重置所有统计"),
        ("RESET TIME;", "重置时间统计"),
        ("RESET REQUEST;", "重置请求统计"),
        ("RESET BYTE;", "重置字节统计"),
        ("RESET CONNECT;", "重置连接统计"),
    ]
    for sql, desc in reset_commands:
        _, out, _, _ = rt.console(sql, "10.2.5: 执行 %s (%s)" % (sql, desc))
        rt.check("执行 %s 成功" % sql, "返回 RESET", out.strip(), "RESET" in out)
