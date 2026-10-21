"""Upstream PostgreSQL provider: pgcluster-managed deployments (pg_deltax, TimescaleDB)."""

import importlib

import psycopg
from platform_app.postgresql_observations import parse_replication
from platform_app.providers import Observation


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
        connection = psycopg.connect(
            host=environment["host"], port=environment["port"],
            dbname=environment.get("database_name") or "postgres",
            user=environment.get("database_user") or "postgres",
            connect_timeout=4, options="-c statement_timeout=4000",
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT application_name, client_addr::text, state, sync_state "
                    "FROM pg_stat_replication ORDER BY application_name"
                )
                rows = cursor.fetchall()
            text = "application_name\tclient_addr\tstate\tsync_state\n" + "\n".join(
                "\t".join("" if value is None else str(value) for value in row)
                for row in rows
            )
            return self.parse_runtime("replication", text)
        finally:
            connection.close()

    def parse_runtime(self, name, text):
        if name != "replication":
            raise ValueError("PostgreSQL 未注册观测: %s" % name)
        return parse_replication(text)

    def observe_database(self, environment):
        connection = psycopg.connect(
            host=environment["host"], port=environment["port"],
            dbname=environment.get("database_name") or "postgres",
            user=environment.get("database_user") or "postgres",
            connect_timeout=4, options="-c statement_timeout=4000",
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
            return [Observation("sql.version", "ready", {"version": version})]
        finally:
            connection.close()


PROVIDER = Pg17Provider()
