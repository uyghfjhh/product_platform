"""Streaming conflict document 3.4.3: 2PC update_pkey_exists/skip."""

from suites.mmr.two_phase_conflict_support import two_phase_update_pkey_case


CASE = two_phase_update_pkey_case(
    "mmr.streaming_conflict.two_phase_update_pkey_exists_skip",
    "2PC streaming update_pkey_exists 的 skip 策略", "3.4.3",
    "skip", 68001, "stream_68", "0", "512")
