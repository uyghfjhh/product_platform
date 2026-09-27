"""Streaming conflict document 2.5.5: update_recently_deleted/skip_transaction."""

from suites.mmr.cases.streaming_conflict.update_recently_deleted_skip import (
    build_update_recently_deleted_case,
)


CASE = build_update_recently_deleted_case(
    "mmr.streaming_conflict.update_recently_deleted_skip_transaction",
    "streaming update_recently_deleted 的 skip_transaction 策略", "2.5.5",
    "skip_transaction", 56001, "0", "1")
