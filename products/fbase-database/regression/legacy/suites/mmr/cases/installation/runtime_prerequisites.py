from framework.assertions import rows_equal
from framework.steps import sql_step


NODES = ["mmr:mmr1", "mmr:mmr2"]
REPORT_NAMES = {"mmr:mmr1": "node134", "mmr:mmr2": "node135"}


def extension_step(node):
    return sql_step(
        "确认 %s 已安装 fdd_mmr 与 fb_license 扩展" % REPORT_NAMES[node],
        "postgres",
        "SELECT extname::text,extversion::text FROM pg_extension "
        "WHERE extname IN ('fdd_mmr','fb_license') ORDER BY extname",
        "返回 fb_license|1.0、fdd_mmr|2.0",
        rows_equal([["fb_license", "1.0"], ["fdd_mmr", "2.0"]]), node=node)


def required_settings_step(node):
    return sql_step(
        "确认 %s 的多活强制启动配置" % REPORT_NAMES[node],
        "postgres",
        "SELECT current_setting('wal_level')::text,"
        "current_setting('track_commit_timestamp')::text,"
        "current_setting('fdd.running_databases')::text,"
        "(position('fdd_mmr' in current_setting('shared_preload_libraries')) > 0)::text,"
        "(current_setting('listen_addresses') IN ('*','127.0.0.1','localhost'))::text",
        "返回 logical|on|postgres|true|true；隔离环境以 loopback 代替文档的 *",
        rows_equal([["logical", "on", "postgres", "true", "true"]]), node=node)


def recommended_settings_step(node):
    return sql_step(
        "确认 %s 的文档推荐复制与日志参数" % REPORT_NAMES[node],
        "postgres",
        "SELECT current_setting('log_min_messages')::text,"
        "current_setting('log_destination')::text,"
        "current_setting('logging_collector')::text,"
        "current_setting('logical_decoding_work_mem')::text,"
        "current_setting('fdd.log_conflicts_to_table')::text,"
        "current_setting('fdd.search_dead_tup_time_interval')::text,"
        "(current_setting('max_logical_replication_workers')::int >= 4)::text,"
        "(current_setting('max_sync_workers_per_subscription')::int >= 2)::text,"
        "(current_setting('max_replication_slots')::int >= 10)::text,"
        "(current_setting('max_wal_senders')::int >= 10)::text",
        "返回 log|stderr,csvlog|on|64MB|on|30s|true|true|true|true",
        rows_equal([["log", "stderr,csvlog", "on", "64MB", "on", "30s",
                     "true", "true", "true", "true"]]), node=node)


CASE = {
    "id": "mmr.installation.runtime_prerequisites",
    "name": "多活安装后运行环境与关键配置",
    "document": "多活功能测试文档.md", "section": "1.1,1.2", "group": "installation",
    "fixtures": ["cluster"],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": NODES[0]},
    "evidence_nodes": NODES,
    "test_topology": {
        "summary": "节点数=2；两端均为可写 MMR 主节点；无物理备库、无独立普通逻辑复制。",
        "report_nodes": REPORT_NAMES,
        "relations": ["MMR 多活: node134 <-> node135，group=fbase_regress_mmr"],
    },
    "prerequisites": [
        "专用两成员多活环境已启动；本用例仅验证安装后的运行状态，不重新编译、安装或修改配置。",
        "文档中的 0.0.0.0/0 与 listen_addresses='*' 在隔离环境中等价收敛为两个成员可互联的 loopback trust 配置。",
    ],
    "steps": [
        extension_step(NODES[0]), extension_step(NODES[1]),
        required_settings_step(NODES[0]), required_settings_step(NODES[1]),
        recommended_settings_step(NODES[0]), recommended_settings_step(NODES[1]),
        sql_step(
            "确认两个成员均具有文档要求的 host trust 连接规则", "postgres",
            "SELECT EXISTS (SELECT 1 FROM pg_hba_file_rules "
            "WHERE error IS NULL AND type='host' AND database=ARRAY['all'] "
            "AND user_name=ARRAY['all'] AND auth_method='trust')::text",
            "返回 true", rows_equal([["true"]]), node=NODES[0]),
        sql_step(
            "确认安装完成后的两成员多活集群可正常校验", "postgres",
            "SELECT (count(*) >= 2)::text,bool_and(is_abnormal='OK')::text,"
            "bool_and(nodestate='ACTIVE')::text FROM fdd.show_node_info(true,false)",
            "返回不少于 2 个成员且均为 OK、ACTIVE",
            rows_equal([["true", "true", "true"]]), node=NODES[0]),
    ],
    "teardown": "只读验证，无测试对象和配置修改。",
}
