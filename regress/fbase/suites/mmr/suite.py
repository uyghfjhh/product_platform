from framework.discovery import discover_cases


SUITE = {
    "id": "mmr",
    "required_plugins": ["fdd_mmr"],
    "coverage_module": "suites.mmr.document_coverage",
    "coverage_complete": True,
    "groups": ["cluster_verification", "conflict", "failover_slot", "node_function_control",
               "global_sequence", "remote_sql", "subscription_control",
               "replication_set", "default_publication", "two_phase", "streaming",
               "streaming_conflict", "background", "forwarding", "installation", "node_management"],
    "cases": discover_cases("suites.mmr.cases"),
}
