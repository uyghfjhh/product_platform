"""Streaming conflict document 3.4.4: 2PC update_pkey_exists/update."""

from suites.mmr.two_phase_conflict_support import two_phase_update_pkey_case


CASE = two_phase_update_pkey_case(
    "mmr.streaming_conflict.two_phase_update_pkey_exists_update",
    "2PC streaming update_pkey_exists 的 update 策略", "3.4.4",
    "update", 67001, "stream_67", "0", "512")
