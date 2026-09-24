import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess

from env.ownership import (
    EnvironmentOwnershipError, build_cleanup_plan, cleanup_script,
)
from env.cluster import ClusterManager
from framework.configuration.loader import RegressionConfig
from framework.execution.locking import ExclusiveFileLock


def environment(database, profile="stable", root=Path("/repo")):
    return RegressionConfig(root, {
        "database": database,
        "framework": {"output_dir": "output"},
    }, profile=profile)


class EnvironmentOwnershipTest(unittest.TestCase):
    def test_plan_keeps_replication_binary_and_data_root_separate(self):
        plan = build_cleanup_plan(environment({
            "mmr_host": "mmr", "mmr_pg_user": "postgres",
            "mmr_postgres_dir": "/opt/mmr", "mmr_data_root": "/data/stable/mmr",
            "rep_host": "rep", "rep_pg_user": "postgres",
            "rep_postgres_dir": "/opt/rep", "rep_data_root": "/data/stable/rep",
        }))

        rep_scope = [scope for scope in plan.scopes if scope.nodes[0].topology == "rep"][0]
        script = cleanup_script(rep_scope)
        self.assertIn("/opt/rep/bin/pg_ctl", script)
        self.assertNotIn("/opt/mmr/bin/pg_ctl", script)
        self.assertIn("ownership marker missing or mismatched", script)
        self.assertEqual(11, len(plan.nodes))

    def test_plan_rejects_broad_or_relative_roots(self):
        database = {
            "mmr_host": "db", "mmr_pg_user": "postgres", "mmr_postgres_dir": "/opt/pg",
            "rep_host": "db", "rep_pg_user": "postgres", "rep_postgres_dir": "/opt/pg",
            "mmr_data_root": "/", "rep_data_root": "relative",
        }
        with self.assertRaises(EnvironmentOwnershipError):
            build_cleanup_plan(environment(database))

    def test_plan_token_changes_between_stable_and_regression(self):
        database = {
            "mmr_host": "db", "mmr_pg_user": "postgres", "mmr_postgres_dir": "/opt/pg",
            "rep_host": "db", "rep_pg_user": "postgres", "rep_postgres_dir": "/opt/pg",
            "mmr_data_root": "/data/mmr", "rep_data_root": "/data/rep",
        }
        stable = build_cleanup_plan(environment(database, "stable"))
        regression = build_cleanup_plan(environment(database, "regression"))
        self.assertNotEqual(stable.scopes[0].token, regression.scopes[0].token)

    def test_destructive_environment_operation_refuses_an_existing_lock(self):
        with self.assertRaises(RuntimeError):
            with TemporaryDirectory() as directory:
                env = environment({
                    "mmr_host": "db", "mmr_pg_user": "postgres", "mmr_postgres_dir": "/opt/pg",
                    "rep_host": "db", "rep_pg_user": "postgres", "rep_postgres_dir": "/opt/pg",
                }, root=Path(directory))
                manager = ClusterManager(env, verbose=False)
                with ExclusiveFileLock(env.env_output_dir / "environment.lock", "test"):
                    manager.clean()

    def test_cleanup_script_refuses_missing_marker_before_any_delete(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plan = build_cleanup_plan(environment({
                "mmr_host": "db", "mmr_pg_user": "postgres", "mmr_postgres_dir": "/opt/pg",
                "mmr_data_root": str(root / "mmr"),
                "rep_host": "db", "rep_pg_user": "postgres", "rep_postgres_dir": "/opt/pg",
                "rep_data_root": str(root / "rep"),
            }))
            scope = plan.scopes[0]
            protected = Path(scope.nodes[0].pgdata)
            protected.mkdir(parents=True)
            result = subprocess.run(
                ["bash", "-c", cleanup_script(scope)], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, universal_newlines=True,
            )
            protected_exists = protected.exists()

        self.assertEqual(73, result.returncode)
        self.assertTrue(protected_exists)
        self.assertIn("ownership marker", result.stderr)

    def test_cleanup_script_rejects_a_symlinked_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            real_root = root / "real"
            real_root.mkdir()
            linked_root = root / "linked"
            linked_root.symlink_to(real_root, target_is_directory=True)
            plan = build_cleanup_plan(environment({
                "mmr_host": "db", "mmr_pg_user": "postgres", "mmr_postgres_dir": "/opt/pg",
                "mmr_data_root": str(linked_root),
                "rep_host": "db", "rep_pg_user": "postgres", "rep_postgres_dir": "/opt/pg",
                "rep_data_root": str(root / "rep"),
            }))
            scope = [item for item in plan.scopes if item.data_root == str(linked_root)][0]
            result = subprocess.run(
                ["bash", "-c", cleanup_script(scope)], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, universal_newlines=True,
            )

        self.assertEqual(76, result.returncode)
        self.assertIn("must not resolve through a symlink", result.stderr)

    def test_topology_scripts_use_strict_error_handling(self):
        root = Path(__file__).resolve().parents[1]
        for relative, prefix in (("env/mmr.py", "$MMR_POSTGRES_DIR"),
                                 ("env/replication.py", "$REP_POSTGRES_DIR")):
            text = (root / relative).read_text(encoding="utf-8")
            self.assertIn("set -euo pipefail", text)
            if relative == "env/mmr.py":
                self.assertIn('"$PG/bin/psql" -v ON_ERROR_STOP=1', text)
            else:
                self.assertNotIn(prefix + "/bin/psql -p", text)
                self.assertIn(prefix + "/bin/psql -v ON_ERROR_STOP=1", text)
