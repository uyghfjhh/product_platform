"""Streaming conflict document 3.2.5: 2PC update_missing/skip_transaction."""

from suites.mmr.two_phase_conflict_support import two_phase_update_missing_case


CASE = two_phase_update_missing_case(
    "mmr.streaming_conflict.two_phase_update_missing_skip_transaction",
    "2PC streaming update_missing 的 skip_transaction 策略", "3.2.5",
    "skip_transaction", 86, "f", "stream_86", "1", "2", False,
    "skip_transaction%")
