"""Streaming conflict document 3.3.4: 2PC insert_exists/update."""

from suites.mmr.two_phase_conflict_support import two_phase_insert_exists_case


CASE = two_phase_insert_exists_case(
    "mmr.streaming_conflict.two_phase_insert_exists_update",
    "2PC streaming insert_exists 的 update 策略", "3.3.4",
    "update", 97, 97001, "b", "stream_97", "0", "1", True)
