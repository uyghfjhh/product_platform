"""Streaming conflict document 2.6.2: delete_recently_updated with error."""

from suites.mmr.cases.streaming_conflict.delete_recently_updated_skip import (
    build_non2pc_delete_recently_updated_case,
)


CASE = build_non2pc_delete_recently_updated_case(
    "mmr.streaming_conflict.delete_recently_updated_error",
    "streaming delete_recently_updated 的 error 策略", "2.6.2", "error",
    49001, "512", "1", "首条冲突记录后回放报错，目标端保留本地更新。")
