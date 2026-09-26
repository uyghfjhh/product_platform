import tempfile
import unittest
from pathlib import Path

from framework.server_logs import ServerLogCollector
from framework.evidence import CoreCollector


class FakeManager(object):
    def __init__(self, data_dir, paths):
        self.nodes = {"primary": {
            "host": "127.0.0.1", "data_dir": str(data_dir),
        }}
        self.paths = paths

    def query_value(self, node_name, database, sql):
        destination = "csvlog" if "csvlog" in sql else "stderr"
        return self.paths.get(destination, "")

    def is_local(self, unused_host):
        return True


class ServerLogCollectorTest(unittest.TestCase):
    def test_allows_missing_original_log_after_declared_environment_reset(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            log_dir = root / "pgdata" / "log"
            case_dir = root / "case"
            log_dir.mkdir(parents=True)
            case_dir.mkdir()
            normal = log_dir / "postgresql.log"
            normal.write_text("old log\n", encoding="utf-8")
            manager = FakeManager(root / "pgdata", {
                "stderr": "log/postgresql.log",
            })
            collector = ServerLogCollector(manager, "primary", case_dir)
            collector.start()
            normal.unlink()
            self.assertEqual(collector.finish(allow_missing_before=True), [])
            self.assertEqual(collector.errors, [])

    def test_collects_only_content_appended_during_case(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            log_dir = root / "pgdata" / "log"
            case_dir = root / "case"
            log_dir.mkdir(parents=True)
            case_dir.mkdir()
            normal = log_dir / "postgresql.log"
            csv = log_dir / "postgresql.csv"
            normal.write_text("old log\n", encoding="utf-8")
            csv.write_text("old csv\n", encoding="utf-8")
            manager = FakeManager(root / "pgdata", {
                "stderr": "log/postgresql.log", "csvlog": "log/postgresql.csv",
            })
            collector = ServerLogCollector(manager, "primary", case_dir)
            collector.start()
            with normal.open("a", encoding="utf-8") as stream:
                stream.write("case log\n")
            with csv.open("a", encoding="utf-8") as stream:
                stream.write("case csv\n")
            self.assertEqual(collector.finish(), ["postgresql.log", "postgresql.csv"])
            self.assertEqual((case_dir / "postgresql.log").read_text(encoding="utf-8"),
                             "case log\n")
            self.assertEqual((case_dir / "postgresql.csv").read_text(encoding="utf-8"),
                             "case csv\n")


class CoreCollectorTest(unittest.TestCase):
    def test_detects_new_remote_core_path(self):
        class Manager(object):
            nodes = {"remote": {"host": "192.0.2.10", "data_dir": "/pgdata"}}

            @staticmethod
            def is_local(unused_host):
                return False

        class Transport(object):
            def __init__(self):
                self.outputs = ["", "/pgdata/core.123\t100.0\t4096\n"]

            def run(self, node, argv, check=False):
                return type("Process", (), {
                    "returncode": 0, "stdout": self.outputs.pop(0),
                })()

        with tempfile.TemporaryDirectory() as root:
            collector = CoreCollector(Manager(), root, transport=Transport())
            collector.start()
            self.assertEqual(collector.finish(), ["remote:/pgdata/core.123"])

    def test_collects_remote_log_content_through_transport(self):
        class RemoteManager(FakeManager):
            def is_local(self, unused_host):
                return False

        class FakeTransport(object):
            def __init__(self):
                self.sizes = [8, 17]

            def run(self, node, argv, check=False):
                if argv[0] == "stat":
                    return type("Process", (), {
                        "returncode": 0, "stdout": "%s\n" % self.sizes.pop(0),
                    })()
                return type("Process", (), {
                    "returncode": 0, "stdout": "case log\n",
                })()

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            case_dir = root / "case"
            case_dir.mkdir()
            manager = RemoteManager(root / "remote", {"stderr": "log/postgresql.log"})
            manager.nodes["primary"]["host"] = "192.0.2.10"
            collector = ServerLogCollector(
                manager, "primary", case_dir, transport=FakeTransport())
            collector.start()
            self.assertEqual(collector.finish(), ["postgresql.log"])
            self.assertEqual((case_dir / "postgresql.log").read_text(encoding="utf-8"),
                             "case log\n")
