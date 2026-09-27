"""Streaming conflict document 3.7.4: 2PC target_column_missing/ignore."""
from suites.mmr.two_phase_conflict_support import two_phase_target_column_case
CASE = two_phase_target_column_case("mmr.streaming_conflict.two_phase_target_column_missing_ignore", "2PC streaming target_column_missing 的 ignore 策略", "3.7.4", "ignore", 97001, "stream_97", "0", "512", "512")
