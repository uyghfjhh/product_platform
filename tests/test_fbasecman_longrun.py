"""The upstream profile must run the explicit selection with the requested time."""


import pytest

from products.fbasecman.longrun import (
    pgbench_commands,
    run,
    selected_workloads,
    suite_directory,
)


@pytest.fixture
def suite(tmp_path):
    suite = tmp_path / "test" / "7x24小时长稳测试"
    pgbench = suite / "pgbench"
    jdbc = suite / "JDBC"
    pgbench.mkdir(parents=True)
    (suite / "contrib").mkdir()
    for asset in ("test.conf.bak", "userlist.conf", "set_stabilize.sh", "create_procdure.sql",
                  "contrib/pg_cron-main.zip"):
        (suite / asset).touch()
    jdbc.mkdir()
    for name in ("config.properties", "session_monitor.properties", "test_Jdbc.sh", "test_Jdbc.java", "SessionMonitor.java"):
        (jdbc / name).touch()
    (pgbench / "call_testbench.sh.bak").write_text(
        """    PGPASSWORD='example password' nohup pgbench "$@" &
start_pgbench pgbench4 -h FBASECMAN_IP -p WRITE_PORT mmrport -f test4.sql -U mmrport -C -c 5 -j 1 -T 2592000 || exit 1
start_pgbench pgbench10 -h FBASECMAN_IP -p FBASECMAN_PORT console -f test10.sql -U admin -c 1 -j 1 -T 2592000 || exit 1
"""
    )
    for number in range(1, 10):
        (pgbench / f"test{number}.sql").touch()
    (pgbench / "test10.sql").write_text("RELOAD;\n")
    return suite


def test_default_duration_and_full_upstream_selection():
    assert selected_workloads({}) == (
        600, [*[f"pgbench{i}" for i in range(1, 11)], "jdbc", "session_monitor"],
    )
    assert selected_workloads({"duration_seconds": 90, "enabled_workloads": ["pgbench4"]}) == (
        90, ["pgbench4"],
    )
    for selection in ([], ["stable.pgbench.ha_node_state"], ["pgbench4", "pgbench4"]):
        with pytest.raises(ValueError, match="请选择"):
            selected_workloads({"enabled_workloads": selection})


def test_profile_and_pgbench_arguments_preserve_upstream_settings(tmp_path, suite):
    with pytest.raises(ValueError, match="缺少常稳资产"):
        suite_directory(tmp_path / "missing")
    assert suite_directory(tmp_path) == suite
    commands = pgbench_commands(suite, 600, ["pgbench4"], 7000, 7001)
    assert commands == {"pgbench4": [
        "-h", "127.0.0.1", "-p", "7001", "mmrport", "-f", "test4.sql",
        "-U", "mmrport", "-C", "-c", "5", "-j", "1", "-T", "600",
    ]}
    with pytest.raises(ValueError, match="缺少"):
        pgbench_commands(suite, 600, ["pgbench9"], 7000, 7001)


def test_selected_workload_runs_and_preserves_original_sql(tmp_path, suite):
    binary = tmp_path / "pgbench"
    binary.write_text('#!/bin/sh\ncat test10.sql\necho "number of transactions actually processed: 5"\nprintf "%s\\n" "$@"\n')
    binary.chmod(0o755)
    output = tmp_path / "output"
    run(suite, output, 1, ["pgbench10"], 7000, 7001, "127.0.0.1", 5432, binary)
    assert (output / "pgbench10.log").read_text().splitlines()[-2:] == ["-T", "1"]
    assert (output / "pgbench10.log").read_text().splitlines()[0] == "RELOAD;"
    assert sorted(path.name for path in output.glob("*.log")) == ["pgbench10.log"]


def test_database_settings_and_member_fixtures_are_read_from_upstream(suite):
    from products.fbasecman.longrun_environment import (
        settings_from_upstream,
        sql_fixture_from_upstream,
    )

    (suite / "set_stabilize.sh").write_text(r'''EXCLUDE_SCHEMA="fdd,cron,jdbc_parse_test"
MMR1_PRELOAD="fdd_mmr,pg_cron"
MMR2_PRELOAD="fdd_mmr"
set_mmr1()
{
    echo \"listen_addresses = '*'
            port = $MMR1_PORT
            shared_preload_libraries = '$MMR1_PRELOAD'
            fdd.exclude_schema = '$EXCLUDE_SCHEMA'
            shared_buffers = 2GB \" >> $MMR1PGDATA/postgresql.conf
    CREATE TABLE test(id int primary key);
    insert into test values(1);
    CREATE USER sql_parse PASSWORD 'fixture';
}
set_mmr2()
{
    echo \"listen_addresses = '*'
            port = $MMR2_PORT
            shared_preload_libraries = '$MMR2_PRELOAD'
            fdd.exclude_schema = '$EXCLUDE_SCHEMA'
            shared_buffers = 2GB \" >> $MMR2PGDATA/postgresql.conf
    CREATE TABLE test(id int primary key);
    CREATE USER sql_parse PASSWORD 'fixture';
}
init_sql_parse_stability_data()
''')
    parameters = settings_from_upstream(suite)
    assert parameters["mmr1"] == [
        "shared_preload_libraries = 'fdd_mmr,pg_cron'",
        "fdd.exclude_schema = 'fdd,cron,jdbc_parse_test'",
        "shared_buffers = 2GB",
    ]
    assert parameters["mmr2"][0] == "shared_preload_libraries = 'fdd_mmr'"
    assert "insert into test values(1);" in sql_fixture_from_upstream(suite, "mmr1")
    assert "insert into test" not in sql_fixture_from_upstream(suite, "mmr2")


def test_memory_report_uses_two_nonempty_snapshots_from_same_pid(tmp_path, monkeypatch):
    import json

    from products.fbasecman import longrun_memory

    monkeypatch.setattr(longrun_memory, "tools_for", lambda _: (tmp_path / "jemalloc.so", "jeprof", "dot"))

    def probe(_argv, *, env, **_options):
        from pathlib import Path

        prefix = Path(env["MALLOC_CONF"].split("prof_prefix:", 1)[1])
        prefix.with_suffix(".100.0.f.heap").write_bytes(b"heap")

    monkeypatch.setattr(longrun_memory.subprocess, "run", probe)
    output = tmp_path / "memory"
    binary = tmp_path / "fbasecman"
    binary.write_bytes(b"symbols")
    profile = longrun_memory.MemoryProfile(tmp_path, output, binary, 30)
    profile.pid = 321
    profile.rows = [(0, 4096), (5, 5120)]
    for name in ("heap.321.10.i1.heap", "heap.321.2.i0.heap", "heap.900.1.i0.heap", "heap.321.11.f.heap"):
        (output / name).write_text("heap")
    (output / "heap.321.12.i2.heap").touch()
    seen = []
    monkeypatch.setattr(profile, "diff", lambda base, later: seen.append((base.name, later.name)))
    profile.finish()
    assert seen == [("heap.321.2.i0.heap", "heap.321.10.i1.heap")]
    result = json.loads((output / "summary.json").read_text())
    assert result["rss_change_mib"] == 1
    assert result["snapshots"] == 2
    assert "<polyline" in (output / "rss.svg").read_text()

    binary.write_bytes(b"recompiled")
    profile.finish()
    result = json.loads((output / "summary.json").read_text())
    assert "二进制发生变更" in result["issue"]
    assert len(seen) == 1


def test_heap_comparison_ranks_complete_stacks_and_merges_shared_prefix(tmp_path):
    from products.fbasecman.longrun_heap import collapsed_stacks, compare, write_json

    samples = [
        collapsed_stacks("main;left;malloc 20\nmain;right;malloc 60\nmain;reduced;calloc 100"),
        collapsed_stacks("main;left;malloc 80\nmain;right;malloc 65\nmain;reduced;calloc 30"),
        collapsed_stacks("main;left;malloc 100\nmain;left;malloc 20\nmain;right;malloc 70"),
    ]
    write_json(tmp_path / "timeline.json", {"issue": None, "samples": [
        {"id": str(index), "elapsed": 60 * index, "bytes": sum(row["bytes"] for row in sample.values())}
        for index, sample in enumerate(samples)
    ]})
    for index, sample in enumerate(samples):
        write_json(tmp_path / f"stacks-{index}.json", sample)
    result = compare(tmp_path)
    assert [row["delta"] for row in result["rows"]] == [100, 10]
    assert result["selected"]["frames"] == ["main", "left", "malloc"]
    assert result["selected"]["series"] == [(0, 20), (60, 80), (120, 120)]
    assert result["tree"][0]["bytes"] == 110
    assert [node["frame"] for node in result["tree"][0]["children"]] == ["left", "right"]
    assert compare(tmp_path, "0", "1", query="right")["rows"][0]["delta"] == 5
    assert compare(tmp_path, direction="reduction")["rows"][0]["delta"] == -100
    assert compare(tmp_path, "1", "2")["rows"][0]["delta"] == 40
    ignored = result["rows"][0]["id"]
    filtered = compare(tmp_path, stack=ignored, excluded=[ignored])
    assert [row["delta"] for row in filtered["rows"]] == [10]
    assert filtered["selected"]["frames"] == ["main", "right", "malloc"]
    assert filtered["tree"][0]["bytes"] == 10
    assert compare(tmp_path)["rows"][0]["id"] == ignored
    with pytest.raises(ValueError, match="时间排列"):
        compare(tmp_path, "2", "1")


def test_warmup_uses_the_first_post_warmup_snapshot_by_default(tmp_path):
    from products.fbasecman.longrun_heap import collapsed_stacks, compare, write_json

    samples = []
    for index, (elapsed, amount) in enumerate(((0, 20), (30, 100), (60, 110))):
        stacks = collapsed_stacks(f"main;allocate {amount}")
        samples.append({"id": str(index), "elapsed": elapsed, "bytes": amount})
        write_json(tmp_path / f"stacks-{index}.json", stacks)
    write_json(tmp_path / "measurement.json", {"start": 25})
    write_json(tmp_path / "timeline.json", {"issue": None, "samples": samples[:2]})
    assert compare(tmp_path)["rows"] == []
    assert compare(tmp_path, "0", "1")["rows"][0]["delta"] == 80
    write_json(tmp_path / "timeline.json", {"issue": None, "samples": samples})
    result = compare(tmp_path)
    assert result["start"] == "1"
    assert result["rows"][0]["delta"] == 10


def test_warmup_extends_the_same_workload(suite, tmp_path):
    from types import SimpleNamespace

    binary = tmp_path / "pgbench"
    binary.write_text('#!/bin/sh\necho "number of transactions actually processed: 5"\nprintf "%s\\n" "$@"\n')
    binary.chmod(0o755)
    boundaries = []
    memory = SimpleNamespace(begin_warmup=boundaries.append, sample=lambda: None, next_analysis=42)
    output = tmp_path / "output"
    run(suite, output, 1, ["pgbench10"], 7000, 7001, "127.0.0.1", 5432, binary, memory, 1)
    assert len(boundaries) == 1
    assert memory.next_analysis == 0
    assert (output / "pgbench10.log").read_text().splitlines()[-2:] == ["-T", "2"]


def test_delete_memory_report_keeps_the_task_and_other_artifacts(tmp_path):
    from fastapi.testclient import TestClient
    from platform_app.api import create_app
    from test_api import settings_for

    settings = settings_for(tmp_path)
    app = create_app(settings, enqueuer=lambda _task_id: None)
    client = TestClient(app)
    environment = {
        "id": "memory-lab", "product_id": "fbasecman", "title": "Memory lab",
        "host": "127.0.0.1", "port": 5432, "database_name": "postgres",
        "database_user": "postgres",
    }
    assert client.post("/api/v1/environments", json=environment).status_code == 201
    task = app.state.store.tasks.create_task(
        "memory-lab", "stability.fbasecman", "upstream-7x24", {"memory_profile": True}, None,
    )
    task_id = task["id"]
    run_dir = settings.artifact_dir("fbasecman", "memory-lab") / "runs" / task_id
    memory = run_dir / "memory"
    memory.mkdir(parents=True)
    (memory / "rss.csv").write_text("elapsed_seconds,rss_kib\n0,10\n")
    (run_dir / "load.log").write_text("preserve")
    url = f"/api/v1/environments/memory-lab/fbasecman-longrun/{task_id}"
    history = "/api/v1/fbasecman-memory-tasks"
    assert client.get(history).json()["tasks"][0]["id"] == task_id
    assert client.delete(url + "/memory").status_code == 409
    assert (memory / "rss.csv").exists()

    app.state.store.tasks.finish_task(task_id, ("QUEUED",), "CANCELLED", "done")
    assert client.delete(url + "/memory").json() == {"deleted": True}
    assert client.delete(url + "/memory").json() == {"deleted": True}
    assert client.get(url + "/memory-live").status_code == 410
    assert client.get(url + "/memory/rss.csv").status_code == 410
    assert client.get(history).json() == {"tasks": [], "next": None}
    assert client.get(f"/api/v1/operations/{task_id}").json()["status"] == "CANCELLED"
    assert (run_dir / "load.log").read_text() == "preserve"
    assert not memory.exists()
    assert list((run_dir / "memory.deleted").iterdir()) == []
