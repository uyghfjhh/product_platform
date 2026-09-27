"""Streaming conflict document 3.9.2: 2PC target_table_missing/error."""
from suites.mmr.cases.streaming_conflict.two_phase_target_table_missing_skip_if_recently_dropped import build_target_table_case
CASE = build_target_table_case("mmr.streaming_conflict.two_phase_target_table_missing_error", "2PC streaming target_table_missing 的 error 策略", "3.9.2", "error", 99001, "stream_99", "1", "2", True)
