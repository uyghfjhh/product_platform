"""Streaming conflict document 3.6.2: 2PC delete_recently_updated/error."""
from suites.mmr.two_phase_conflict_support import two_phase_delete_recently_updated_case
CASE = two_phase_delete_recently_updated_case("mmr.streaming_conflict.two_phase_delete_recently_updated_error", "2PC streaming delete_recently_updated 的 error 策略", "3.6.2", "error", 49001, "stream_49", "512", "1", "error%")
