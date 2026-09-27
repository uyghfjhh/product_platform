"""Streaming conflict document 3.5.1: 2PC update_recently_deleted/skip."""
from suites.mmr.two_phase_conflict_support import two_phase_update_recently_deleted_case
CASE = two_phase_update_recently_deleted_case("mmr.streaming_conflict.two_phase_update_recently_deleted_skip", "2PC streaming update_recently_deleted 的 skip 策略", "3.5.1", "skip", 60001, "stream_60", "0", "512")
