"""Streaming conflict document 3.4.2: 2PC update_pkey_exists/error."""

from suites.mmr.two_phase_conflict_support import two_phase_update_pkey_case


CASE = two_phase_update_pkey_case(
    "mmr.streaming_conflict.two_phase_update_pkey_exists_error",
    "2PC streaming update_pkey_exists 的 error 策略", "3.4.2",
    "error", 69001, "stream_69", "0", "1", "error%")
