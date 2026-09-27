"""Adapter for the existing fbasecman MMR/replication environment manager."""

from .cluster import ClusterManager
from .inventory import format_inventory
from framework.environment.provider import EnvironmentProvider


class FbasecmanEnvironmentProvider(EnvironmentProvider):
    def __init__(self, regress_env, verbose=True):
        self.regress_env = regress_env
        self.manager = ClusterManager(regress_env, verbose=verbose)

    def setup(self, adopt_existing=False):
        return self.manager.setup(adopt_existing=adopt_existing)

    def clean(self, dry_run=False, adopt_existing=False):
        return self.manager.clean(
            dry_run=dry_run, adopt_existing=adopt_existing,
        )

    def start(self):
        return self.manager.start()

    def restart(self):
        return self.manager.restart()

    def stop(self):
        return self.manager.stop()

    def heal(self):
        return self.manager.heal()

    def status_text(self):
        status = self.manager.status()
        return "\n".join([
            format_inventory(status["inventory"]),
            "",
            self._format_health(status["health"]),
            "",
            "Logs: %s" % self.regress_env.env_logs_dir,
        ])

    def _format_health(self, health):
        db = self.regress_env.config["database"]
        streaming_actual = health.get("mmr_streaming", health.get("rep_streaming", "-"))
        expected_streaming = len(db["ports"].get("mmr1_standbys", db["ports"].get("rep_standbys", ())))
        checks = [
            ("MMR non-active nodes", health.get("mmr_non_active", "-"), "expect 0",
             "postgres@MMR_HOST: psql -p MMR1_PORT -d postgres; select count(*) from fdd.mmr_node where node_state != 'ACTIVE';"),
            ("test_db testdb_node1", health.get("testdb_node1", "-"), "expect ACTIVE",
             "postgres@MMR_HOST: psql -p MMR1_PORT -d test_db; select node_state from fdd.mmr_node where node_name = 'testdb_node1';"),
            ("test_db testdb_node2", health.get("testdb_node2", "-"), "expect JOIN_START",
             "postgres@MMR_HOST: psql -p MMR1_PORT -d test_db; select node_state from fdd.mmr_node where node_name = 'testdb_node2';"),
            ("MMR1 streaming replicas", streaming_actual, "expect %s" % expected_streaming,
             "postgres@MMR_HOST: psql -p MMR1_PORT -d postgres; select count(*) from pg_stat_replication where state = 'streaming' and application_name not like 'fmmr%';"),
        ]
        lines = ["Health checks:"]
        for name, actual, expected, source in checks:
            lines.append("  %-24s actual=%-10s %s" % (name, actual, expected))
            lines.append("    source: %s" % source)
        return "\n".join(lines)
