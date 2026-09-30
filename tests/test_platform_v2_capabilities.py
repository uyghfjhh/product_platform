"""Platform capabilities must work without any product implementation."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from platform_app.filestore import FileStore
from platform_app.result_publication import publish_regression_results
from platform_regress.clients.pgbench import PgbenchRequest, parse_pgbench_result
from platform_regress.environment.disposable import DisposablePostgresResources
from platform_regress.environment.guards import FileRestoreGuard
from platform_regress.environment.postgresql_fixtures import PostgresFixtures
from platform_regress.evidence.artifacts import ArtifactRepository, prune_run_artifacts
from platform_regress.evidence.fingerprint import local_fingerprint, remote_fingerprint
from platform_regress.evidence.server_logs import ServerLogCollector
from platform_regress.execution.longrun import (
    WorkloadGroup,
    claim_finalization,
    finalize_run,
    observe_workloads,
)
from platform_regress.execution.monitoring import sample_process
from platform_regress.execution.remote import RemoteTarget, ssh_command
from platform_regress.persistence.state import JsonStateStore
from platform_regress.sdk import (
    CaseContext,
    CaseFailure,
    RegressionEngine,
    ReportSpec,
    RuntimeBinding,
    RuntimeExecutorCase,
)


def test_combined_business_and_cleanup_failure_preserves_both(tmp_path):
    class Runtime:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            raise OSError("cleanup failed")

        def finish(self, *args):
            pass

        def stop(self):
            pass

    def execute(ctx, runtime):
        raise CaseFailure("business failed")

    case = RuntimeExecutorCase(
        "demo.case",
        lambda ctx: RuntimeBinding("case", lambda c, s: Runtime(), execute, "ok"),
    )
    result = RegressionEngine().run(case, CaseContext("demo.case", tmp_path))
    assert result.business_verdict == "FAIL"
    assert result.reason == "business failed"
    assert result.verdict == result.cleanup.status == "ERROR"
    assert result.cleanup.reason == "cleanup failed"


@pytest.mark.parametrize("name", ["../victim", "../../../victim", "/victim", ".."])
def test_report_identity_rejects_traversal(name):
    with pytest.raises(ValueError):
        ReportSpec(name, "demo.case", "case", "demo")


def test_setting_reload_failure_still_restores_auto_conf(tmp_path):
    context = CaseContext("demo.setting", tmp_path)
    statements = []

    def sql(node, query, **kwargs):
        statements.append(query)
        return SimpleNamespace(rows=() if "pg_file_settings" in query else (("64MB",),))

    context.sql = sql
    reloads = 0

    def reload(*args, **kwargs):
        nonlocal reloads
        reloads += 1
        if reloads == 1:
            raise OSError("reload failed")

    context.reload = reload
    with pytest.raises(OSError):
        context.set_setting("primary", "work_mem", "16MB")
    context.cleanup_fixtures()
    assert "ALTER SYSTEM RESET work_mem" in statements
    assert reloads == 2


def publication_setup(tmp_path):
    settings = SimpleNamespace(output_dir=tmp_path / "output")
    store = FileStore(tmp_path / "data")
    environment = {"id": "lab", "product_id": "demo"}
    root = settings.output_dir / "regression/lab"
    root.mkdir(parents=True)
    return settings, store, environment, root


def test_publisher_rejects_stale_facts_and_success_without_facts(tmp_path):
    settings, store, env, root = publication_setup(tmp_path)
    output = root / "smoke.case"
    output.mkdir()
    (output / "result.json").write_text(
        json.dumps({"target": "smoke.case", "operation_id": "old", "verdict": "PASS"})
    )
    status, reason = publish_regression_results(
        store,
        settings,
        env,
        {"id": "new", "target": "smoke.case"},
        "SUCCEEDED",
        "done",
        case_targets={"smoke.case"},
    )
    assert status == "FAILED"
    assert store.list_results("lab")[0]["status"] == "ERROR"


def test_publisher_uses_matching_rows_instead_of_aggregate_counts(tmp_path):
    settings, store, env, root = publication_setup(tmp_path)
    output = root / "all"
    output.mkdir()
    (output / "suite-result.json").write_text(
        json.dumps(
            {
                "counts": {"PASS": 100},
                "results": [
                    {
                        "target": "smoke.case",
                        "operation_id": "new",
                        "verdict": "FAIL",
                        "reason": "bad",
                    },
                    {"target": "smoke.old", "operation_id": "old", "verdict": "PASS"},
                ],
            }
        )
    )
    status, _ = publish_regression_results(
        store,
        settings,
        env,
        {"id": "new", "target": "all"},
        "SUCCEEDED",
        "done",
        case_targets={"smoke.case", "smoke.old"},
    )
    assert status == "FAILED"
    assert [row["target"] for row in store.list_results("lab")] == ["smoke.case"]


def test_log_collector_rotation_and_truncation(tmp_path):
    directory = tmp_path / "db"
    directory.mkdir()
    first = directory / "first.log"
    second = directory / "second.log"
    first.write_text("old\n")
    second.write_text("new file\n")
    current = first.name
    context = CaseContext(
        "demo.logs",
        tmp_path / "case",
        environment={
            "nodes": {
                "primary": {
                    "host": "localhost",
                    "port": 5432,
                    "data_dir": str(directory),
                }
            },
        },
    )
    collector = ServerLogCollector(
        context,
        "primary",
        context.output_dir,
        current_log=lambda *args: current,
        destinations={"stderr": "server.log"},
    )
    collector.start()
    with first.open("a") as stream:
        stream.write("old file tail\n")
    current = second.name
    refs = collector.finish()
    assert (context.output_dir / refs[0]).read_text() == "old file tail\nnew file\n"
    assert collector.finish() == []
    collector = ServerLogCollector(
        context,
        "primary",
        context.output_dir,
        current_log=lambda *args: current,
        destinations={"stderr": "truncated.log"},
    )
    collector.start()
    second.write_text("x\n")
    refs = collector.finish()
    assert (context.output_dir / refs[0]).read_text() == "x\n"


def test_remote_log_snapshot_records_size_and_fetches_only_tail(tmp_path, monkeypatch):
    context = CaseContext(
        "demo.remote",
        tmp_path,
        environment={
            "nodes": {
                "remote": {
                    "host": "remote.example",
                    "port": 5432,
                    "data_dir": "/pg/data",
                }
            },
        },
    )
    monkeypatch.setattr(context, "is_local", lambda host: False)
    size = 10

    class Remote:
        def __init__(self, *args):
            pass

        def stat(self, path):
            return 1, 22, size

        def tail(self, path, offset):
            assert offset == 10
            return b"new\n"

    monkeypatch.setattr("platform_regress.evidence.server_logs.RemoteExecutor", Remote)
    collector = ServerLogCollector(
        context,
        "remote",
        tmp_path,
        current_log=lambda *args: "pg.log",
        destinations={"stderr": "remote.log"},
    )
    collector.start()
    assert collector.before["stderr"][1] == 10
    size = 14
    reference = collector.finish()[0]
    assert (tmp_path / reference).read_bytes() == b"new\n"


def test_remote_command_quotes_script_and_separates_identity():
    command = ssh_command(
        RemoteTarget("host.example", "postgres", 2222), "cat 'a b'", login_shell=True
    )
    assert command[command.index("-p") + 1] == "2222"
    assert "postgres@host.example" in command
    with pytest.raises(ValueError):
        RemoteTarget("-oProxyCommand=bad", "postgres")


def test_pgbench_command_and_metrics_are_product_independent():
    command = PgbenchRequest(
        "/pg/bin/pgbench", "localhost", 5432, "u", "db", "test.sql", 8, 2
    ).argv()
    assert command[-4:] == ["-c", "8", "-j", "2"]
    result = parse_pgbench_result(
        "number of transactions actually processed: 12\nnumber of failed transactions: 0\ntps = 123.5\nlatency average = 2.4 ms"
    )
    assert result["ok"] and result["transactions"] == 12 and result["tps"] == 123.5


def test_state_defaults_are_isolated_and_finalization_is_claimed_once(tmp_path):
    store = JsonStateStore(
        tmp_path / "state.json",
        defaults={"schema_version": 1, "status": "running", "workloads": {}},
    )
    first = store.load()
    first["workloads"]["a"] = {}
    assert store.load()["workloads"] == {}
    assert claim_finalization(store)
    assert not claim_finalization(store)
    assert store.load()["status"] == "finalizing"


def test_supervision_records_completion_without_product_code(tmp_path):
    store = JsonStateStore(
        tmp_path / "state.json",
        defaults={
            "schema_version": 1,
            "status": "running",
            "workloads": {"work": {"pid": 0, "status": "running"}},
        },
    )
    assert observe_workloads(store, lambda name, item: {"ok": False, "returncode": 1})
    assert store.load()["workloads"]["work"]["status"] == "failed"


def test_finalization_does_not_resurrect_stopped_run(tmp_path):
    store = JsonStateStore(
        tmp_path / "state.json",
        defaults={"schema_version": 1, "status": "finalizing", "run_id": "one"},
    )
    store.save(store.load())

    def finalize(state):
        state["status"] = "completed"
        store.update(lambda current: current.update(status="stopped"))

    def report(state):
        raise OSError("report write failed")

    finalize_run(store, finalize=finalize, cleanup=lambda state: {}, report=report)
    assert store.load()["status"] == "stopped"


def test_resource_sampling_records_live_process():
    sample = sample_process(os.getpid())
    assert sample["pid"] == os.getpid() and int(sample["rss_kb"]) > 0
    assert sample["fd_count"] >= 0


def test_file_guard_restores_on_failed_mutation(tmp_path):
    file = tmp_path / "configuration"
    file.write_bytes(b"original")
    context = CaseContext("demo.file", tmp_path / "run")
    guard = FileRestoreGuard(context, file)
    guard.write_text("changed")
    context.cleanup_fixtures()
    assert file.read_bytes() == b"original"


def test_database_fixture_never_drops_object_after_failed_create(tmp_path):
    context = CaseContext("demo.database", tmp_path)
    deferred, calls = [], []

    def checked(ctx, node, user, db, sql):
        calls.append(sql)
        assert deferred
        if sql.startswith("CREATE"):
            raise RuntimeError("partial setup")

    fixtures = PostgresFixtures(
        context,
        checked=checked,
        scalar=None,
        execute=None,
        apply_cluster=None,
        check_setting=None,
        defer=lambda ctx, title, callback, **kwargs: deferred.append(callback),
    )
    context.resolve_node = lambda selector: "primary"
    with pytest.raises(RuntimeError):
        fixtures.database({"name": "test_db"})
    deferred[0]()
    assert calls == ['CREATE DATABASE "test_db"']


def test_disposable_resources_reject_external_or_symlink_paths(tmp_path):
    prefix = tmp_path / "owned_"
    external = tmp_path / "external"
    external.mkdir()
    link = tmp_path / "owned_link"
    link.symlink_to(external, target_is_directory=True)
    context = CaseContext("demo.resources", tmp_path / "run")
    manager = DisposablePostgresResources(
        context,
        owned_prefix=str(prefix),
        pg_ctl=lambda: "pg_ctl",
        run=lambda *args, **kwargs: None,
    )
    with pytest.raises(Exception):
        manager.remove(external)
    with pytest.raises(Exception):
        manager.remove(link)
    assert external.is_dir()


def test_retention_preserves_evidence_originals_and_path_boundaries(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    log = root / "server.log"
    content = "\n".join(str(i) for i in range(1000))
    log.write_text(content)
    result = prune_run_artifacts(
        root, max_file_size_mb=0.001, keep_head_lines=2, keep_tail_lines=3
    )
    assert log.read_text() == content
    assert result.pruned[0]["action"] == "preview_created"
    assert "999" in Path(result.pruned[0]["preview"]).read_text()
    with pytest.raises(ValueError):
        ArtifactRepository(root).log("../outside.log")


def test_binary_fingerprint_rejects_invalid_remote_payload(tmp_path):
    path = tmp_path / "binary"
    path.write_bytes(b"content")
    assert len(local_fingerprint(path)["sha256"]) == 64
    result = remote_fingerprint(
        "host",
        "user",
        "/binary",
        runner=lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="bad\n12 34\n", stderr=""
        ),
    )
    assert "error" in result


def test_database_fixture_has_native_sdk_transport(tmp_path):
    context = CaseContext("demo.native-fixture", tmp_path)
    context.resolve_node = lambda selector: "primary"
    statements = []
    context.sql = lambda node, sql, **kw: (
        statements.append(sql),
        SimpleNamespace(rows=()),
    )[1]
    PostgresFixtures(context).table({"name": "temporary"})
    context.cleanup_fixtures()
    assert statements == [
        'CREATE TABLE "public"."temporary" (id integer PRIMARY KEY)',
        'DROP TABLE IF EXISTS "public"."temporary"',
    ]


def test_active_report_failure_marks_run_failed(tmp_path):
    store = JsonStateStore(
        tmp_path / "state.json",
        defaults={"schema_version": 1, "status": "finalizing", "run_id": "one"},
    )
    store.save(store.load())

    def report(state):
        raise OSError("disk full")

    finalize_run(
        store,
        finalize=lambda state: state.update(status="completed"),
        cleanup=lambda state: {},
        report=report,
    )
    assert store.load()["status"] == "failed"
    assert "disk full" in store.load()["report_error"]


def test_cleanup_actions_attempt_all_resources():
    from platform_regress.execution.longrun import cleanup_actions

    calls = []

    def bad():
        calls.append("bad")
        raise OSError("failed")

    result = cleanup_actions(
        {"first": bad, "second": lambda: calls.append("second") or True}
    )
    assert calls == ["bad", "second"]
    assert result["second"] and result["errors"]["first"] == "OSError: failed"


def test_workload_group_owns_launch_even_when_state_write_fails(tmp_path):
    class Process:
        pid = 0

        def poll(self):
            return 0

        def close_output(self):
            self.closed = True

    process = Process()

    class Store:
        def save(self, state):
            raise OSError("state write failed")

    state = {"run_dir": str(tmp_path), "commands": {}, "workloads": {"a": {}}}
    group = WorkloadGroup(
        Store(),
        state,
        launch=lambda item: (process, tmp_path / "a.log", ["cmd"]),
        describe=lambda item: {"fingerprint": "cmd"},
        evaluate=lambda *a: {"ok": True},
    )
    with pytest.raises(OSError):
        group.run([SimpleNamespace(name="a")])
    assert group.close() and process.closed


@pytest.mark.parametrize(
    "fixture,options",
    [
        ("roles", {"create": ["existing_role"]}),
        ("table", {"name": "existing_table"}),
        ("sequence", {"name": "existing_sequence"}),
    ],
)
def test_failed_create_never_drops_preexisting_sql_objects(tmp_path, fixture, options):
    context = CaseContext("demo.ownership", tmp_path)
    context.resolve_node = lambda selector: "primary"
    statements = []

    def sql(node, query, **kwargs):
        statements.append(query)
        if query.startswith("CREATE"):
            raise RuntimeError("already exists")
        raise AssertionError("must not delete an object the fixture did not create")

    context.sql = sql
    with pytest.raises(RuntimeError):
        getattr(PostgresFixtures(context), fixture)(options)
    context.cleanup_fixtures()
    assert len(statements) == 1


def test_report_constructor_rejects_symlink_escape(tmp_path):
    from platform_regress.sdk import ReportRuntime

    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "case"
    sentinel.mkdir()
    (sentinel / "keep").write_text("evidence")
    output = tmp_path / "output"
    (output / "runs").mkdir(parents=True)
    (output / "runs/demo").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        ReportRuntime(
            tmp_path,
            ReportSpec("case", "demo.case", "case", "demo"),
            output_root=output,
            context_data={},
        )
    assert (sentinel / "keep").read_text() == "evidence"


def test_postgresql_log_archive_protects_open_files(tmp_path):
    import subprocess

    from platform_regress.evidence.postgresql_logs import postgres_log_archive_script

    install = tmp_path / "postgres"
    (install / "bin").mkdir(parents=True)
    data = tmp_path / "data"
    logs = data / "pg_log"
    logs.mkdir(parents=True)
    open_log = logs / "open.log"
    closed_log = logs / "closed.log"
    open_log.write_text("open evidence")
    closed_log.write_text("closed evidence")
    psql = install / "bin/psql"
    psql.write_text(
        '#!/bin/sh\ncase "$*" in *data_directory*) echo "'
        + str(data)
        + '";; *) echo pg_log;; esac\n'
    )
    psql.chmod(0o755)
    tools = tmp_path / "tools"
    tools.mkdir()
    lsof = tools / "lsof"
    lsof.write_text('#!/bin/sh\necho "n' + str(open_log) + '"\n')
    lsof.chmod(0o755)
    script = postgres_log_archive_script(str(install), 5432, "postgres", 0, "node")
    result = subprocess.run(
        ["bash", "-se"],
        input=script,
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": str(tools) + ":" + os.environ["PATH"]},
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert (
        "ARCHIVED_FILES=1" in result.stdout and "SKIPPED_OPEN_FILES=1" in result.stdout
    )
    assert open_log.read_text() == "open evidence"
    assert closed_log.read_text() == "closed evidence"
    assert list((logs / "archive").glob("run_node_*.tar.gz"))
