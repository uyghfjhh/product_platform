"""Streaming conflict document 3.11.4: 2PC update_origin_change/update."""

from suites.mmr.cases.streaming_conflict.two_phase_update_origin_change_update_if_newer import specialize_case


CASE = specialize_case(
    "mmr.streaming_conflict.two_phase_update_origin_change_update",
    "2PC streaming update_origin_change 的 update 策略", "3.11.4", "update",
    17001, "stream_17", "0", "512")
