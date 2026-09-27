"""Streaming conflict document 3.4.5: 2PC update_pkey_exists/skip_transaction."""

from suites.mmr.two_phase_conflict_support import two_phase_update_pkey_case


CASE = two_phase_update_pkey_case(
    "mmr.streaming_conflict.two_phase_update_pkey_exists_skip_transaction",
    "2PC streaming update_pkey_exists 的 skip_transaction 策略", "3.4.5",
    "skip_transaction", 66001, "stream_66", "0", "1", "skip_transaction%")
