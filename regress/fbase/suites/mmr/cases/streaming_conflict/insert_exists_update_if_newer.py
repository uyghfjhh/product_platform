"""Streaming conflict document 2.3.1: insert_exists with update_if_newer."""

from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    shared_non2pc_set_steps,
)
from suites.mmr.streaming_conflict_support import non2pc_case_base


def build_non2pc_insert_exists_case(case_id, name, section, resolver, conflict_id,
                                    range_start, range_end, expected_high_rows,
                                    expected_name, expected_history, note,
                                    known_issue=None):
    """Build a standalone 2.3 case while keeping its rendered SQL explicit."""
    case = non2pc_case_base(case_id, name, section, [
        "发布端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "订阅端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "本节 resolver：node134 的 insert_exists=%s；%s" % (resolver, note),
    ])
    if known_issue:
        case["known_issue"] = known_issue
    case["session"] = {
        "key": "mmr_streaming_conflict_non2pc",
        "order": {"update_if_newer": 50, "update": 60,
                  "skip_transaction": 70, "error": 80}[resolver],
        "fixtures": [{"type": "shared_mmr_conflict_topology", "data_dir":
                      "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}",
                      "source_port": PORT134, "target_port": PORT135}],
    }
    case["fixtures"] = [
        "cluster",
        {"type": "shared_mmr_conflict_reset", "source_port": PORT134,
         "target_port": PORT135, "set_name": "set1",
         "tables": ["immediate_parallel_conflict"],
         "resolvers": {"insert_exists": "update_if_newer"}},
    ]
    name_check = ("name='a'" if expected_name == "a"
                  else "name=repeat('b',128)")
    expected = "%s|1|%s" % (expected_high_rows, expected_history)
    case["steps"] = shared_non2pc_set_steps() + [
        sql("将 node134 insert_exists 设为 %s" % resolver, PORT134,
            "SELECT fdd.alter_local_node_set_conflict_resolver('insert_exists','%s')" % resolver,
            "返回 node134,insert_exists,%s" % resolver,
            "node134,insert_exists,%s" % resolver, report_node="node134"),
        sql("确认触发前 %s 冲突记录为 0" % resolver, PORT134,
            "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='insert_exists' AND conflict_resolution='%s'" % resolver,
            "返回 0", "0", report_node="node134"),
        sql("按文档在 node135 插入冲突 id=%s 和 512 行大事务" % conflict_id, PORT135,
            "BEGIN; INSERT INTO immediate_parallel_conflict(id,name) SELECT gs,repeat('b',128) FROM generate_series(%s,%s) gs WHERE gs=%s OR gs BETWEEN %s AND %s; COMMIT" %
            (conflict_id, range_end, conflict_id, range_start, range_end),
            "返回 BEGIN、INSERT 0 513、COMMIT", "BEGIN", "INSERT 0 513", "COMMIT",
            report_node="node135"),
        wait_for_rows("确认 node134 的数据结果和 %s 冲突记录" % resolver, PORT134,
                      "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN %s AND %s)::text || '|' || (SELECT count(*) FROM immediate_parallel_conflict WHERE id=%s AND %s)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='insert_exists' AND conflict_resolution='%s')::text" %
                      (range_start, range_end, conflict_id, name_check, resolver),
                      expected, "node134"),
    ]
    return case


CASE = build_non2pc_insert_exists_case(
    "mmr.streaming_conflict.insert_exists_update_if_newer",
    "streaming insert_exists 的 update_if_newer 策略", "2.3.1",
    "update_if_newer", 80, 80001, 80512, "512", "b", "1",
    "远端较新的 id=80 应覆盖本地值。")
