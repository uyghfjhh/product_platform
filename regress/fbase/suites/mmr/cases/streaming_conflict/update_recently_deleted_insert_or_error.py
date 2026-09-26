"""Streaming conflict document 2.5.4: update_recently_deleted/insert_or_error."""

from suites.mmr.cases.streaming_conflict.update_recently_deleted_skip import (
    build_update_recently_deleted_case,
)


CASE = build_update_recently_deleted_case(
    "mmr.streaming_conflict.update_recently_deleted_insert_or_error",
    "streaming update_recently_deleted 的 insert_or_error 策略", "2.5.4",
    "insert_or_error", 57001, "512", None)
