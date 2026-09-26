"""Streaming conflict document 2.1.1: delete_missing with skip."""

from suites.mmr.streaming_conflict_support import (
    DSN134, NODE134, NODE135, PORT134, PORT135, ROOT,
    psql, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    shared_non2pc_set_steps,
)


CASE = {
    "id": "mmr.streaming_conflict.delete_missing_skip",
    "name": "streaming delete_missing 的 skip 策略",
    "document": "多活streaming冲突处理测试文档.md", "section": "2.1.1",
    "group": "streaming_conflict", "report_setting_details": False,
    "fixtures": [
        "cluster",
        {"type": "shared_mmr_conflict_reset", "source_port": PORT134,
         "target_port": PORT135, "set_name": "set1",
         "tables": ["immediate_parallel_conflict"],
         "resolvers": {"delete_missing": "skip"}},
    ],
    "session": {
        "key": "mmr_streaming_conflict_non2pc",
        "order": 90,
        "fixtures": [{"type": "shared_mmr_conflict_topology", "data_dir": ROOT,
                      "source_port": PORT134, "target_port": PORT135}],
    },
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "test_topology": {"summary": "节点数=2；node134/node135 为对等可写 MMR 主节点；无物理备库、无独立普通逻辑复制。",
                      "nodes": [{"name": "node134", "role": "MMR primary", "host": "127.0.0.1", "port": PORT134, "data_dir": NODE134}, {"name": "node135", "role": "MMR primary", "host": "127.0.0.1", "port": PORT135, "data_dir": NODE135}],
                      "relations": ["MMR 多活: node134 <-> node135（双向复制）", "物理流复制: 无"]},
    "conflict_configuration": ["发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。", "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。", "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。", "本节 resolver：node135 的 delete_missing=skip。"],
    "prerequisites": ["严格按文档使用临时 two_phase 双节点，不触碰共享 mmr。", "512 行、每行 128 字节的 INSERT 超过 64kB，触发 streaming 大事务路径。"],
    "steps": shared_non2pc_set_steps() + [
        sql("将 node135 delete_missing 设为 skip", PORT135, "SELECT fdd.alter_local_node_set_conflict_resolver('delete_missing','skip')", "返回 node135,delete_missing,skip", "node135,delete_missing,skip", report_node="node135"),
        sql("确认触发前冲突记录为 0", PORT135, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='delete_missing' AND conflict_resolution='skip'", "返回 0", "0", report_node="node135"),
        sql("按文档在 node134 插入 512 行大事务", PORT134, "INSERT INTO immediate_parallel_conflict(id,name) SELECT gs,repeat('a',128) FROM generate_series(100001,100512) gs", "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
        wait_for_rows("等待 node135 收到 512 行", PORT135,
                      "SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN 100001 AND 100512",
                      "512", "node135"),
        sql("按文档删除缺失 id=100 和 512 行", PORT134, "BEGIN; DELETE FROM immediate_parallel_conflict WHERE id=100 OR id BETWEEN 100001 AND 100512; COMMIT", "返回 BEGIN、DELETE 513、COMMIT", "BEGIN", "DELETE 513", "COMMIT", report_node="node134"),
        wait_for_rows("确认 node135 删除结果和冲突记录", PORT135,
                      "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id=100 OR id BETWEEN 100001 AND 100512)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='delete_missing' AND conflict_resolution='skip')::text",
                      "0|1", "node135"),
    ],
    "teardown": "fixture 以 immediate 停止临时 node134/node135 并删除目录；临时复制集、订阅、复制槽和冲突记录一并清除。",
}
