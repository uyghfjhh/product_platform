import unittest
from types import SimpleNamespace
from pathlib import Path

from pgclusterlib.config import load
from pgclusterlib.errors import OperationError
from pgclusterlib.runtime import Runtime


class Result:
    returncode = 0
    stdout = ""
    stderr = ""


class PluginExecutor:
    def __init__(self):
        self.installed = set()
        self.commands = []

    @staticmethod
    def is_local(address):
        return address == "127.0.0.1"

    def exists(self, host, path):
        if path.endswith("/bin/pg_config"):
            return True
        return path in self.installed

    @staticmethod
    def is_dir(host, path):
        return path.startswith("/home/postgres/")

    def run(self, args, host="local", **kwargs):
        self.commands.append((host, args))
        if args[:2] == ["sudo", "-n"]:
            source = args[4]
            extension = {
                "fbase_mac": "fbase_mac",
                "fdd_mmr": "fdd_mmr",
                "fb_license": "fb_license",
            }[Path(source).name]
            self.installed.add("/usr/local/fbase15.15/share/extension/%s.control" % extension)
        return Result()


class ManagedStoppedExecutor(PluginExecutor):
    def __init__(self):
        super().__init__()
        self.writes = {}

    def exists(self, host, path):
        return path.endswith(".pgcluster-managed")

    def is_nonempty_dir(self, host, path):
        return True

    def run(self, args, host="local", **kwargs):
        self.commands.append((host, args))
        result = Result()
        if len(args) > 1 and args[1] == "status":
            result.returncode = 3
        return result

    def write_text(self, host, path, content, mode="0600"):
        self.writes[path] = content

    def read_text(self, host, path, check=True):
        return ""

    def append_text(self, host, path, content):
        self.writes[path] = self.writes.get(path, "") + content


class RuntimeInstallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load(Path(__file__).parents[1] / "backend" / "pgcluster" / "pgcluster.yaml")

    def test_install_builds_missing_fbase_plugins(self):
        executor = PluginExecutor()
        result = Runtime(self.config, executor).install_installation("fbase15")
        fbase_host = self.config.hosts["fbase15_host"]["address"]
        self.assertIn("fbase_mac@%s: 已安装" % fbase_host, result)
        self.assertEqual(len([args for _, args in executor.commands if args[0] == "make"]), 3)
        self.assertEqual(len([args for _, args in executor.commands if args[:2] == ["sudo", "-n"]]), 3)

    def test_install_reports_missing_plugin_without_source(self):
        with self.assertRaisesRegex(OperationError, "未配置 source_dir"):
            Runtime(self.config, PluginExecutor()).install_installation("postgresql17")

    def test_create_streaming_creates_declared_extensions_on_primary(self):
        cluster = {"primary": "node1", "standbys": [],
                   "extensions": ["pg_deltax", "timescaledb"]}
        instance = {
            "host_config": {"address": "127.0.0.1"}, "port": 15432,
            "data_dir": "/d", "installation_config": {"home": "/h", "plugins": {}}}
        config = SimpleNamespace(
            streaming_clusters={"ts": cluster}, hosts={},
            instance=lambda name: instance)
        runtime = Runtime(config, executor=PluginExecutor())
        runtime._init_primary = lambda *args, **kwargs: None
        runtime._wait = lambda *args, **kwargs: None
        runtime._state = lambda: {"primaries": {}}
        queries = []
        runtime._psql = lambda name, sql, database="postgres", tuples=False: (
            queries.append((name, sql, database)) or "")
        runtime.create_streaming("ts")
        creates = [(name, sql, database) for name, sql, database in queries
                   if sql.startswith("CREATE EXTENSION")]
        self.assertEqual(creates, [
            ("node1", 'CREATE EXTENSION IF NOT EXISTS "pg_deltax"', "postgres"),
            ("node1", 'CREATE EXTENSION IF NOT EXISTS "timescaledb"', "postgres"),
        ])

    def test_create_streaming_creates_databases_with_extensions(self):
        cluster = {"primary": "node1", "standbys": [],
                   "databases": {"db_plain": {},
                                 "db_deltax": {"extensions": ["pg_deltax"]},
                                 "db_ts": ["timescaledb"]}}
        instance = {
            "host_config": {"address": "127.0.0.1"}, "port": 15432,
            "data_dir": "/d", "installation_config": {"home": "/h", "plugins": {}}}
        config = SimpleNamespace(
            streaming_clusters={"ts": cluster}, hosts={},
            instance=lambda name: instance)
        runtime = Runtime(config, executor=PluginExecutor())
        runtime._init_primary = lambda *args, **kwargs: None
        runtime._wait = lambda *args, **kwargs: None
        runtime._state = lambda: {"primaries": {}}
        queries = []
        runtime._psql = lambda name, sql, database="postgres", tuples=False: (
            queries.append((sql, database)) or "")
        runtime.create_streaming("ts")
        self.assertIn(('CREATE DATABASE "db_plain"', "postgres"), queries)
        self.assertIn(('CREATE DATABASE "db_deltax"', "postgres"), queries)
        self.assertIn(('CREATE DATABASE "db_ts"', "postgres"), queries)
        self.assertIn(
            ('CREATE EXTENSION IF NOT EXISTS "pg_deltax"', "db_deltax"), queries)
        self.assertIn(
            ('CREATE EXTENSION IF NOT EXISTS "timescaledb"', "db_ts"), queries)

    def test_create_streaming_skips_existing_database(self):
        cluster = {"primary": "node1", "standbys": [],
                   "databases": {"db_plain": None}}
        instance = {
            "host_config": {"address": "127.0.0.1"}, "port": 15432,
            "data_dir": "/d", "installation_config": {"home": "/h", "plugins": {}}}
        config = SimpleNamespace(
            streaming_clusters={"ts": cluster}, hosts={},
            instance=lambda name: instance)
        runtime = Runtime(config, executor=PluginExecutor())
        runtime._init_primary = lambda *args, **kwargs: None
        runtime._wait = lambda *args, **kwargs: None
        runtime._state = lambda: {"primaries": {}}
        queries = []
        runtime._psql = lambda name, sql, database="postgres", tuples=False: (
            queries.append(sql) or ("1" if "pg_database" in sql else ""))
        runtime.create_streaming("ts")
        self.assertFalse(any(sql.startswith("CREATE DATABASE") for sql in queries))

    def test_create_streaming_rejects_missing_required_plugin(self):
        install = {"home": "/h", "plugins": {
            "pg_deltax": {"required": True, "extension": "pg_deltax"}}}
        cluster = {"primary": "node1", "standbys": []}
        instance = {
            "host_config": {"address": "127.0.0.1"}, "installation_config": install}
        config = SimpleNamespace(
            streaming_clusters={"ts": cluster}, hosts={},
            instance=lambda name: instance)
        runtime = Runtime(config, executor=PluginExecutor())
        runtime._init_primary = lambda *args, **kwargs: self.fail("initdb 不应执行")
        runtime._state = lambda: {"primaries": {}}
        with self.assertRaisesRegex(OperationError, "pg_deltax.*未安装"):
            runtime.create_streaming("ts")

    def test_create_streaming_rejects_missing_preload_library(self):
        install = {"home": "/h", "plugins": {
            "pg_deltax": {"required": True, "extension": "pg_deltax",
                          "preload_library": "pg_deltax"}}}
        cluster = {"primary": "node1", "standbys": []}
        instance = {
            "host_config": {"address": "127.0.0.1"}, "installation_config": install}
        config = SimpleNamespace(
            streaming_clusters={"ts": cluster}, hosts={},
            instance=lambda name: instance)
        executor = PluginExecutor()
        executor.installed.add("/h/share/extension/pg_deltax.control")
        runtime = Runtime(config, executor=executor)
        runtime._init_primary = lambda *args, **kwargs: self.fail("initdb 不应执行")
        runtime._state = lambda: {"primaries": {}}
        with self.assertRaisesRegex(OperationError, "缺少预加载库"):
            runtime.create_streaming("ts")

    def test_retry_rewrites_managed_primary_config_after_start_failure(self):
        executor = ManagedStoppedExecutor()
        Runtime(self.config, executor)._init_primary("basic_primary_node")
        self.assertTrue(any(path.endswith("/pgcluster.conf") for path in executor.writes))
        self.assertTrue(any(args[1] == "start" for _, args in executor.commands))

    def test_replication_uses_loopback_only_on_same_database_host(self):
        nodes = {'primary': {'host_config': {'address': '192.168.1.24'}, 'port': 15432},
                 'local_standby': {'host_config': {'address': '192.168.1.24'}},
                 'remote_standby': {'host_config': {'address': '192.168.1.25'}}}
        config = SimpleNamespace(hosts={}, instance=nodes.__getitem__, streaming_clusters={
            'group': {'standbys': [{'instance': 'local_standby', 'application_name': 'pg_3'},
                                  {'instance': 'remote_standby', 'application_name': 'pg_4'}]}})
        runtime = Runtime(config)
        self.assertEqual(runtime._replication_host('primary', 'local_standby'), '127.0.0.1')
        self.assertEqual(runtime._replication_host('primary', 'remote_standby'), '192.168.1.24')
        runtime._psql = lambda *args, **kwargs: 'host=127.0.0.1 port=15432 application_name=pg_3'
        self.assertTrue(runtime._upstream_matches('group', 'local_standby', 'primary'))
        runtime._psql = lambda *args, **kwargs: 'host=127.0.0.1 port=15432 application_name=pg_4'
        self.assertFalse(runtime._upstream_matches('group', 'remote_standby', 'primary'))


if __name__ == "__main__":
    unittest.main()
