"""Streaming conflict document 3.8.3: 2PC source_column_missing/skip."""
from suites.mmr.two_phase_conflict_support import two_phase_source_column_case
CASE = two_phase_source_column_case("mmr.streaming_conflict.two_phase_source_column_missing_skip", "2PC streaming source_column_missing 的 skip 策略", "3.8.3", "skip", 98001, "stream_98", "0", "0", "512")
