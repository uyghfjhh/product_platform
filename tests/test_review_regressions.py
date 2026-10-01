"""Failure-path regression coverage for the seven platform review findings."""

import json
import sys
import time
from types import SimpleNamespace

import pytest
from platform_app.api.schemas import public_task
from platform_app.filestore import FileStore
from platform_app.result_publication import publish_regression_results
from platform_regress.environment.disposable import DisposablePostgresResources
from platform_regress.evidence.artifacts import CleanupReport, remove_artifact
from platform_regress.evidence.server_logs import ServerLogCollector
from platform_regress.execution.longrun import finalize_run
from platform_regress.persistence.state import JsonStateStore
from platform_regress.sdk import Blocked, Cancelled, CaseContext


@pytest.mark.parametrize("parent_link", [False, True])
def test_artifact_cleanup_rejects_out_of_root_file(tmp_path, parent_link):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "keep"
    victim.write_text("evidence")
    if parent_link:
        (root / "link").symlink_to(outside, target_is_directory=True)
        path = root / "link/keep"
    else:
        path = root / "../outside/keep"
    with pytest.raises(ValueError):
        remove_artifact(path, CleanupReport(), root=root)
    assert victim.read_text() == "evidence"


def test_artifact_symlink_itself_can_be_deleted_without_touching_target(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "keep"
    target.write_text("evidence")
    link = root / "link"
    link.symlink_to(target)
    remove_artifact(link, CleanupReport(), root=root)
    assert not link.is_symlink() and target.read_text() == "evidence"


@pytest.mark.parametrize("outcome", ["exception", "false", "errors"])
def test_cleanup_failure_cannot_publish_completed_run(tmp_path, outcome):
    store = JsonStateStore(
        tmp_path / "state.json",
        defaults={
            "schema_version": 1,
            "status": "finalizing",
            "run_id": "run",
        },
    )
    store.save(store.load())

    def cleanup(state):
        if outcome == "exception":
            raise OSError("stop failed")
        return (
            {"workloads_stopped": False}
            if outcome == "false"
            else {"errors": {"stop": "failed"}}
        )

    reports = []
    finalize_run(
        store,
        finalize=lambda state: state.update(status="completed"),
        cleanup=cleanup,
        report=lambda state: reports.append(state["status"]),
    )
    assert store.load()["status"] == "failed"
    assert store.load()["cleanup_error"]
    assert reports == ["failed"]


def test_large_stdin_obeys_deadline_and_keeps_failure_evidence(tmp_path):
    context = CaseContext("demo.stdin", tmp_path)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        context.command(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            input_text="x" * 1000000,
            timeout_seconds=0.1,
        )
    assert time.monotonic() - started < 2
    assert context.evidence
    assert json.loads((tmp_path / context.evidence[0]).read_text())["error"]


def test_large_stdin_obeys_cancellation(tmp_path):
    started = time.monotonic()
    context = CaseContext(
        "demo.cancel", tmp_path, cancelled=lambda: time.monotonic() - started >= 0.05
    )
    with pytest.raises(Cancelled):
        context.command(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            input_text="x" * 1000000,
        )
    assert time.monotonic() - started < 2
    assert context.evidence


def test_communicate_drains_output_while_sending_large_input(tmp_path):
    context = CaseContext("demo.duplex", tmp_path)
    code = "import sys; sys.stdout.write('a'*1000000); sys.stdout.flush(); data=sys.stdin.read(); print(len(data))"
    result = context.command(
        [sys.executable, "-c", code], input_text="b" * 1000000, timeout_seconds=2
    )
    assert result.returncode == 0 and result.stdout.endswith("1000000\n")


def test_background_stdin_never_blocks_start_and_timeout_reclaims_child(tmp_path):
    context = CaseContext("demo.background", tmp_path)
    started = time.monotonic()
    argv = [sys.executable, "-c", "import time; time.sleep(10)"]
    process = context.start_command(argv, input_text="x" * 1000000)
    result = context.finish_command(argv, process, timeout_seconds=0.1)
    assert time.monotonic() - started < 2
    assert result.returncode == 124 and process.poll() is not None
    context.cleanup_fixtures()


def test_background_input_delivered_and_captured(tmp_path):
    context = CaseContext("demo.input", tmp_path)
    argv = [sys.executable, "-c", "import sys; print(len(sys.stdin.read()))"]
    process = context.start_command(argv, input_text="x" * 1000000)
    result = context.finish_command(argv, process, timeout_seconds=2)
    assert result.returncode == 0 and result.stdout == "1000000\n"
    context.cleanup_fixtures()


def test_stale_aggregate_does_not_hide_fresh_case_facts(tmp_path):
    root = tmp_path / "output/demo/lab/runs/test-run/cases"
    (root / "smoke.case").mkdir(parents=True)
    (root / "suite-result.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "target": "smoke.case",
                        "operation_id": "old",
                        "verdict": "PASS",
                    }
                ]
            }
        )
    )
    case = root / "smoke.case/result.json"
    case.write_text(
        json.dumps({"target": "smoke.case", "operation_id": "new", "verdict": "FAIL"})
    )
    if (root / "suite-result.json").is_file():
        for row in json.loads((root / "suite-result.json").read_text()).get("results", []):
            case_path = root / row["target"] / "result.json"
            if not case_path.exists():
                case_path.parent.mkdir(parents=True, exist_ok=True)
                case_path.write_text(json.dumps(row))
    store = FileStore(tmp_path / "data")
    publish_regression_results(
        store,
        __import__("test_api").settings_for(tmp_path),
        {"id": "lab", "product_id": "demo"},
        {"id": "new", "target": "all"},
        "FAILED",
        "interrupted",
        case_targets={"smoke.case"},
    )
    rows = store.results.list_results("lab")
    assert [(row["target"], row["status"]) for row in rows] == [("smoke.case", "FAIL")]
    assert rows[0]["artifact_dir"] == str(case.parent)


def test_partial_aggregate_does_not_hide_other_completed_cases(tmp_path):
    root = tmp_path / "output/demo/lab/runs/test-run/cases"
    root.mkdir(parents=True)
    (root / "suite-result.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "target": "smoke.one",
                        "operation_id": "new",
                        "verdict": "PASS",
                    }
                ]
            }
        )
    )
    (root / "smoke.two").mkdir()
    (root / "smoke.two/result.json").write_text(
        json.dumps(
            {
                "target": "smoke.two",
                "operation_id": "new",
                "verdict": "FAIL",
            }
        )
    )
    (root / "smoke.one").mkdir()
    (root / "smoke.one" / "result.json").write_text(json.dumps({"target": "smoke.one", "operation_id": "new", "verdict": "PASS"}))
    store = FileStore(tmp_path / "data")
    terminal, _ = publish_regression_results(
        store,
        __import__("test_api").settings_for(tmp_path),
        {"id": "lab", "product_id": "demo"},
        {"id": "new", "target": "all"},
        "SUCCEEDED",
        "done",
        case_targets={"smoke.one", "smoke.two"},
    )
    assert terminal == "FAILED"
    assert {row["target"] for row in store.results.list_results("lab")} == {
        "smoke.one",
        "smoke.two",
    }


def test_same_name_rotation_keeps_both_old_tail_and_new_file(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "server.log"
    path.write_text("old\n")
    context = CaseContext(
        "demo.rotation",
        tmp_path / "run",
        environment={
            "nodes": {
                "primary": {"host": "localhost", "port": 5432, "data_dir": str(data)}
            },
        },
    )
    collector = ServerLogCollector(
        context,
        "primary",
        context.output_dir,
        current_log=lambda *a: "server.log",
        destinations={"stderr": "server.log"},
    )
    collector.start()
    with path.open("a") as stream:
        stream.write("old tail\n")
    path.rename(data / "rotated.log")
    path.write_text("ERROR new-file\n")
    ref = collector.finish()[0]
    assert (context.output_dir / ref).read_text() == "old tail\nERROR new-file\n"
    assert not collector.errors and not collector._handles
    context.cleanup_fixtures()


def test_remote_same_name_rotation_uses_inode_to_find_previous_file(
    tmp_path, monkeypatch
):
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
    inode = 10

    class Remote:
        def __init__(self, *a):
            pass

        def stat(self, path):
            return 1, inode, 4 if inode == 10 else 15

        def tail_identity(self, path, offset, identity):
            assert offset == 4 and identity == (1, 10)
            return b"old tail\n"

        def tail(self, path, offset):
            assert offset == 0
            return b"ERROR new-file\n"

    monkeypatch.setattr("platform_regress.evidence.server_logs.RemoteExecutor", Remote)
    collector = ServerLogCollector(
        context,
        "remote",
        tmp_path,
        current_log=lambda *a: "server.log",
        destinations={"stderr": "remote.log"},
    )
    collector.start()
    inode = 11
    ref = collector.finish()[0]
    assert (tmp_path / ref).read_bytes() == b"old tail\nERROR new-file\n"


@pytest.mark.parametrize(
    "returncode,output",
    [(127, "lsof missing"), (124, "timeout"), (1, "permission denied")],
)
def test_listener_probe_failure_is_never_treated_as_free(tmp_path, returncode, output):
    context = CaseContext("demo.ports", tmp_path)
    resources = DisposablePostgresResources(
        context,
        owned_prefix=str(tmp_path / "owned_"),
        pg_ctl=lambda: "pg_ctl",
        run=lambda *a, **kw: (returncode, output),
    )
    with pytest.raises(Blocked):
        resources.wait_ports_free(["5432"])


def test_valid_empty_listener_probe_is_free(tmp_path):
    context = CaseContext("demo.ports", tmp_path)
    resources = DisposablePostgresResources(
        context,
        owned_prefix=str(tmp_path / "owned_"),
        pg_ctl=lambda: "pg_ctl",
        run=lambda *a, **kw: (1, ""),
    )
    resources.wait_ports_free(["5432"])


def task_setup(tmp_path):
    store = FileStore(tmp_path)
    task = store.tasks.create_task("lab", "tests.demo", "smoke.case", {}, None)
    store.tasks.transition_task(task["id"], ("QUEUED",), "RUNNING")
    return store, task


def test_terminal_and_event_are_visible_when_projection_fails(tmp_path, monkeypatch):
    store, task = task_setup(tmp_path)

    def fail(*a, **kw):
        raise OSError("projection failed")

    monkeypatch.setattr(store.tasks, "_append_event", fail)
    assert store.tasks.finish_task(task["id"], ("RUNNING",), "SUCCEEDED", "done")
    meta = store.tasks.get_task(task["id"])
    assert meta["status"] == "SUCCEEDED" and meta["pending_events"]
    assert "pending_events" not in public_task(meta)
    assert store.tasks.list_events(task["id"])[0]["event_type"] == "operation.finished"
    recovered = FileStore(tmp_path)
    assert recovered.tasks.get_task(task["id"])["status"] == "SUCCEEDED"
    assert "pending_events" not in recovered.tasks.get_task(task["id"])
    assert len(recovered.tasks.list_events(task["id"])) == 1


def test_retry_after_projection_success_never_duplicates_event(tmp_path, monkeypatch):
    store, task = task_setup(tmp_path)
    original = store.tasks._write_task

    def fail_clear(row):
        if row["status"] == "SUCCEEDED" and not row.get("pending_events"):
            raise OSError("interrupted before outbox clear")
        return original(row)

    monkeypatch.setattr(store.tasks, "_write_task", fail_clear)
    store.tasks.finish_task(task["id"], ("RUNNING",), "SUCCEEDED", "done")
    assert len(store.tasks.list_events(task["id"])) == 1
    recovered = FileStore(tmp_path)
    recovered.tasks.recover_event_projections()
    lines = recovered.tasks._task_events(task["id"]).read_text().splitlines()
    assert len(lines) == 1 and len(recovered.tasks.list_events(task["id"])) == 1


def test_partial_utf8_event_append_is_replayed_from_durable_metadata(
    tmp_path, monkeypatch
):
    store, task = task_setup(tmp_path)

    def partial(*a, **kw):
        store.tasks._task_events(task["id"]).write_bytes(b'{"payload":"\xe4')
        raise OSError("interrupted write")

    monkeypatch.setattr(store.tasks, "_append_event", partial)
    store.tasks.finish_task(task["id"], ("RUNNING",), "FAILED", "失败")
    assert store.tasks.list_events(task["id"])[0]["payload"]["reason"] == "失败"
    recovered = FileStore(tmp_path)
    assert recovered.tasks.list_events(task["id"])[0]["payload"]["reason"] == "失败"
    assert len(recovered.tasks._task_events(task["id"]).read_text().splitlines()) == 1


def test_pending_events_keep_order_and_cursor_across_restart(tmp_path, monkeypatch):
    store, task = task_setup(tmp_path)

    def fail(*a, **kw):
        raise OSError("projection failed")

    monkeypatch.setattr(store.tasks, "_append_event", fail)
    store.tasks.add_event(task["id"], "step.finished", {"title": "one"})
    store.tasks.finish_task(task["id"], ("RUNNING",), "FAILED", "two")
    assert [e["sequence"] for e in store.tasks.list_events(task["id"])] == [1, 2]
    recovered = FileStore(tmp_path)
    assert [e["sequence"] for e in recovered.tasks.list_events(task["id"], after=1)] == [2]


def test_failed_authoritative_commit_does_not_publish_terminal_or_event(
    tmp_path, monkeypatch
):
    store, task = task_setup(tmp_path)

    def fail(*a, **kw):
        raise OSError("metadata write failed")

    monkeypatch.setattr(store.tasks, "_write_task", fail)
    with pytest.raises(OSError):
        store.tasks.finish_task(task["id"], ("RUNNING",), "SUCCEEDED", "done")
    assert store.tasks.get_task(task["id"])["status"] == "RUNNING"
    assert store.tasks.list_events(task["id"]) == []


def test_queued_cancel_commits_its_event_even_when_projection_fails(
    tmp_path, monkeypatch
):
    store = FileStore(tmp_path)
    task = store.tasks.create_task("lab", "tests.demo", "smoke.case", {}, None)

    def fail(*a, **kw):
        raise OSError("projection failed")

    monkeypatch.setattr(store.tasks, "_append_event", fail)
    assert store.tasks.request_cancel(task["id"])
    assert store.tasks.get_task(task["id"])["status"] == "CANCELLED"
    assert (
        store.tasks.list_events(task["id"])[0]["event_type"] == "operation.cancel_requested"
    )


def test_recovery_terminal_transition_also_commits_finished_event(tmp_path):
    store, task = task_setup(tmp_path)
    assert store.tasks.transition_task(
        task["id"], ("RUNNING",), "RECOVERY_REQUIRED", reason="restart"
    )
    assert store.tasks.list_events(task["id"])[0]["payload"]["status"] == "RECOVERY_REQUIRED"


def test_event_stream_drains_finish_racing_with_event_read(tmp_path, monkeypatch):
    import asyncio

    from platform_app.api import create_app
    from test_api import settings_for

    app = create_app(settings_for(tmp_path), enqueuer=lambda task_id: None)
    store = app.state.store
    task = store.tasks.create_task("lab", "tests.demo", "smoke.case", {}, None)
    store.tasks.transition_task(task["id"], ("QUEUED",), "RUNNING")
    original = store.tasks.list_events
    first = True

    def race(task_id, after=0):
        nonlocal first
        snapshot = original(task_id, after)
        if first:
            first = False
            store.tasks.finish_task(task_id, ("RUNNING",), "SUCCEEDED", "done")
        return snapshot

    async def no_wait(*a):
        pass

    monkeypatch.setattr(store.tasks, "list_events", race)
    monkeypatch.setattr("asyncio.sleep", no_wait)
    endpoint = next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", "") == "/api/v1/operations/{task_id}/events/stream"
    )

    async def collect():
        response = await endpoint(task["id"], after=0)
        return [part async for part in response.body_iterator]

    assert "operation.finished" in "".join(asyncio.run(collect()))
