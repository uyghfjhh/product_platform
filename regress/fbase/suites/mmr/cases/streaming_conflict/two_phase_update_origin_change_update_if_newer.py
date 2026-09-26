"""Streaming conflict document 3.11.1: 2PC update_origin_change/update_if_newer."""

from copy import deepcopy

from framework.assertions import output_contains_text
from suites.mmr.cases.node_management.create_group import create_node, init_instance, psql, shell
from suites.mmr.cases.streaming_conflict.delete_missing_skip import PORT134, PORT135, sql, wait_for_rows
from suites.mmr.streaming_conflict_support import NODE134, NODE135, ROOT, non2pc_case_base
from suites.mmr.two_phase_conflict_support import (
    TABLE, parallel_prepared_origin_change, two_phase_streaming_set_steps,
)

NODE136 = ROOT + "/node136"
PORT136 = "15653"


def _node136_steps():
    """Join the third node exactly as the reference three-node conflict test does."""
    dsn134 = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % PORT134
    steps = [
        init_instance("初始化 node136", NODE136, PORT136),
        create_node(PORT136, "node136", "parallel", True),
        shell("以 schema-only 模式将 node136 加入 g1", psql(PORT136,
              "SELECT fdd.join_group('g1','%s',true,'schema-only','table_exist_error')" % dsn134),
              "返回 node join to group complete finished", timeout=90),
    ]
    for step in steps:
        step["report"] = False
    return steps


def _case():
    case = non2pc_case_base(
        "mmr.streaming_conflict.two_phase_update_origin_change_update_if_newer",
        "2PC streaming update_origin_change 的 update_if_newer 策略", "3.11.1", [
            "node134=parallel,two_phase=false；node135/node136=parallel,two_phase=true。",
            "node134=immediate、node135=buffered，logical_decoding_work_mem=64kB。",
            "node135 的 update_origin_change=update_if_newer；node134/node136 按文档顺序并发预备。",
        ])
    case["steps"] = two_phase_streaming_set_steps() + [
        sql("按文档在 node134 创建 2PC 测试表", PORT134,
            "CREATE TABLE %s(id int PRIMARY KEY,name text)" % TABLE,
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node134"),
        sql("按文档在 node135 创建同构空 2PC 表", PORT135,
            "CREATE TABLE %s(id int PRIMARY KEY,name text)" % TABLE,
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node135"),
        sql("按文档在 node135 将 2PC 表加入私有 set1", PORT135,
            "SELECT fdd.replication_set_add_table('%s'::regclass,'set1',false,false)" % TABLE,
            "复制集绑定成功", "replication_set_add_table", report_node="node135"),
        sql("按文档在 node134/node135 订阅私有 set1", PORT134,
            "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{set1}'');')",
            "node134/node135 均成功", "node134", "node135", report_node="node134"),
        sql("按文档在 node135 异步执行私有 set1 变更", PORT135,
            "SELECT fdd.replication_set_async_execute(true)", "异步生效",
            "replication_set_async_execute", report_node="node135"),
        *_node136_steps(),
        sql("确认 node136 已由 schema-only join 得到 2PC 表结构", PORT136,
            "SELECT to_regclass('public.%s')" % TABLE,
            "返回 %s" % TABLE, TABLE, report_node="node136"),
        sql("将 node135 update_origin_change 设为 update_if_newer", PORT135,
            "SELECT fdd.alter_local_node_set_conflict_resolver('update_origin_change','update_if_newer')",
            "返回 node135,update_origin_change,update_if_newer",
            "node135,update_origin_change,update_if_newer", report_node="node135"),
        sql("确认触发前 update_if_newer 历史为 0", PORT135,
            "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change' AND conflict_resolution='update_if_newer'" % TABLE,
            "返回 0", "0", report_node="node135"),
        sql("按文档在 node134 插入 512 行前置数据", PORT134,
            "INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(20001,20512) gs" % TABLE,
            "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
        wait_for_rows("等待 node135 收到 512 行", PORT135,
            "SELECT count(*) FROM %s WHERE id BETWEEN 20001 AND 20512" % TABLE, "512", "node135"),
        wait_for_rows("等待 node136 收到 512 行", PORT136,
            "SELECT count(*) FROM %s WHERE id BETWEEN 20001 AND 20512" % TABLE, "512", "node136"),
        parallel_prepared_origin_change("按文档并发准备并回滚两端更新", PORT134, PORT136,
            TABLE, 20001, "stream_20", "ROLLBACK"),
        wait_for_rows("确认 rollback 后 node135 无 update_if_newer 历史", PORT135,
            "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change' AND conflict_resolution='update_if_newer'" % TABLE,
            "0", "node135"),
        parallel_prepared_origin_change("按文档并发准备并提交两端更新", PORT134, PORT136,
            TABLE, 20001, "stream_20", "COMMIT"),
        wait_for_rows("确认 commit 后 node135 记录 512 条 update_if_newer 历史", PORT135,
            "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change' AND conflict_resolution='update_if_newer'" % TABLE,
            "512", "node135"),
    ]
    case["test_topology"] = {
        "summary": "节点数=3；node134/node135/node136 为对等可写 MMR 主节点；无物理备库、无独立普通逻辑复制。",
        "nodes": [
            {"name": "node134", "role": "MMR primary", "host": "127.0.0.1", "port": PORT134, "data_dir": NODE134},
            {"name": "node135", "role": "MMR primary", "host": "127.0.0.1", "port": PORT135, "data_dir": NODE135},
            {"name": "node136", "role": "MMR primary", "host": "127.0.0.1", "port": PORT136, "data_dir": NODE136},
        ],
        "relations": ["MMR 多活: node134 <-> node135 <-> node136（双向复制）", "物理流复制: 无"],
    }
    case["prerequisites"][0] = "严格按文档使用临时 three-node two_phase 拓扑，不触碰共享 mmr。"
    case["teardown"] = "fixture 以 immediate 停止临时 node134/node135/node136 并删除整个临时根目录；其中包含临时表、MMR 元数据、订阅、复制槽和本轮冲突记录。"
    return case


CASE = _case()


def specialize_case(case_id, name, section, resolver, start, gid, rollback_history,
                    commit_history, history_pattern=None):
    """Use the verified three-node 2PC topology for all section 3.11 resolvers."""
    case = deepcopy(CASE)
    case.update({"id": case_id, "name": name, "section": section})
    pattern = history_pattern or resolver
    replacements = {
        "update_if_newer": resolver,
        "20001": str(start), "20512": str(start + 511), "stream_20": gid,
    }
    for step in case["steps"]:
        for key in ("title", "expected", "display_sql"):
            if isinstance(step.get(key), str):
                for old, new in replacements.items():
                    step[key] = step[key].replace(old, new)
        if isinstance(step.get("argv"), list):
            step["argv"] = [
                _replace(value, replacements) if isinstance(value, str) else value
                for value in step["argv"]
            ]
    if pattern != resolver:
        for step in case["steps"]:
            for key in ("display_sql",):
                if isinstance(step.get(key), str):
                    step[key] = step[key].replace(
                        "conflict_resolution='%s'" % resolver,
                        "conflict_resolution LIKE '%s'" % pattern)
            if isinstance(step.get("argv"), list):
                step["argv"] = [
                    value.replace("conflict_resolution='%s'" % resolver,
                                  "conflict_resolution LIKE '%s'" % pattern)
                    if isinstance(value, str) else value for value in step["argv"]
                ]
    for step in case["steps"]:
        if step["title"].startswith("将 node135 update_origin_change 设为"):
            step["assertion"] = output_contains_text(
                "node135,update_origin_change,%s" % resolver)
        if step["title"].startswith("确认 rollback 后"):
            step["title"] = "确认 rollback 后 node135 冲突历史为 %s" % rollback_history
            step["expected"] = "45 秒内返回 %s" % rollback_history
            step["argv"][-1] = step["argv"][-1].replace("'0'", "'%s'" % rollback_history)
        if step["title"].startswith("确认 commit 后"):
            step["title"] = "确认 commit 后 node135 冲突历史为 %s" % commit_history
            step["expected"] = "45 秒内返回 %s" % commit_history
            step["argv"][-1] = step["argv"][-1].replace("'512'", "'%s'" % commit_history)
    case["conflict_configuration"][-1] = (
        "node135 的 update_origin_change=%s；node134/node136 按文档顺序并发预备。" % resolver)
    return case


def _replace(value, replacements):
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value
