"""Pre-flight and post-flight environment sanitizer and self-healing guard."""

from pathlib import Path
from typing import Any, Dict

from framework.configuration import load_regression_config
from framework.environment import create_environment_provider


def _health_errors(health, config):
    """Compare the baseline indicators used by the deployment health gate."""
    ports = config["database"]["ports"]
    expected_streaming = len(ports.get("mmr1_standbys", (
        ports.get("mmr1_standby1"),
        ports.get("mmr1_standby2"),
        ports.get("mmr1_standby3"),
    )))
    expected = {
        "mmr_non_active": "0",
        "testdb_node1": "ACTIVE",
        "testdb_node2": "JOIN_START",
        "mmr_streaming": str(expected_streaming),
    }
    return [
        "%s=%s (expected %s)" % (key, health.get(key, "<missing>"), value)
        for key, value in expected.items()
        if str(health.get(key, "<missing>")) != value
    ]


def preflight_health_check(root_dir: Path, auto_heal: bool = True) -> Dict[str, Any]:
    """Check the test environment and report failure without hiding its cause."""
    try:
        env = load_regression_config(root_dir)
        provider = create_environment_provider("fbasecman", env, verbose=False)
        if auto_heal:
            res = provider.heal()
            health = res.get("health", {}) if isinstance(res, dict) else {}
            errors = _health_errors(health, env.config)
            if errors:
                return {"status": "FAILED", "reason": "; ".join(errors)}
            return {"status": "HEALED", "result": res}
        return {"status": "CHECKED", "result": provider.status_text()}
    except Exception as exc:
        diagnostics = {}
        try:
            diagnostics["status_text"] = provider.status_text()
        except Exception as status_exc:
            diagnostics["status_error"] = str(status_exc)
        return {"status": "FAILED", "reason": str(exc), "diagnostics": diagnostics}
