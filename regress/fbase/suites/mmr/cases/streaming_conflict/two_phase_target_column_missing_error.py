"""Streaming conflict document 3.7.3: 2PC target_column_missing/error."""
from suites.mmr.two_phase_conflict_support import two_phase_target_column_case
CASE = two_phase_target_column_case("mmr.streaming_conflict.two_phase_target_column_missing_error", "2PC streaming target_column_missing 的 error 策略", "3.7.3", "error", 98001, "stream_98", "1", "0", "2")
