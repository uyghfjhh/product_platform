"""Streaming conflict document 3.3.1: 2PC insert_exists/update_if_newer."""

from suites.mmr.two_phase_conflict_support import two_phase_insert_exists_case


CASE = two_phase_insert_exists_case(
    "mmr.streaming_conflict.two_phase_insert_exists_update_if_newer",
    "2PC streaming insert_exists 的 update_if_newer 策略", "3.3.1",
    "update_if_newer", 100, 100001, "b", "stream_100", "0", "1", True)
