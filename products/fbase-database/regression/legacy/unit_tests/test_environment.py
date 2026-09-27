import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from framework.config import RegressionConfig
from framework.environment import EnvironmentManager


class MmrTopologyTest(unittest.TestCase):
    def make_manager(self, root):
        data = {
            "postgres": {"home": "/opt/pg", "license_file": "/tmp/license.dat"},
            "clusters": {
                "mmr": {
                    "plugins": {
                        "fb_license": {"preload": False},
                        "fdd_mmr": {"enabled_databases": ["postgres"]},
                    },
                    "nodes": {
                        "n1": {"host": "127.0.0.1", "port": 10011,
                               "data_dir": str(Path(root) / "n1")},
                        "n1s": {"host": "127.0.0.1", "port": 10012,
                                "data_dir": str(Path(root) / "n1s")},
                        "n2": {"host": "127.0.0.1", "port": 10021,
                               "data_dir": str(Path(root) / "n2")},
                        "n2s": {"host": "127.0.0.1", "port": 10022,
                                "data_dir": str(Path(root) / "n2s")},
                    },
                    "groups": {
                        "mmr": {
                            "group_name": "test_mmr",
                            "members": {
                                "m1": {"primary": "n1", "standbys": ["n1s"]},
                                "m2": {"primary": "n2", "standbys": ["n2s"]},
                            },
                        }
                    },
                }
            },
        }
        return EnvironmentManager(RegressionConfig(root, data), "mmr")

    def test_mmr_primary_standby_relations(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            self.assertEqual(manager.physical_relations(), [("n1", "n1s"), ("n2", "n2s")])
            self.assertEqual(manager.physical_standbys(), {"n1s", "n2s"})

    def test_fb_license_is_not_preloaded(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            settings = manager.effective_settings("n1")
            self.assertEqual(settings["shared_preload_libraries"], ["fdd_mmr"])
            self.assertEqual(settings["fdd.running_databases"], ["postgres"])

    def test_mmr_roles(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            self.assertEqual(manager.roles()["n1"], "mmr_primary:m1")
            self.assertEqual(manager.roles()["n2s"], "mmr_standby:m2")

    def test_lock_identity_uses_physical_nodes_not_node_aliases(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            original = manager.lock_identity()
            manager.nodes = {
                "renamed_primary": manager.nodes["n1"],
                "renamed_standby": manager.nodes["n1s"],
                "other_primary": manager.nodes["n2"],
                "other_standby": manager.nodes["n2s"],
            }
            self.assertEqual(manager.lock_identity(), original)

    def test_mmr_status_returns_one_row_per_group(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            udf_rows = [
                {"nodename": "m1", "is_abnormal": "OK", "detail": "OK",
                 "nodestate": "ACTIVE", "real_nodestate": "ACTIVE"},
                {"nodename": "m2", "is_abnormal": "OK", "detail": "OK",
                 "nodestate": "ACTIVE", "real_nodestate": "ACTIVE"},
            ]
            with patch.object(manager, "_mmr_udf_check", return_value=udf_rows):
                self.assertEqual(manager.mmr_status_rows(), [
                    ("test_mmr", 2, "healthy", "all member checks passed"),
                ])

    def test_mmr_cluster_status_uses_common_group_format(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            with patch.object(manager, "mmr_status_rows", return_value=[
                    ("test_mmr", 2, "healthy", "all member checks passed")]):
                self.assertEqual(manager.cluster_status_rows({"state": "running"}), [
                    ("mmr", "test_mmr", "healthy", "members=2"),
                ])

    def test_node_health_checks_process_and_primary_standby_role(self):
        self.assertEqual(
            EnvironmentManager._node_health("mmr_primary:m1", "running", "false"),
            "healthy")
        self.assertEqual(
            EnvironmentManager._node_health("mmr_standby:m1", "running", "true"),
            "healthy")
        self.assertEqual(
            EnvironmentManager._node_health("mmr_standby:m1", "running", "false"),
            "unhealthy")
        self.assertEqual(
            EnvironmentManager._node_health("primary", "stopped", "-"),
            "unhealthy")

    def test_mmr_udf_health_requires_ok_and_active_states(self):
        healthy = {"is_abnormal": "OK", "nodestate": "ACTIVE",
                   "real_nodestate": "ACTIVE"}
        abnormal = dict(healthy, is_abnormal="ERR_NODE_STATE")
        inactive = dict(healthy, real_nodestate="CREATED")
        self.assertTrue(EnvironmentManager._mmr_rows_healthy([healthy]))
        self.assertFalse(EnvironmentManager._mmr_rows_healthy([abnormal]))
        self.assertFalse(EnvironmentManager._mmr_rows_healthy([inactive]))

    def test_environment_query_uses_bounded_timeout(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            calls = []

            def run(command, **options):
                calls.append((command, options))
                return SimpleNamespace(stdout="ok\n")

            manager.runner = SimpleNamespace(run=run)
            self.assertEqual(manager._query_value("n1", "postgres", "SELECT 'ok'"), "ok")
            self.assertEqual(calls[0][1]["timeout"], 5)


class MacStatusTest(unittest.TestCase):
    def test_missing_required_command_blocks_with_install_hint(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            with patch("framework.environment.shutil.which", return_value=None):
                reason = manager.check_requirements({"commands": ["script"]})
        self.assertEqual(reason, "缺少命令: script；请安装 util-linux（提供 script）")

    def make_manager(self, root):
        data = {
            "postgres": {"home": "/opt/pg", "license_file": "/tmp/license.dat"},
            "clusters": {
                "mac": {
                    "plugins": {
                        "fb_license": {"preload": False},
                        "fbase_mac": {},
                    },
                    "nodes": {
                        "primary": {"host": "127.0.0.1", "port": 15432,
                                    "data_dir": str(Path(root) / "primary")},
                        "standby": {"host": "127.0.0.1", "port": 15433,
                                    "data_dir": str(Path(root) / "standby")},
                        "subscriber": {"host": "127.0.0.1", "port": 15434,
                                       "data_dir": str(Path(root) / "subscriber")},
                    },
                    "groups": {
                        "streaming": {"primary": "primary", "standbys": ["standby"]},
                        "logical": {
                            "database": "postgres", "publisher": "primary",
                            "publication_name": "test_pub",
                            "subscribers": {
                                "subscriber": {"subscription_name": "test_sub",
                                               "slot_name": "test_slot"},
                            },
                        },
                    },
                }
            },
        }
        return EnvironmentManager(RegressionConfig(root, data), "mac")

    def test_streaming_status_reports_replay_lag(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            with patch.object(manager, "_query_value", return_value="1|2048"):
                self.assertEqual(manager.streaming_status_rows(), [
                    ("streaming", "2.0 KiB", "healthy", "OK"),
                ])

    def test_logical_status_reports_slot_lag(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)

            def query(node, database, sql):
                return "1|1024" if "pg_replication_slots" in sql else "1"

            with patch.object(manager, "_query_value", side_effect=query):
                self.assertEqual(manager.logical_status_rows(), [
                    ("logical", "1.0 KiB", "healthy", "OK"),
                ])

    def test_plugin_health_checks_extension_and_preload(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            with patch.object(manager, "_query_value",
                              side_effect=["2", "fbase_mac"]):
                self.assertTrue(manager._plugins_healthy("primary"))

    def test_mac_cluster_status_combines_replication_groups(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            with patch.object(manager, "streaming_status_rows", return_value=[
                    ("streaming", "2.0 KiB", "healthy", "OK")]), \
                    patch.object(manager, "logical_status_rows", return_value=[
                        ("logical", "1.0 KiB", "healthy", "OK")]):
                self.assertEqual(manager.cluster_status_rows({"state": "running"}), [
                    ("mac", "streaming", "healthy", "max_replay_lag=2.0 KiB"),
                    ("mac", "logical", "healthy", "max_slot_lag=1.0 KiB"),
                ])

    def test_stopped_environment_has_one_unhealthy_cluster_row(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            self.assertEqual(manager.cluster_status_rows({"state": "stopped"}), [
                ("mac", "-", "unhealthy", "environment=stopped"),
            ])

    def test_requirement_settings_report_actual_values(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            requirements = {"settings": [
                {"name": "shared_preload_libraries", "contains": "fbase_mac",
                 "purpose": "加载插件"},
                {"name": "fdb.separate_user", "equals": "on",
                 "purpose": "启用三权分立"},
            ]}
            class Client(object):
                def check_setting(self, node, spec):
                    actual = "fbase_mac" if spec["name"] == "shared_preload_libraries" else "on"
                    return {
                        "node": node, "name": spec["name"], "actual": actual,
                        "matched": True, "requirement": "test",
                        "purpose": spec["purpose"], "sql": "SHOW " + spec["name"],
                        "output": actual, "error": "", "source": "requirement",
                    }
            blocker, details = manager.evaluate_requirements(
                requirements, postgres_client=Client())
            self.assertEqual(blocker, "")
            self.assertEqual([item["actual"] for item in details], ["fbase_mac", "on"])
            self.assertTrue(all(item["matched"] for item in details))

    def test_requirement_setting_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            requirements = {"settings": [{
                "name": "fdb.separate_user", "equals": "on",
                "purpose": "启用三权分立",
            }]}
            class Client(object):
                def check_setting(self, node, spec):
                    return {
                        "node": node, "name": spec["name"], "actual": "off",
                        "matched": False, "requirement": "等于 on",
                        "purpose": spec["purpose"], "sql": "SHOW " + spec["name"],
                        "output": "off", "error": "", "source": "requirement",
                    }
            blocker, details = manager.evaluate_requirements(
                requirements, postgres_client=Client())
            self.assertIn("实际=off", blocker)
            self.assertFalse(details[0]["matched"])

    def test_system_time_requirement_blocks_without_sudo(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            manager.runner = SimpleNamespace(
                run=lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="sudo denied"))
            manager.status_rows = lambda: ({}, [
                ("primary", "primary", "127.0.0.1", 15432, "running", "false",
                 "healthy", "plugins", "/tmp/primary"),
            ])
            blocker, unused_details = manager.evaluate_requirements({
                "node": "primary", "writable_node": True, "system_time_control": True,
            })
            self.assertIn("sudo", blocker)
