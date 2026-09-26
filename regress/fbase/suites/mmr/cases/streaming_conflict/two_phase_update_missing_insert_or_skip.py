"""Streaming conflict document 3.2.1: 2PC update_missing/insert_or_skip."""

from suites.mmr.two_phase_conflict_support import two_phase_update_missing_case


CASE = two_phase_update_missing_case(
    "mmr.streaming_conflict.two_phase_update_missing_insert_or_skip",
    "2PC streaming update_missing 的 insert_or_skip 策略", "3.2.1",
    "insert_or_skip", 90, "b", "stream_90", "0", "1", True)
