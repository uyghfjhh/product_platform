"""Pre-flight and post-flight environment sanitizer and self-healing guard."""

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from platform_regress.environment.registry import create_environment_provider


def preflight_health_check(
    root_dir: Path,
    provider_name: str,
    loader: Callable[[Path], Any],
    expected_errors: Optional[Callable[[Any, Any], list]] = None,
    auto_heal: bool = True,
    provider_factory: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Check the test environment and report failure without hiding its cause."""
    factory = provider_factory or create_environment_provider
    try:
        env = loader(root_dir)
        provider = factory(provider_name, env, verbose=False)
        if auto_heal:
            res = provider.heal()
            health = res.get("health", {}) if isinstance(res, dict) else {}
            errors = expected_errors(health, env.config) if expected_errors else []
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
