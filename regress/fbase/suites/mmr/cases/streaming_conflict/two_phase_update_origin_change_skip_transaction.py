"""Streaming conflict document 3.11.5: 2PC update_origin_change/skip_transaction."""

from suites.mmr.cases.streaming_conflict.two_phase_update_origin_change_update_if_newer import specialize_case


CASE = specialize_case(
    "mmr.streaming_conflict.two_phase_update_origin_change_skip_transaction",
    "2PC streaming update_origin_change 的 skip_transaction 策略", "3.11.5", "skip_transaction",
    16001, "stream_16", "1", "2", "skip_transaction%")
