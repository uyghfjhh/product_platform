"""Streaming conflict document 2.5.2: update_recently_deleted/error."""

from suites.mmr.cases.streaming_conflict.update_recently_deleted_skip import (
    build_update_recently_deleted_case,
)


CASE = build_update_recently_deleted_case(
    "mmr.streaming_conflict.update_recently_deleted_error",
    "streaming update_recently_deleted 的 error 策略", "2.5.2", "error",
    59001, "0", "1")
