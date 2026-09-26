"""Streaming conflict document 2.3.2: insert_exists with error."""

from suites.mmr.cases.streaming_conflict.insert_exists_update_if_newer import (
    build_non2pc_insert_exists_case,
)


CASE = build_non2pc_insert_exists_case(
    "mmr.streaming_conflict.insert_exists_error",
    "streaming insert_exists 的 error 策略", "2.3.2",
    "error", 79, 79001, 79512, "0", "a", "1",
    "id=79 保持本地值；error 终止本次 apply 事务，非冲突行不应用。")
