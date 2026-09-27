"""Streaming conflict document 3.11.3: 2PC update_origin_change/skip."""

from suites.mmr.cases.streaming_conflict.two_phase_update_origin_change_update_if_newer import specialize_case


CASE = specialize_case(
    "mmr.streaming_conflict.two_phase_update_origin_change_skip",
    "2PC streaming update_origin_change 的 skip 策略", "3.11.3", "skip",
    18001, "stream_18", "0", "512")
