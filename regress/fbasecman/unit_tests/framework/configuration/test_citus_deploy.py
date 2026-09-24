import unittest
from types import SimpleNamespace

from framework.configuration import ConfigurationError, validate_config
from env.mmr import build_mmr_setup_script
from env.replication import build_replication_setup_script


def _base_valid_config():
    return {
        "fbasecman": {
            "fbasecman_bin": "/opt/fbasecman/bin/fbasecman",
            "write_port": 17432,
            "read_port": 16432,
            "log_level": "info",
            "license_dir": "/opt/lic",
        },
        "database": {
            "mmr_host": "127.0.0.1",
            "rep_host": "127.0.0.1",
            "mmr_pg_user": "postgres",
            "rep_pg_user": "postgres",
            "mmr_postgres_dir": "/usr/local/pgsql",
            "rep_postgres_dir": "/usr/local/pgsql",
            "ports": {
                "mmr1": 10011,
                "mmr1_standby1": 10012,
                "mmr1_standby2": 10013,
                "mmr1_standby3": 10014,
                "mmr2": 10021,
                "mmr2_standby1": 10022,
                "mmr2_standby2": 10023,
                "mmr2_standby3": 10024,
                "rep_primary": 10051,
                "rep_standby1": 10052,
                "rep_standby2": 10053,
            },
        },
        "local": {
            "postgres_dir": "/usr/local/pgsql",
            "jdbc_lib_dir": "lib_jdbc",
            "jdbc_versions": ["42.7.7"],
        },
        "framework": {
            "output_dir": "output",
            "default_timeout": 60,
        },
    }


class CitusDeploymentConfigTest(unittest.TestCase):
    def test_validation_allows_boolean_enable_citus(self):
        cfg = _base_valid_config()
        cfg["database"]["enable_citus"] = True
        validated = validate_config(cfg, profile="regress")
        self.assertTrue(validated["database"]["enable_citus"])

        cfg["database"]["enable_citus"] = False
        validated = validate_config(cfg, profile="regress")
        self.assertFalse(validated["database"]["enable_citus"])

    def test_validation_rejects_non_boolean_enable_citus(self):
        cfg = _base_valid_config()
        cfg["database"]["enable_citus"] = "true"
        with self.assertRaises(ConfigurationError) as ctx:
            validate_config(cfg, profile="regress")
        self.assertIn("database.enable_citus must be a boolean", str(ctx.exception))

    def test_mmr_script_citus_disabled(self):
        cfg = _base_valid_config()
        cfg["database"]["enable_citus"] = False
        env = SimpleNamespace(config=cfg)

        script = build_mmr_setup_script(env)
        self.assertIn("shared_preload_libraries='fdd_mmr'", script)
        self.assertNotIn("citus", script)

    def test_mmr_script_citus_enabled(self):
        cfg = _base_valid_config()
        cfg["database"]["enable_citus"] = True
        env = SimpleNamespace(config=cfg)

        script = build_mmr_setup_script(env)
        self.assertIn("shared_preload_libraries='citus,fdd_mmr'", script)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS citus;", script)
        # Verify extension is added for both postgres and test_db
        self.assertIn('-d test_db -U "$USER" -c "CREATE EXTENSION IF NOT EXISTS citus;"', script)

    def test_replication_script_citus_disabled(self):
        cfg = _base_valid_config()
        cfg["database"]["enable_citus"] = False
        env = SimpleNamespace(config=cfg)

        script = build_replication_setup_script(env)
        self.assertNotIn("shared_preload_libraries = 'citus'", script)
        self.assertNotIn("CREATE EXTENSION IF NOT EXISTS citus;", script)

    def test_replication_script_citus_enabled(self):
        cfg = _base_valid_config()
        cfg["database"]["enable_citus"] = True
        env = SimpleNamespace(config=cfg)

        script = build_replication_setup_script(env)
        self.assertIn("shared_preload_libraries = 'citus'", script)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS citus;", script)


if __name__ == "__main__":
    unittest.main()
