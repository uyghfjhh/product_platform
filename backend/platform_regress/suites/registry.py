"""Product-neutral registry for explicitly declared suite plugins."""

import sys
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .contracts import SuitePlugin


_DEFAULT_PREFLIGHT_CHECK = None
_DEFAULT_QUIET_ENV_VAR = "PLATFORM_REGRESS_QUIET_ENV"


def set_default_preflight_check(check):
    """Install the process-wide preflight hook used by bare registries."""
    global _DEFAULT_PREFLIGHT_CHECK
    _DEFAULT_PREFLIGHT_CHECK = check


def set_default_quiet_env_var(name):
    """Set the env var toggled around preflight to silence environment output."""
    global _DEFAULT_QUIET_ENV_VAR
    _DEFAULT_QUIET_ENV_VAR = name


class SuiteRegistry(object):
    """Registry managing explicitly declared suite plugins."""

    def __init__(self, preflight_check=None, quiet_env_var=None):
        self._suites: Dict[str, SuitePlugin] = {}
        self._order: List[str] = []
        self._preflight_check = preflight_check
        self._quiet_env_var = quiet_env_var


    def register(self, plugin: SuitePlugin) -> None:
        if not isinstance(plugin, SuitePlugin):
            raise TypeError("suite registration requires a SuitePlugin")
        if plugin.id in self._suites:
            raise ValueError("Suite %r is already registered." % plugin.id)
        self._suites[plugin.id] = plugin
        self._order.append(plugin.id)

    def get(self, suite_id: str) -> Optional[SuitePlugin]:
        return self._suites.get(suite_id)

    def all_suites(self) -> List[SuitePlugin]:
        return [self._suites[suite_id] for suite_id in self._order]

    def suite_ids(self) -> List[str]:
        return list(self._order)

    def suite_targets(self, suite_id: str) -> List[str]:
        plugin = self.get(suite_id)
        return plugin.get_targets() if plugin else []

    def selected_targets(self, target: str) -> List[str]:
        if "." in target:
            suite_id, _, _ = target.partition(".")
            plugin = self.get(suite_id)
            return [target] if plugin and target in plugin.get_targets() else []
        return self.suite_targets(target)

    def run_target(self, root_dir: Path, target: str, sanitize: bool = True,
                   preflight: str = "heal") -> int:
        suite_id, separator, sub_target = target.partition(".")
        plugin = self.get(suite_id)
        if not plugin:
            print("Unknown suite %r in target %r" % (suite_id, target), file=sys.stderr)
            return 2
        if separator and target not in plugin.get_targets():
            print("Unknown case %r" % target, file=sys.stderr)
            return 2
        if preflight not in ("heal", "warn", "off"):
            raise ValueError("unknown preflight policy %r" % preflight)
        checker = self._preflight_check or _DEFAULT_PREFLIGHT_CHECK
        quiet_var = self._quiet_env_var or _DEFAULT_QUIET_ENV_VAR
        if sanitize and preflight != "off" and checker is not None:
            try:
                previous_quiet = os.environ.get(quiet_var)
                os.environ[quiet_var] = "1"
                check = checker(root_dir)
            except Exception as exc:
                check = {"status": "FAILED", "reason": str(exc)}
            finally:
                if previous_quiet is None:
                    os.environ.pop(quiet_var, None)
                else:
                    os.environ[quiet_var] = previous_quiet
            if check.get("status") == "FAILED":
                print("Environment preflight failed: %s" % check.get("reason", "unknown error"),
                      file=sys.stderr)
                diagnostics = check.get("diagnostics", {})
                if diagnostics.get("status_text"):
                    print("Environment diagnostics:\n%s" % diagnostics["status_text"], file=sys.stderr)
                if preflight == "heal":
                    return 3
        return plugin.run(root_dir, target=sub_target if separator else None)

    def show_target(self, target: Optional[str]) -> Optional[str]:
        if not target or target == "all":
            return "\n\n".join(plugin.show() for plugin in self.all_suites())
        plugin = self.get(target)
        return plugin.show() if plugin else None

    def to_web_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": plugin.id,
                "title": plugin.title,
                "description": plugin.description,
                "items": plugin.get_cases(),
                "prefix": plugin.prefix,
            }
            for plugin in self.all_suites()
        ]
