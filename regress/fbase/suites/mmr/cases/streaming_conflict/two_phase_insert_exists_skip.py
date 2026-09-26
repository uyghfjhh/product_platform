"""Streaming conflict document 3.3.3: 2PC insert_exists/skip."""

from suites.mmr.two_phase_conflict_support import two_phase_insert_exists_case


CASE = two_phase_insert_exists_case(
    "mmr.streaming_conflict.two_phase_insert_exists_skip",
    "2PC streaming insert_exists 的 skip 策略", "3.3.3",
    "skip", 98, 98001, "b", "stream_98", "0", "1", False)
