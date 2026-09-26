"""Streaming conflict document 2.4.2: update_pkey_exists with error."""

from suites.mmr.cases.streaming_conflict.update_pkey_exists_update_if_newer import (
    build_non2pc_update_pkey_case,
)


CASE = build_non2pc_update_pkey_case(
    "mmr.streaming_conflict.update_pkey_exists_error",
    "streaming update_pkey_exists 的 error 策略", "2.4.2",
    "error", 69001, "1", "首行冲突报错，文档要求 node135 本地 512 行仍保留。")
