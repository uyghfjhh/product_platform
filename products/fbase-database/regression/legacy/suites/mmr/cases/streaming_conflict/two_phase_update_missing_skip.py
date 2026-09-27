"""Streaming conflict document 3.2.3: 2PC update_missing/skip."""

from suites.mmr.two_phase_conflict_support import two_phase_update_missing_case


CASE = two_phase_update_missing_case(
    "mmr.streaming_conflict.two_phase_update_missing_skip",
    "2PC streaming update_missing 的 skip 策略", "3.2.3",
    "skip", 88, "d", "stream_88", "0", "1", False)
