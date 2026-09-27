"""Streaming conflict document 3.7.2: 2PC target_column_missing/skip."""
from suites.mmr.two_phase_conflict_support import two_phase_target_column_case
CASE = two_phase_target_column_case("mmr.streaming_conflict.two_phase_target_column_missing_skip", "2PC streaming target_column_missing 的 skip 策略", "3.7.2", "skip", 99001, "stream_99", "0", "0", "512")
