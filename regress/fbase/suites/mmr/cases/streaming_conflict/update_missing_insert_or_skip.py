"""Streaming conflict document 2.2.1: update_missing with insert_or_skip."""

from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.streaming_conflict_support import (copy_non2pc_common_steps,
                                                   non2pc_case_base)


CASE = non2pc_case_base(
    "mmr.streaming_conflict.update_missing_insert_or_skip",
    "streaming update_missing 的 insert_or_skip 策略", "2.2.1",
    [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "本节 resolver：node135 的 update_missing=insert_or_skip。",
    ])


def common_streaming_set_steps():
    """Return the named shared topology before any conflict-specific SQL."""
    return copy_non2pc_common_steps()


def common_update_missing_steps():
    """Return the standalone 2.2 common state, without a delete resolver."""
    return common_streaming_set_steps() + [
    sql("按文档在 node134 插入 update_missing 前置的 512 行", PORT134,
        "INSERT INTO immediate_parallel_conflict(id,name) SELECT gs,repeat('a',128) FROM generate_series(90001,90512) gs",
        "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
    wait_for_rows("等待 node135 收到 update_missing 前置的 512 行", PORT135,
                  "SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN 90001 AND 90512",
                  "512", "node135"),
    ]


def shared_non2pc_set_steps():
    """Build one case's table binding on top of the reusable session topology."""
    return [
        sql("按文档在 node134 创建测试表", PORT134,
            "CREATE TABLE immediate_parallel_conflict(id int PRIMARY KEY,name text)",
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node134"),
        sql("按文档在 node134 插入初始 100 行", PORT134,
            "INSERT INTO immediate_parallel_conflict VALUES (generate_series(1,100),'a')",
            "返回 INSERT 0 100", "INSERT 0 100", report_node="node134"),
        sql("按文档在 node135 创建同构空表", PORT135,
            "CREATE TABLE immediate_parallel_conflict(id int PRIMARY KEY,name text)",
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node135"),
        sql("按文档在 node135 将表加入复制集", PORT135,
            "SELECT fdd.replication_set_add_table('immediate_parallel_conflict'::regclass,'set1',false,false)",
            "复制集绑定成功", "replication_set_add_table", report_node="node135"),
        sql("按文档在所有节点订阅复制集", PORT134,
            "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{set1}'');')",
            "node134/node135 均成功", "node134", "node135", report_node="node134"),
        sql("按文档在 node135 异步执行复制集变更", PORT135,
            "SELECT fdd.replication_set_async_execute()", "异步生效",
            "replication_set_async_execute", report_node="node135"),
        sql("确认 node135 初始表为空", PORT135,
            "SELECT count(*) FROM immediate_parallel_conflict", "返回 0", "0",
            report_node="node135"),
    ]


def shared_update_missing_steps():
    """Reuse the session topology and build update_missing's data precondition."""
    return shared_non2pc_set_steps() + [
        sql("按文档在 node134 插入 update_missing 前置的 512 行", PORT134,
            "INSERT INTO immediate_parallel_conflict(id,name) SELECT gs,repeat('a',128) FROM generate_series(90001,90512) gs",
            "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
        wait_for_rows("等待 node135 收到 update_missing 前置的 512 行", PORT135,
                      "SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN 90001 AND 90512",
                      "512", "node135"),
    ]


CASE["session"] = {
    "key": "mmr_streaming_conflict_non2pc",
    "order": 10,
    "fixtures": [{"type": "shared_mmr_conflict_topology", "data_dir":
                  "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}",
                  "source_port": PORT134, "target_port": PORT135}],
}
CASE["fixtures"] = [
    "cluster",
    {"type": "shared_mmr_conflict_reset", "source_port": PORT134,
     "target_port": PORT135, "set_name": "set1",
     "tables": ["immediate_parallel_conflict"],
     "resolvers": {"update_missing": "skip"}},
]
CASE["steps"] = shared_update_missing_steps() + [
    sql("将 node135 update_missing 设为 insert_or_skip", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('update_missing','insert_or_skip')",
        "返回 node135,update_missing,insert_or_skip",
        "node135,update_missing,insert_or_skip", report_node="node135"),
    sql("确认触发前 insert_or_skip 冲突记录为 0", PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_missing' AND conflict_resolution='insert_or_skip'",
        "返回 0", "0", report_node="node135"),
    sql("按文档更新目标缺失 id=90 和 512 行", PORT134,
        "BEGIN; UPDATE immediate_parallel_conflict SET name=repeat('b',128) WHERE id=90 OR id BETWEEN 90001 AND 90512; COMMIT",
        "返回 BEGIN、UPDATE 513、COMMIT", "BEGIN", "UPDATE 513", "COMMIT",
        report_node="node134"),
    wait_for_rows("确认 node135 按 insert_or_skip 写入 id=90 并记录冲突", PORT135,
                  "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id=90 AND name=repeat('b',128))::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_missing' AND conflict_resolution='insert_or_skip')::text",
                  "1|1", "node135"),
]
