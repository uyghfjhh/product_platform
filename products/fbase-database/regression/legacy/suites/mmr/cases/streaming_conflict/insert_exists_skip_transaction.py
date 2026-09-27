"""Streaming conflict document 2.3.5: insert_exists with skip_transaction."""

from suites.mmr.cases.streaming_conflict.insert_exists_update_if_newer import (
    build_non2pc_insert_exists_case,
)


CASE = build_non2pc_insert_exists_case(
    "mmr.streaming_conflict.insert_exists_skip_transaction",
    "streaming insert_exists 的 skip_transaction 策略", "2.3.5",
    "skip_transaction", 76, 76001, 76512, "0", "a", "1",
    "冲突后同一事务的 512 行也必须跳过。")
