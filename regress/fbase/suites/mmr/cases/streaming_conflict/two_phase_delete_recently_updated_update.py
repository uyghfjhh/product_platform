"""Streaming conflict document 3.6.3: 2PC delete_recently_updated/update."""
from suites.mmr.two_phase_conflict_support import two_phase_delete_recently_updated_case
CASE = two_phase_delete_recently_updated_case("mmr.streaming_conflict.two_phase_delete_recently_updated_update", "2PC streaming delete_recently_updated 的 update 策略", "3.6.3", "update", 48001, "stream_48", "0", "512")
