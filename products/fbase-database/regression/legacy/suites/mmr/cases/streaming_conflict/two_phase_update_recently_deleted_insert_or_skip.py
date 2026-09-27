"""Streaming conflict document 3.5.3: 2PC update_recently_deleted/insert_or_skip."""
from suites.mmr.two_phase_conflict_support import two_phase_update_recently_deleted_case
CASE = two_phase_update_recently_deleted_case("mmr.streaming_conflict.two_phase_update_recently_deleted_insert_or_skip", "2PC streaming update_recently_deleted 的 insert_or_skip 策略", "3.5.3", "insert_or_skip", 58001, "stream_58", "512", "512")
