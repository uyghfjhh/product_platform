"""Streaming conflict document 3.5.2: 2PC update_recently_deleted/error."""
from suites.mmr.two_phase_conflict_support import two_phase_update_recently_deleted_case
CASE = two_phase_update_recently_deleted_case("mmr.streaming_conflict.two_phase_update_recently_deleted_error", "2PC streaming update_recently_deleted 的 error 策略", "3.5.2", "error", 59001, "stream_59", "0", "1", "error%")
