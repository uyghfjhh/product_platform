"""Streaming conflict document 3.1.3: 2PC delete_missing/skip_transaction."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.delete_missing_skip import PORT134, PORT135, sql, wait_for_rows
from suites.mmr.cases.streaming_conflict.two_phase_delete_missing_error import CASE as ERROR_CASE
from suites.mmr.cases.streaming_conflict.two_phase_delete_missing_skip import TABLE


CASE = deepcopy(ERROR_CASE)
CASE.update({"id": "mmr.streaming_conflict.two_phase_delete_missing_skip_transaction",
             "name": "2PC streaming delete_missing 的 skip_transaction 策略", "section": "3.1.3"})
CASE["conflict_configuration"][-1] = "node135 的 delete_missing=skip_transaction；先按 3.1.2 重建 error 回放前态。"
CASE["steps"][-1]["continue_on_failure"] = True
CASE["steps"] += [
    sql("按文档将 node135 delete_missing 改为 skip_transaction", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('delete_missing','skip_transaction')",
        "返回 node135,delete_missing,skip_transaction", "node135,delete_missing,skip_transaction", report_node="node135"),
    wait_for_rows("确认 worker 重试后已有一条 skip_transaction 历史", PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='delete_missing' AND conflict_resolution LIKE 'skip_transaction%%'" % TABLE,
        "1", "node135"),
    sql("按文档插入 skip_transaction 前置 512 行", PORT134,
        "INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(98001,98512) gs" % TABLE,
        "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
    wait_for_rows("等待 node135 收到 skip_transaction 前置数据", PORT135,
        "SELECT count(*) FROM %s WHERE id BETWEEN 98001 AND 98512" % TABLE, "512", "node135"),
    sql("按文档准备第一次 skip_transaction 删除", PORT134,
        "BEGIN; DELETE FROM %s WHERE id=98 OR id BETWEEN 98001 AND 98512; PREPARE TRANSACTION 'stream_98'" % TABLE,
        "返回 BEGIN、DELETE 513、PREPARE TRANSACTION", "BEGIN", "DELETE 513", "PREPARE TRANSACTION", report_node="node134"),
    sql("按文档回滚第一次 skip_transaction 预备事务", PORT134,
        "ROLLBACK PREPARED 'stream_98'", "返回 ROLLBACK PREPARED", "ROLLBACK PREPARED", report_node="node134"),
    wait_for_rows("确认 rollback 后累计两条 skip_transaction 历史", PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='delete_missing' AND conflict_resolution LIKE 'skip_transaction%%'" % TABLE,
        "2", "node135"),
    sql("按文档准备第二次 skip_transaction 删除", PORT134,
        "BEGIN; DELETE FROM %s WHERE id=98 OR id BETWEEN 98001 AND 98512; PREPARE TRANSACTION 'stream_98'" % TABLE,
        "返回 BEGIN、DELETE 513、PREPARE TRANSACTION", "BEGIN", "DELETE 513", "PREPARE TRANSACTION", report_node="node134"),
    sql("按文档提交第二次 skip_transaction 预备事务", PORT134,
        "COMMIT PREPARED 'stream_98'", "返回 COMMIT PREPARED", "COMMIT PREPARED", report_node="node134"),
    wait_for_rows("确认 commit 后 node135 保留 512 行且累计三条 skip_transaction 历史", PORT135,
        "SELECT (SELECT count(*) FROM %s WHERE id=98 OR id BETWEEN 98001 AND 98512)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='delete_missing' AND conflict_resolution LIKE 'skip_transaction%%')::text" % (TABLE,TABLE),
        "512|3", "node135"),
]
