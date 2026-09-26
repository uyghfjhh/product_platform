"""Streaming conflict document 3.2.4: 2PC update_missing/insert_or_error."""

from suites.mmr.two_phase_conflict_support import two_phase_update_missing_case


CASE = two_phase_update_missing_case(
    "mmr.streaming_conflict.two_phase_update_missing_insert_or_error",
    "2PC streaming update_missing 的 insert_or_error 策略", "3.2.4",
    "insert_or_error", 87, "e", "stream_87", "0", "1", True)
