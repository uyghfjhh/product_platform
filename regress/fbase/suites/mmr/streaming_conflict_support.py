"""Stable building blocks for streaming-conflict document cases.

This module is deliberately outside ``suites.mmr.cases``: every module below
that package is discovered as a test case and must export ``CASE``.
"""

from copy import deepcopy

from framework.assertions import command_succeeds, output_contains_text
from framework.steps import command_step
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql,
                                                            shell)


ROOT = "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}"
NODE134, NODE135 = ROOT + "/node134", ROOT + "/node135"
PORT134, PORT135 = "15651", "15652"
DSN134 = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % PORT134
SET_NAME = "set1"
TABLE = "immediate_parallel_conflict"


def setup(step):
    step["report"] = False
    return step


def sql(title, port, statement, expected, *markers, **options):
    step = shell(
        title,
        "%s -X -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
        (PSQL, port, statement), expected,
        output_contains_text(*(markers or (expected,))),
        timeout=options.get("timeout", 30))
    step["display_sql"] = statement
    step["report_node"] = options.get("report_node")
    return step


def wait_for_rows(title, port, statement, expected, report_node, timeout=45):
    """Poll asynchronous replication while retaining the final psql evidence."""
    script = (
        "last=''; last_status=0; for i in $(seq 1 %s); do "
        "v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r 2>&1); "
        "last_status=$?; last=$v; test \"$last_status\" = 0 && test \"$v\" = %r "
        "&& { echo \"$v\"; exit 0; }; sleep 1; done; "
        "printf '最后一次 psql 退出码=%%s，输出=%%s\\n' \"$last_status\" \"$last\"; exit 1" %
        (timeout, PSQL, port, statement, expected))
    step = command_step(title, ["sh", "-ec", script],
                        "%s 秒内返回 %s" % (timeout, expected),
                        command_succeeds(), node="mmr:mmr1", timeout=timeout + 5,
                        display_sql=statement, report_node=report_node)
    step["report_database"] = "postgres"
    return step


def non2pc_case_base(case_id, name, section, conflict_configuration):
    """Return common case metadata without borrowing another case's steps."""
    return {
        "id": case_id, "name": name,
        "document": "多活streaming冲突处理测试文档.md", "section": section,
        "group": "streaming_conflict", "report_setting_details": False,
        "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
        "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                         "writable_node": True, "node": "mmr:mmr1"},
        "evidence_nodes": ["mmr:mmr1"],
        "test_topology": {
            "summary": "节点数=2；node134/node135 为对等可写 MMR 主节点；无物理备库、无独立普通逻辑复制。",
            "nodes": [
                {"name": "node134", "role": "MMR primary", "host": "127.0.0.1",
                 "port": PORT134, "data_dir": NODE134},
                {"name": "node135", "role": "MMR primary", "host": "127.0.0.1",
                 "port": PORT135, "data_dir": NODE135},
            ],
            "relations": ["MMR 多活: node134 <-> node135（双向复制）", "物理流复制: 无"],
        },
        "conflict_configuration": conflict_configuration,
        "prerequisites": ["严格按文档使用临时 two_phase 双节点，不触碰共享 mmr。",
                          "512 行、每行 128 字节的 INSERT 超过 64kB，触发 streaming 大事务路径。"],
        "teardown": "fixture 以 immediate 停止临时 node134/node135 并删除目录；临时复制集、订阅、复制槽和冲突记录一并清除。",
    }


def non2pc_common_steps():
    """Build the documented two-node state through node135's empty table."""
    return [
        setup(init_instance("初始化 node134", NODE134, PORT134)),
        setup(shell("设置 node134 文档 GUC", psql(PORT134, "ALTER SYSTEM SET debug_logical_replication_streaming='immediate'") + "; " + psql(PORT134, "ALTER SYSTEM SET logical_decoding_work_mem='64kB'") + "; " + psql(PORT134, "SELECT pg_reload_conf()"), "参数设置成功")),
        setup(create_node(PORT134, "node134", "parallel", False)),
        setup(shell("创建建组所需默认复制集测试表", psql(PORT134, "CREATE TABLE public.streaming_join_probe(id int PRIMARY KEY)"), "返回 CREATE TABLE")),
        setup(shell("在 node134 创建 g1", psql(PORT134, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 node135", NODE135, PORT135)),
        setup(shell("设置 node135 文档 GUC", psql(PORT135, "ALTER SYSTEM SET debug_logical_replication_streaming='buffered'") + "; " + psql(PORT135, "ALTER SYSTEM SET logical_decoding_work_mem='64kB'") + "; " + psql(PORT135, "SELECT pg_reload_conf()"), "参数设置成功")),
        setup(create_node(PORT135, "node135", "parallel", True)),
        setup(shell("以 all 模式将 node135 加入 g1", psql(PORT135, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN134), "返回 node join to group complete finished", timeout=90)),
        sql("确认 node134 streaming 配置", PORT134, "SHOW debug_logical_replication_streaming", "返回 immediate", "immediate", report_node="node134"),
        sql("确认 node135 streaming 配置", PORT135, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered", report_node="node135"),
        sql("确认两端 logical_decoding_work_mem", PORT134, "SHOW logical_decoding_work_mem", "返回 64kB", "64kB", report_node="node134"),
        sql("确认 node135 logical_decoding_work_mem", PORT135, "SHOW logical_decoding_work_mem", "返回 64kB", "64kB", report_node="node135"),
        sql("确认两端 MMR streaming 和 two_phase", PORT134, "SELECT string_agg(node_name || '|' || CASE streaming WHEN 'p' THEN 'parallel' END || '|' || two_phase::text, ';' ORDER BY node_name) FROM fdd.mmr_node", "返回 node134|parallel|false；node135|parallel|true", "node134|parallel|false;node135|parallel|true", report_node="node134"),
        sql("按文档创建私有复制集", PORT134, "SELECT fdd.create_replication_set('set1',true,true,true,true,false,false,false)", "创建成功", "create_replication_set", report_node="node134"),
        sql("按文档在 node134 创建测试表", PORT134, "CREATE TABLE immediate_parallel_conflict(id int PRIMARY KEY,name text)", "返回 CREATE TABLE", "CREATE TABLE", report_node="node134"),
        sql("按文档在 node134 插入初始 100 行", PORT134, "INSERT INTO immediate_parallel_conflict VALUES (generate_series(1,100),'a')", "返回 INSERT 0 100", "INSERT 0 100", report_node="node134"),
        sql("按文档确认 node134 初始数据为 100 行", PORT134, "SELECT count(*) FROM immediate_parallel_conflict", "返回 100", "100", report_node="node134"),
        sql("按文档在 node135 创建同构空表", PORT135, "CREATE TABLE immediate_parallel_conflict(id int PRIMARY KEY,name text)", "返回 CREATE TABLE", "CREATE TABLE", report_node="node135"),
        sql("按文档在 node135 将表加入复制集", PORT135, "SELECT fdd.replication_set_add_table('immediate_parallel_conflict'::regclass,'set1',false,false)", "复制集绑定成功", "replication_set_add_table", report_node="node135"),
        sql("按文档在所有节点订阅复制集", PORT134, "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{set1}'');')", "node134/node135 均成功", "node134", "node135", report_node="node134"),
        sql("按文档在 node135 异步执行复制集变更", PORT135, "SELECT fdd.replication_set_async_execute()", "异步生效", "replication_set_async_execute", report_node="node135"),
        sql("确认 node135 初始表为空", PORT135, "SELECT count(*) FROM immediate_parallel_conflict", "返回 0", "0", report_node="node135"),
    ]


def copy_non2pc_common_steps():
    """Give callers isolated mutable step dictionaries."""
    return deepcopy(non2pc_common_steps())
