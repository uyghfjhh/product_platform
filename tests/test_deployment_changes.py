import json
from types import SimpleNamespace

import pytest
from platform_app.deployment.change_execution import ChangeExecutor
from platform_app.deployment.workbench import diff_operations


def config():
    return {
        "hosts": {"local": {"address": "127.0.0.1"}},
        "postgresql_installations": {"pg": {"home": "/opt/pgsql"}},
        "instances": {
            "p": {
                "host": "local",
                "installation": "pg",
                "port": 7000,
                "data_dir": "/data/p",
            },
            "s": {
                "host": "local",
                "installation": "pg",
                "port": 7001,
                "data_dir": "/data/s",
            },
        },
        "streaming_clusters": {
            "stream": {
                "primary": "p",
                "standbys": [{"instance": "s", "slot": "physical_s"}],
            }
        },
    }


class Runtime:
    def __init__(self, raw):
        self.config = SimpleNamespace(
            raw=raw,
            instance=lambda n: {
                **raw["instances"][n],
                "installation_config": {"home": "/opt/pgsql"},
                "host_config": {"address": "127.0.0.1"},
            },
        )
        self.calls = []
        self.running = {"p": True, "s": True}
        self.executor = SimpleNamespace(run=self.command)
        self.fail = None

    def command(self, argv, **kwargs):
        self.calls.append(argv)
        if "stop" in argv:
            self.running["s" if "/data/s" in argv else "p"] = False

    def _psql(self, node, sql, **kwargs):
        self.calls.append((node, sql))
        if self.fail and self.fail in sql:
            raise RuntimeError("injected failure")
        if "data_directory" in sql:
            return "/data/" + node + "|" + str(7000 if node == "p" else 7001)
        if "row_to_json(t) FROM" in sql:
            return json.dumps(
                {
                    "name": "max_connections",
                    "setting": "100",
                    "context": "postmaster",
                    "unit": None,
                }
            )
        if "SHOW primary_slot_name" in sql:
            return "physical_s"
        if "SHOW synchronous_standby_names" in sql:
            return ""
        if "slot_type,active" in sql:
            return json.dumps({"slot_type": "physical", "active": False})
        return "0"

    def status_instance(self, node):
        return {"running": self.running[node]}

    def start_instance(self, node):
        self.calls.append(("start", node))
        self.running[node] = True

    def target_instances(self, target):
        return list(self.config.raw["instances"])

    def verify_target(self, target):
        self.calls.append(("health", target))


def test_parameter_restart_checkpoint_resumes_after_interruption(tmp_path):
    raw = config()
    runtime = Runtime(raw)
    changes = {"max_connections": {"from": 100, "to": 200}}
    plan = {
        "id": "plan",
        "target": "streaming.stream",
        "diff": {"parameters": changes, "removed": [], "added": []},
    }
    checkpoint = tmp_path / "state.json"
    runtime.fail = "ALTER SYSTEM"
    executor = ChangeExecutor(plan, raw, runtime, runtime, checkpoint, lambda _: None)
    with pytest.raises(RuntimeError):
        executor.run()
    assert (
        json.loads(checkpoint.read_text())["settings"]["p"]["max_connections"][
            "setting"
        ]
        == "100"
    )
    runtime.fail = None
    executor = ChangeExecutor(plan, raw, runtime, runtime, checkpoint, lambda _: None)
    executor.run()
    assert json.loads(checkpoint.read_text())["status"] == "APPLIED"
    restart_order = [
        call[1]
        for call in runtime.calls
        if isinstance(call, tuple) and call[0] == "start"
    ]
    assert restart_order == ["s", "p"]
    before = len(runtime.calls)
    ChangeExecutor(plan, raw, runtime, runtime, checkpoint, lambda _: None).run()
    assert all("ALTER SYSTEM" not in str(c) for c in runtime.calls[before:])


def test_shrink_stops_before_releasing_physical_slot_and_keeps_data(tmp_path):
    raw = config()
    desired = config()
    del desired["instances"]["s"]
    desired["streaming_clusters"]["stream"]["standbys"] = []
    diff, ops, executable = diff_operations(raw, desired)
    assert (
        executable
        and next(op for op in ops if op["kind"] == "remove_node")["executable"]
    )
    old = Runtime(raw)
    new = Runtime(desired)
    ChangeExecutor(
        {"id": "shrink", "target": "streaming.stream", "diff": diff},
        raw,
        new,
        old,
        tmp_path / "state.json",
    ).run()
    calls = [str(c) for c in old.calls]
    assert next(i for i, c in enumerate(calls) if "'stop'" in c) < next(
        i for i, c in enumerate(calls) if "pg_drop_replication_slot" in c
    )
    assert not any("remove_tree" in c or "rm " in c for c in calls)


def test_structural_parameters_and_primary_removal_remain_blocked():
    raw = config()
    desired = config()
    desired["postgresql_config"] = {"parameters": {"port": 8888}}
    assert not diff_operations(raw, desired)[2]
    desired = config()
    del desired["instances"]["p"]
    assert not diff_operations(raw, desired)[2]


def test_standby_migration_requires_independent_slot_and_allows_new_host():
    from copy import deepcopy

    raw = config()
    desired = deepcopy(raw)
    desired["hosts"]["remote"] = {
        "address": "192.168.0.15",
        "ssh": {"user": "postgres"},
    }
    desired["instances"]["s"].update(host="remote", data_dir="/new/data/s")
    assert not diff_operations(raw, desired)[2]
    desired["streaming_clusters"]["stream"]["standbys"][0]["slot"] = "migrated_s"
    diff, operations, allowed = diff_operations(raw, desired)
    assert allowed
    assert next(row for row in operations if row["kind"] == "change_node")["executable"]
    assert next(row for row in operations if row["kind"] == "host")["executable"]
    assert diff["changed"][0]["name"] == "s"


def test_unknown_shutdown_status_never_retires_node(tmp_path):
    runtime = Runtime(config())
    runtime.status_instance = lambda _: {"running": False, "known": False}
    executor = ChangeExecutor(
        {"id": "unknown"}, config(), runtime, runtime, tmp_path / "state.json"
    )
    with pytest.raises(ValueError, match="运行状态"):
        executor.stop(runtime, "s")
    assert runtime.calls == []


def test_resume_after_restart_stopped_but_start_interrupted(tmp_path):
    runtime = Runtime(config())
    original = runtime.start_instance

    def fail(node):
        raise RuntimeError("interrupted after shutdown")

    runtime.start_instance = fail
    plan = {
        "id": "restart-recovery",
        "target": "streaming.stream",
        "diff": {
            "parameters": {"max_connections": {"from": 100, "to": 200}},
            "added": [],
            "removed": [],
        },
    }
    path = tmp_path / "checkpoint.json"
    with pytest.raises(RuntimeError):
        ChangeExecutor(plan, config(), runtime, runtime, path).run()
    assert runtime.running["s"] is False
    runtime.start_instance = original
    ChangeExecutor(plan, config(), runtime, runtime, path).run()
    assert runtime.running["s"] and runtime.running["p"]
    assert json.loads(path.read_text())["status"] == "APPLIED"


def test_last_synchronous_standby_cannot_be_silently_retired(tmp_path):
    raw = config()
    desired = config()
    del desired["instances"]["s"]
    desired["streaming_clusters"]["stream"]["standbys"] = []
    old = Runtime(raw)
    new = Runtime(desired)
    original = old._psql
    old._psql = lambda node, sql, **kwargs: (
        "ANY 1 (s)"
        if sql == "SHOW synchronous_standby_names"
        else original(node, sql, **kwargs)
    )
    diff, _, _ = diff_operations(raw, desired)
    with pytest.raises(ValueError, match="同步复制"):
        ChangeExecutor(
            {"id": "sync", "target": "streaming.stream", "diff": diff},
            raw,
            new,
            old,
            tmp_path / "state.json",
        ).run()
    assert old.running["s"]
    assert not any("pg_drop_replication_slot" in str(c) for c in old.calls)


def test_local_interface_aliases_share_the_same_resource_identity(monkeypatch):
    from platform_app.resources import canonical_host
    monkeypatch.setattr('platform_app.resources.local_addresses',lambda: {'127.0.0.1','192.168.0.12','::1'})
    assert canonical_host('192.168.0.12')==canonical_host('127.0.0.1')=='local'
    assert canonical_host('127.0.0.2')=='local'
    assert canonical_host('192.168.0.15')=='192.168.0.15'
