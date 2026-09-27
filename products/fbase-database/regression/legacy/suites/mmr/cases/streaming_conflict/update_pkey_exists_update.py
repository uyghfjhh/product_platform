"""Streaming conflict document 2.4.4: update_pkey_exists with update."""

from suites.mmr.cases.streaming_conflict.update_pkey_exists_update_if_newer import (
    build_non2pc_update_pkey_case,
)


CASE = build_non2pc_update_pkey_case(
    "mmr.streaming_conflict.update_pkey_exists_update",
    "streaming update_pkey_exists 的 update 策略", "2.4.4",
    "update", 67001, "512", "远端主键更新强制应用，node135 记录全部 512 条冲突。")
