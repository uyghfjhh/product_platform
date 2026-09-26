from framework.discovery import discover_cases


SUITE = {
    "id": "mac",
    "required_plugins": ["fbase_mac"],
    "coverage_module": "suites.mac.document_coverage",
    "coverage_complete": True,
    "groups": [
        "separation_of_duties", "mac", "audit", "password",
        "tde", "tlcp", "gm", "gb18030", "failover_slot",
    ],
    "cases": discover_cases("suites.mac.cases"),
}
