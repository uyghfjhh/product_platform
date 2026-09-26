"""Streaming conflict document 2.5.3: update_recently_deleted/insert_or_skip."""

from suites.mmr.cases.streaming_conflict.update_recently_deleted_skip import (
    build_update_recently_deleted_case,
)


CASE = build_update_recently_deleted_case(
    "mmr.streaming_conflict.update_recently_deleted_insert_or_skip",
    "streaming update_recently_deleted 的 insert_or_skip 策略", "2.5.3",
    "insert_or_skip", 58001, "512", None)
