"""Streaming conflict document 2.4.5: update_pkey_exists with skip_transaction."""

from suites.mmr.cases.streaming_conflict.update_pkey_exists_update_if_newer import (
    build_non2pc_update_pkey_case,
)


CASE = build_non2pc_update_pkey_case(
    "mmr.streaming_conflict.update_pkey_exists_skip_transaction",
    "streaming update_pkey_exists 的 skip_transaction 策略", "2.4.5",
    "skip_transaction", 66001, "1", "首个主键冲突触发整事务跳过，仅记录一条冲突。")
