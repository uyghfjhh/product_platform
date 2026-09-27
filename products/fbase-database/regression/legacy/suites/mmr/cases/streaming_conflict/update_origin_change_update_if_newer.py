"""Streaming conflict document 2.11.1: update_origin_change/update_if_newer."""

from copy import deepcopy

from suites.mmr.cases.node_management.create_group import PSQL
from suites.mmr.cases.conflict.update_origin_change import (
    CASE as BASE_CASE, OBSERVER_PORT, PEER_PORT, SOURCE_PORT,
)
from suites.mmr.cases.streaming_conflict.delete_missing_skip import psql, sql, wait_for_rows
from suites.mmr.cases.streaming_conflict.delete_recently_updated_skip import concurrent_sql


TABLE = "immediate_parallel_conflict"


def ordered_origin_change(title, start, end):
    """Follow mmr-autotest: source commit propagates before observer update."""
    source_update = "UPDATE %s SET name=repeat('c',128) WHERE id BETWEEN %s AND %s" % (TABLE, start, end)
    observer_update = "UPDATE %s SET name=repeat('d',128) WHERE id BETWEEN %s AND %s" % (TABLE, start, end)
    script = (
        "%s; for attempt in $(seq 1 45); do "
        "v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); "
        "test \"$v\" = 512 && break; sleep 1; done; "
        "test \"$v\" = 512 || { echo 'node134 update did not reach node135'; exit 1; }; %s" %
        (psql(SOURCE_PORT, source_update), PSQL, PEER_PORT,
         "SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s AND name=repeat('c',128)" % (TABLE, start, end),
         psql(OBSERVER_PORT, observer_update)))
    return concurrent_sql(
        title, script, "node134 UPDATE 提交并到达 node135 后，node136 UPDATE 成功提交",
        "-- node134，先提交并等待复制至 node135\n%s;\n-- node136，随后更新\n%s;" %
        (source_update, observer_update))


def _parallel_topology_steps():
    """Use the existing isolated three-node lifecycle with document node modes."""
    steps = deepcopy(BASE_CASE["steps"][:10])
    for step in steps:
        argv = step.get("argv")
        if argv:
            argv[-1] = argv[-1].replace("'off',false", "'parallel',false")
            argv[-1] = argv[-1].replace("origin_change_{run_id}", TABLE)
            argv[-1] = argv[-1].replace("id int PRIMARY KEY,title text", "id int PRIMARY KEY,name text")
    return steps


def _streaming_guc_steps(node, port, mode):
    return [
        sql("设置 %s debug_logical_replication_streaming" % node, port,
            "ALTER SYSTEM SET debug_logical_replication_streaming='%s'" % mode,
            "返回 ALTER SYSTEM", "ALTER SYSTEM", report_node=node),
        sql("设置 %s logical_decoding_work_mem" % node, port,
            "ALTER SYSTEM SET logical_decoding_work_mem='64kB'",
            "返回 ALTER SYSTEM", "ALTER SYSTEM", report_node=node),
        sql("重新加载 %s 参数" % node, port, "SELECT pg_reload_conf()",
            "返回 true", "t", report_node=node),
    ]


def _shared_origin_reset_steps():
    steps = []
    for node, port in (("node134", SOURCE_PORT), ("node135", PEER_PORT),
                       ("node136", OBSERVER_PORT)):
        steps.extend([
            sql("清空 %s 上一条用例的测试数据" % node, port,
                "TRUNCATE immediate_parallel_conflict", "返回 TRUNCATE TABLE",
                "TRUNCATE TABLE", report_node=node),
            sql("清空 %s 上一条用例的冲突历史" % node, port,
                "TRUNCATE fdd.mmr_conflict_history", "返回 TRUNCATE TABLE",
                "TRUNCATE TABLE", report_node=node),
        ])
    reset = sql("恢复 node135 update_origin_change 默认策略", PEER_PORT,
                "SELECT fdd.alter_local_node_set_conflict_resolver('update_origin_change','update_if_newer')",
                "返回 node135,update_origin_change,update_if_newer",
                "node135,update_origin_change,update_if_newer", report_node="node135")
    reset["session_reset"] = True
    steps.append(reset)
    return steps


CASE = deepcopy(BASE_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.update_origin_change_update_if_newer",
    "name": "streaming update_origin_change 的 update_if_newer 策略",
    "document": "多活streaming冲突处理测试文档.md",
    "section": "2.11.1",
    "group": "streaming_conflict",
    "conflict_configuration": [
        "node134/node135/node136 均为 parallel MMR 节点；node135 是冲突观察端。",
        "node134=immediate、node135=buffered，两个节点 logical_decoding_work_mem=64kB。",
        "node135 的 update_origin_change=update_if_newer。",
    ],
})
CASE["session"] = {
    "key": "mmr_streaming_conflict_origin",
    "order": 250,
    "fixtures": [{"type": "isolated_mmr_node_creation",
                  "data_dir": "/tmp/fbase_regress_mmr_conflict_origin_{run_id}"}],
    "steps": _parallel_topology_steps(),
}
CASE["fixtures"] = ["cluster"]
CASE["steps"] = _shared_origin_reset_steps() + [
    *_streaming_guc_steps("node134", SOURCE_PORT, "immediate"),
    *_streaming_guc_steps("node135", PEER_PORT, "buffered"),
    sql("确认 node134 streaming 参数", SOURCE_PORT,
        "SHOW debug_logical_replication_streaming", "返回 immediate", "immediate", report_node="node134"),
    sql("确认 node135 streaming 参数", PEER_PORT,
        "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered", report_node="node135"),
    sql("确认所有节点订阅默认复制集 g1", SOURCE_PORT,
        "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{g1}'');')",
        "node134/node135 均成功", "node134", "node135", report_node="node134"),
    sql("在 node136 异步执行默认复制集变更", OBSERVER_PORT,
        "SELECT fdd.replication_set_async_execute()", "异步生效", "replication_set_async_execute", report_node="node136"),
    sql("将 node135 update_origin_change 设为 update_if_newer", PEER_PORT,
        "SELECT fdd.alter_local_node_set_conflict_resolver('update_origin_change','update_if_newer')",
        "返回 node135,update_origin_change,update_if_newer", "node135,update_origin_change,update_if_newer", report_node="node135"),
    sql("确认触发前 update_if_newer 历史为 0", PEER_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change' AND conflict_resolution='update_if_newer'" % TABLE,
        "返回 0", "0", report_node="node135"),
    sql("按文档在 node134 插入 512 行前置数据", SOURCE_PORT,
        "INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(20001,20512) gs" % TABLE,
        "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
    wait_for_rows("等待 node135 收到 512 行", PEER_PORT,
        "SELECT count(*) FROM %s WHERE id BETWEEN 20001 AND 20512" % TABLE, "512", "node135"),
    wait_for_rows("等待 node136 收到 512 行", OBSERVER_PORT,
        "SELECT count(*) FROM %s WHERE id BETWEEN 20001 AND 20512" % TABLE, "512", "node136"),
    ordered_origin_change("按 mmr-autotest 时序执行 node134/node136 的 512 行更新", 20001, 20512),
    wait_for_rows("确认 node135 记录 512 条 update_if_newer 冲突", PEER_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change' AND conflict_resolution='update_if_newer'" % TABLE,
        "512", "node135"),
]
