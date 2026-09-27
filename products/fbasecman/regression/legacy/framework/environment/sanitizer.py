"""Pre-flight and post-flight environment sanitizer and self-healing guard."""

from pathlib import Path
from typing import Any, Dict

import platform_regress.environment.sanitizer as _sanitizer
from framework.configuration import load_regression_config
from framework.environment.registry import create_environment_provider


def _health_errors(health, config):
    """Compare the baseline indicators used by the deployment health gate."""
    ports = config["database"]["ports"]
    expected_streaming = len(ports.get("mmr1_standbys", (
        ports.get("mmr1_standby1"),
        ports.get("mmr1_standby2"),
        ports.get("mmr1_standby3"),
    )))
    expected = {
        "mmr_non_active": {"0"},
        "testdb_node1": {"ACTIVE"},
        "testdb_node2": {"JOIN_START", "ACTIVE"},
        "mmr_streaming": {str(expected_streaming)},
    }
    return [
        "%s=%s (expected %s)" % (key, health.get(key, "<missing>"), "/".join(sorted(values)))
        for key, values in expected.items()
        if str(health.get(key, "<missing>")) not in values
    ]


def preflight_health_check(root_dir: Path, auto_heal: bool = True) -> Dict[str, Any]:
    """Check the test environment and report failure without hiding its cause."""
    return _sanitizer.preflight_health_check(
        root_dir, "fbasecman", load_regression_config,
        expected_errors=_health_errors, auto_heal=auto_heal,
        provider_factory=create_environment_provider,
    )
