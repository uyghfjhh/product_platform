"""Streaming conflict document 3.6.1: 2PC delete_recently_updated/skip."""
from suites.mmr.two_phase_conflict_support import two_phase_delete_recently_updated_case
CASE = two_phase_delete_recently_updated_case("mmr.streaming_conflict.two_phase_delete_recently_updated_skip", "2PC streaming delete_recently_updated 的 skip 策略", "3.6.1", "skip", 50001, "stream_50", "512", "512")
