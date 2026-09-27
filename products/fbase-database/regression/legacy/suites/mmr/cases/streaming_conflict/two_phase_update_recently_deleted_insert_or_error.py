"""Streaming conflict document 3.5.4: 2PC update_recently_deleted/insert_or_error."""
from suites.mmr.two_phase_conflict_support import two_phase_update_recently_deleted_case
CASE = two_phase_update_recently_deleted_case("mmr.streaming_conflict.two_phase_update_recently_deleted_insert_or_error", "2PC streaming update_recently_deleted 的 insert_or_error 策略", "3.5.4", "insert_or_error", 57001, "stream_57", "512", "512")
