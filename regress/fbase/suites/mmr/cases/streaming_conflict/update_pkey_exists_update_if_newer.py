"""Streaming conflict document 2.4.1: update_pkey_exists with update_if_newer."""

from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    shared_non2pc_set_steps,
)
from suites.mmr.streaming_conflict_support import non2pc_case_base


def build_non2pc_update_pkey_case(case_id, name, section, resolver, start,
                                  expected_history, note):
    """Build one independent 2.4 strategy around the shared concurrent DML."""
    case = non2pc_case_base(case_id, name, section,
        [
        "发布端/订阅端：debug_logical_replication_streaming 分别为 immediate/buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "本节 resolver：node135 的 update_pkey_exists=%s；%s" % (resolver, note),
        ])
    end = start + 511
    shifted_start = start + 1000000
    shifted_end = end + 1000000
    case["session"] = {
        "key": "mmr_streaming_conflict_non2pc",
        "order": {"update_if_newer": 110, "update": 120,
                  "skip_transaction": 130, "error": 140}[resolver],
        "fixtures": [{"type": "shared_mmr_conflict_topology", "data_dir":
                      "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}",
                      "source_port": PORT134, "target_port": PORT135}],
    }
    case["fixtures"] = [
        "cluster",
        {"type": "shared_mmr_conflict_reset", "source_port": PORT134,
         "target_port": PORT135, "set_name": "set1",
         "tables": ["immediate_parallel_conflict"],
         "resolvers": {"update_pkey_exists": "update_if_newer"}},
    ]
    case["steps"] = shared_non2pc_set_steps() + [
    sql("将 node135 update_pkey_exists 设为 %s" % resolver, PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('update_pkey_exists','%s')" % resolver,
        "返回 node135,update_pkey_exists,%s" % resolver,
        "node135,update_pkey_exists,%s" % resolver, report_node="node135"),
    sql("确认触发前 %s 冲突记录为 0" % resolver, PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_pkey_exists' AND conflict_resolution='%s'" % resolver,
        "返回 0", "0", report_node="node135"),
    sql("按文档在 node134 插入 512 行主键更新前置数据", PORT134,
        "INSERT INTO immediate_parallel_conflict(id,name) SELECT gs,repeat('a',128) FROM generate_series(%s,%s) gs" % (start, end),
        "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
    wait_for_rows("等待 node135 收到主键更新前置的 512 行", PORT135,
                  "SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN %s AND %s" % (start, end),
                  "512", "node135"),
    sql("按文档在所有节点同时将 512 个主键加 1000000", PORT134,
        "SELECT fdd.run_on_all_nodes('update immediate_parallel_conflict set id=id+1000000 where id between %s and %s')" % (start, end),
        "node134/node135 均返回 UPDATE 512", "node134", "node135", "UPDATE 512",
        report_node="node134"),
    wait_for_rows("确认 node135 旧主键清空、新主键为 512 行并记录预期冲突", PORT135,
                  "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN %s AND %s)::text || '|' || (SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN %s AND %s)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_pkey_exists' AND conflict_resolution='%s')::text" % (start, end, shifted_start, shifted_end, resolver),
                  "0|512|%s" % expected_history, "node135"),
    ]
    return case


CASE = build_non2pc_update_pkey_case(
    "mmr.streaming_conflict.update_pkey_exists_update_if_newer",
    "streaming update_pkey_exists 的 update_if_newer 策略", "2.4.1",
    "update_if_newer", 70001, "512", "两个节点同时更新 512 行主键。")
