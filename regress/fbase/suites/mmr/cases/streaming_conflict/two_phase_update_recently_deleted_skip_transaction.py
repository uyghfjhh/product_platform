"""Streaming conflict document 3.5.5: 2PC update_recently_deleted/skip_transaction."""
from suites.mmr.two_phase_conflict_support import two_phase_update_recently_deleted_case
CASE = two_phase_update_recently_deleted_case("mmr.streaming_conflict.two_phase_update_recently_deleted_skip_transaction", "2PC streaming update_recently_deleted 的 skip_transaction 策略", "3.5.5", "skip_transaction", 56001, "stream_56", "0", "1", "skip_transaction%")
