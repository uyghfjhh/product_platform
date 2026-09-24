"""Pre-flight and post-flight environment sanitizer and self-healing guard."""

from pathlib import Path
from typing import Any, Dict

from framework.configuration import load_regression_config
from framework.environment import create_environment_provider


def preflight_health_check(root_dir: Path, auto_heal: bool = True) -> Dict[str, Any]:
    """Inspect environment state before test execution; trigger auto-heal if needed."""
    try:
        env = load_regression_config(root_dir)
        provider = create_environment_provider("fbasecman", env, verbose=False)
        if auto_heal:
            res = provider.heal()
            if isinstance(res, dict) and "health" in res:
                health = res["health"]
                unavail = [f"{k}: {v}" for k, v in health.items() if "UNAVAILABLE" in str(v)]
                if unavail:
                    return {"status": "FAILED", "reason": ", ".join(unavail)}
            return {"status": "HEALED", "result": res}
        return {"status": "CHECKED"}
    except Exception as exc:
        return {"status": "FAILED", "reason": str(exc)}
