"""Stable contracts shared by suite manifests, runners and front ends."""

import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


class CaseSpec(object):
    """Common identity and selection fields for declarative suite cases."""

    def __init__(self, suite_id, name, summary, executor, enabled=True,
                 topology=None, route_mode=None, tags=()):
        self.suite_id = suite_id
        self.name = name
        self.summary = summary
        self.executor = executor
        self.enabled = bool(enabled)
        self.topology = topology
        self.route_mode = route_mode
        self.tags = tuple(tags)
        # ``suite_name`` is retained as the run-artifact directory key used by
        # the established runtimes; new code should prefer ``suite_id``.
        self.suite_name = suite_id

    @property
    def target(self):
        return "%s.%s" % (self.suite_id, self.name)


class CaseResult(object):
    """Normalized result value for new runners and application services."""

    def __init__(self, target, status, duration_seconds=0.0, reason=None,
                 artifacts=None, steps=None):
        self.target = target
        self.status = status
        self.duration_seconds = float(duration_seconds)
        self.reason = reason
        self.artifacts = dict(artifacts or {})
        self.steps = list(steps or [])


def validate_cases(suite_id: str, cases: Iterable[Any]) -> List[Any]:
    """Validate the minimum case contract and return a materialized list."""
    materialized = list(cases)
    names = set()
    for case in materialized:
        for attribute in ("name", "summary", "target"):
            if not getattr(case, attribute, None):
                raise ValueError(
                    "suite %r case %r has no %s" % (suite_id, case, attribute)
                )
        expected_target = "%s.%s" % (suite_id, case.name)
        if case.target != expected_target:
            raise ValueError(
                "suite %r case %r has target %r, expected %r"
                % (suite_id, case.name, case.target, expected_target)
            )
        if case.name in names:
            raise ValueError("suite %r has duplicate case %r" % (suite_id, case.name))
        names.add(case.name)
    return materialized


class SuitePlugin(object):
    """One explicit integration point for a test suite.

    A plugin owns the suite metadata, its selectable manifest cases, and the
    runner/show delegates.  The registry therefore no longer guesses module
    names, case getter names, or uppercase constants.
    """

    def __init__(
        self,
        suite_id: str,
        title: str,
        description: str,
        case_loader: Callable[[], Iterable[Any]],
        runner: Callable[[Path, Optional[str]], Any],
        shower: Optional[Callable[[], str]] = None,
        prefix: Optional[str] = None,
    ):
        self.id = suite_id
        self.title = title
        self.description = description
        self.prefix = prefix or "%s." % suite_id
        self._case_loader = case_loader
        self._runner = runner
        self._shower = shower

    def get_cases(self) -> List[Any]:
        return validate_cases(self.id, self._case_loader())

    def get_targets(self) -> List[str]:
        return [case.target for case in self.get_cases()]

    def show(self) -> str:
        if self._shower is not None:
            return self._shower()
        lines = ["%s - %s" % (self.id, self.title)]
        lines.extend("  - %s" % target for target in self.get_targets())
        return "\n".join(lines)

    def run(self, root_dir: Path, target: Optional[str] = None) -> int:
        try:
            result = self._runner(root_dir, target=target)
            if isinstance(result, CaseResult):
                return 0 if result.status == "PASS" else 1
            if isinstance(result, bool):
                return 0 if result else 1
            if isinstance(result, int) and result >= 0:
                return result
            raise TypeError("suite runner must return bool, non-negative int or CaseResult")
        except Exception as exc:
            print("[%s] Suite execution raised exception: %s" % (self.id, exc),
                  file=sys.stderr)
            return 1
