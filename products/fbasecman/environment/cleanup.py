"""Ownership-checked removal of managed PostgreSQL test nodes."""

from framework.configuration import RegressionConfig
from framework.execution.shell import LoggedShellRunner

from .ownership import build_cleanup_plan, cleanup_script


def clean_environment(env: RegressionConfig, runner: LoggedShellRunner, dry_run=False):
    plan = build_cleanup_plan(env)
    if dry_run:
        return plan
    for index, scope in enumerate(plan.scopes, 1):
        runner.run_remote(
            scope.user, scope.host, cleanup_script(scope),
            log_name="%02d_clean_%s.log" % (index, scope.nodes[0].topology),
        )
    return plan
