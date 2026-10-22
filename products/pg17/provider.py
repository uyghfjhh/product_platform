"""Upstream PostgreSQL provider: pgcluster-managed deployments (pg_deltax, TimescaleDB)."""

import importlib

from platform_app.postgresql_observations import (
    observe_pg_replication,
    observe_pg_version,
    parse_replication,
)


def _templates():
    return importlib.import_module("products.pg17.deployment.templates")


class Pg17Provider:
    def deployment_templates(self, settings):
        return _templates().templates(settings)

    def compile_deployment(self, settings, spec, environment_id):
        return _templates().compile_template(settings, spec, environment_id)

    def validate_target(self, settings, target):
        return target.startswith("streaming.")

    def discover(self, settings):
        return []

    def command(self, settings, environment, action, target, parameters):
        raise ValueError("PostgreSQL 未注册操作: %s" % action)

    def observe_runtime(self, settings, environment):
        return observe_pg_replication(
            environment, lambda text: self.parse_runtime("replication", text)
        )

    def parse_runtime(self, name, text):
        if name != "replication":
            raise ValueError("PostgreSQL 未注册观测: %s" % name)
        return parse_replication(text)

    def observe_database(self, environment):
        return observe_pg_version(environment)


PROVIDER = Pg17Provider()
