"""Streaming conflict document 3.11.2: 2PC update_origin_change/error."""

from suites.mmr.cases.streaming_conflict.two_phase_update_origin_change_update_if_newer import specialize_case


CASE = specialize_case(
    "mmr.streaming_conflict.two_phase_update_origin_change_error",
    "2PC streaming update_origin_change 的 error 策略", "3.11.2", "error",
    19001, "stream_19", "1", "2", "error%")
