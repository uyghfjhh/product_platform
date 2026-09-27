"""Streaming conflict document 2.3.4: insert_exists with update."""

from suites.mmr.cases.streaming_conflict.insert_exists_update_if_newer import (
    build_non2pc_insert_exists_case,
)


CASE = build_non2pc_insert_exists_case(
    "mmr.streaming_conflict.insert_exists_update",
    "streaming insert_exists 的 update 策略", "2.3.4",
    "update", 77, 77001, 77512, "512", "b", "1",
    "远端 id=77 强制覆盖本地值。")
