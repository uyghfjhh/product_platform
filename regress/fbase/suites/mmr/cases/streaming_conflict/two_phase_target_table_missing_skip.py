"""Streaming conflict document 3.9.3: 2PC target_table_missing/skip."""
from suites.mmr.cases.streaming_conflict.two_phase_target_table_missing_skip_if_recently_dropped import build_target_table_case
CASE = build_target_table_case("mmr.streaming_conflict.two_phase_target_table_missing_skip", "2PC streaming target_table_missing 的 skip 策略", "3.9.3", "skip", 98001, "stream_98", "0", "512")
