"""Streaming conflict document 3.8.2: 2PC source_column_missing/error."""
from suites.mmr.two_phase_conflict_support import two_phase_source_column_case
CASE = two_phase_source_column_case("mmr.streaming_conflict.two_phase_source_column_missing_error", "2PC streaming source_column_missing 的 error 策略", "3.8.2", "error", 99001, "stream_99", "1", "0", "2")
