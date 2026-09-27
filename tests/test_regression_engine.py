import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from platform_regress import Blocked, Cancelled, CaseContext, RegressionEngine
from platform_app.actions import run_task
from platform_app.api import create_app
from platform_app.config import Settings
from test_api import settings_for


class PassingCase:
    def setup(self, context):
        context.step("setup", "Prepare")

    def run(self, context):
        context.attach_text("query.log", "select 1\n")
        context.step("check", "Check result")
        return True

    def cleanup(self, context):
        context.step("cleanup", "Restore")


def test_runner_records_result_and_ordered_evidence(tmp_path):
    result = RegressionEngine().run(PassingCase(), CaseContext("demo.case", tmp_path))
    assert result.verdict == "PASS"
    assert result.evidence == (f"artifacts/{result.execution_id}/query.log",)
    assert json.loads((tmp_path / "result.json").read_text())["cleanup"]["status"] == "PASS"
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [item["sequence"] for item in events] == list(range(1, len(events) + 1))
    assert events[0]["kind"] == "case.started"
    assert events[-1]["kind"] == "case.finished"
    assert {item["execution_id"] for item in events} == {result.execution_id}


def test_rerun_replaces_events_and_never_reuses_old_evidence(tmp_path):
    first = RegressionEngine().run(PassingCase(), CaseContext("demo.case", tmp_path))

    class SecondCase:
        def run(self, context):
            context.step("new", "New check")
            return True

    second = RegressionEngine().run(SecondCase(), CaseContext("demo.case", tmp_path))
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert first.execution_id != second.execution_id
    assert second.evidence == ()
    assert json.loads((tmp_path / "result.json").read_text())["execution_id"] == second.execution_id
    assert [item["sequence"] for item in events] == list(range(1, len(events) + 1))
    assert {item["execution_id"] for item in events} == {second.execution_id}


def test_business_failure_survives_cleanup_failure(tmp_path):
    class BrokenCase:
        def run(self, context):
            raise AssertionError("wrong route")

        def cleanup(self, context):
            raise RuntimeError("replica still down")

    result = RegressionEngine().run(BrokenCase(), CaseContext("demo.case", tmp_path))
    assert result.business_verdict == "FAIL"
    assert result.reason == "wrong route"
    assert result.cleanup.status == "ERROR"
    assert result.verdict == "ERROR"


def test_blocked_and_cooperative_cancel_have_distinct_verdicts(tmp_path):
    class BlockedCase:
        def run(self, context):
            raise Blocked("database unavailable")

    blocked = RegressionEngine().run(BlockedCase(), CaseContext("demo.blocked", tmp_path / "blocked"))
    assert blocked.verdict == "BLOCKED"

    called = []

    class CancelledCase:
        def run(self, context):
            called.append("run")

        def cleanup(self, context):
            called.append("cleanup")

    cancelled = RegressionEngine().run(
        CancelledCase(), CaseContext("demo.cancel", tmp_path / "cancel", lambda: True),
    )
    assert cancelled.verdict == "CANCELLED"
    assert called == ["cleanup"]


def test_product_case_runs_via_platform_cli(tmp_path):
    package = tmp_path / "demo"
    package.mkdir()
    (package / "product.yaml").write_text("id: demo\ntitle: Demo\nplugin_api: v1\n", encoding="utf-8")
    (package / "cases.py").write_text(
        "class Case:\n"
        "    def run(self, context):\n"
        "        context.step('query', 'SQL result', details={'rows': 1})\n"
        "        context.attach_text('sql.log', 'SELECT 1\\n')\n"
        "        return True\n"
        "CASES = {'demo.query': Case()}\n",
        encoding="utf-8",
    )
    output = tmp_path / "result"
    root = Path(__file__).parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root / "backend"), str(root)))}
    process = subprocess.run(
        [sys.executable, "-m", "platform_regress.cli", "--product-dir", str(package),
         "--output-dir", str(output), "demo.query"],
        cwd=root, env=env, capture_output=True, text=True, timeout=20, check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert json.loads(process.stdout)["verdict"] == "PASS"
    payload = json.loads((output / "result.json").read_text())
    assert payload["evidence"] == [f"artifacts/{payload['execution_id']}/sql.log"]


def test_platform_cli_accepts_explicit_nodes(tmp_path):
    from platform_regress.cli import main

    package = tmp_path / "demo"
    package.mkdir()
    (package / "product.yaml").write_text("id: demo\n", encoding="utf-8")
    (package / "cases.py").write_text(
        "class Case:\n"
        "    def run(self, context):\n"
        "        assert context.environment['nodes']['primary'] == "
        "{'host': '127.0.0.1', 'port': 15432}\n"
        "        return True\n"
        "CASES = {'demo.case': Case()}\n",
        encoding="utf-8",
    )
    assert main(["--product-dir", str(package), "--output-dir", str(tmp_path / "out"),
                 "--node", "primary=127.0.0.1:15432", "demo.case"]) == 0


def test_platform_cli_runs_a_suite_batch_and_writes_aggregate(tmp_path):
    from platform_regress.cli import main
    package = tmp_path / "demo"
    package.mkdir()
    (package / "product.yaml").write_text("id: demo\n", encoding="utf-8")
    (package / "cases.py").write_text(
        "class Case:\n"
        "    def __init__(self, name): self.name = name\n"
        "    def run(self, context): context.step('check', self.name); return True\n"
        "CASES = {'demo.one': Case('one'), 'demo.two': Case('two')}\n",
        encoding="utf-8",
    )
    output = tmp_path / "batch"
    assert main(["--product-dir", str(package), "--output-dir", str(output), "--suite", "demo"]) == 0
    aggregate = json.loads((output / "suite-result.json").read_text())
    assert aggregate["counts"]["PASS"] == 2
    assert (output / "demo.one" / "result.json").is_file()
    assert (output / "demo.two" / "result.json").is_file()


def test_evidence_name_cannot_escape_case_directory(tmp_path):
    context = CaseContext("demo.case", tmp_path)
    with pytest.raises(ValueError, match="证据文件名"):
        context.attach_text("../private", "secret")


def test_attach_file_copies_source_into_execution_evidence(tmp_path):
    source = tmp_path / "old-report.txt"
    source.write_text("original verdict\n", encoding="utf-8")
    context = CaseContext("demo.case", tmp_path / "platform")
    reference = context.attach_file("report.txt", source)
    source.write_text("changed later\n", encoding="utf-8")
    assert (context.output_dir / reference).read_text(encoding="utf-8") == "original verdict\n"
    with pytest.raises(ValueError, match="证据文件名"):
        context.attach_file("../escape", source)


def test_sql_uses_declared_node_and_records_query_evidence(tmp_path, monkeypatch):
    from platform_regress import SqlResult

    calls = []

    class Column:
        name = "value"

    class Cursor:
        description = [Column()]
        statusmessage = "SELECT 1"

        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, query): calls.append(query)
        def fetchall(self): return [(1,)]

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def cursor(self): return Cursor()

    def connect(**kwargs):
        calls.append(kwargs)
        return Connection()

    monkeypatch.setattr("platform_regress.engine.psycopg.connect", connect)
    context = CaseContext("demo.case", tmp_path, environment={
        "user": "tester", "nodes": {"primary": {"host": "127.0.0.1", "port": 15432}},
    })
    result = context.sql("primary", "SELECT 1")
    assert result == SqlResult((("1",),), ("value",), "SELECT 1")
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 15432
    assert calls[0]["user"] == "tester"
    evidence = json.loads((tmp_path / context.evidence[0]).read_text())
    assert evidence["sql"] == "SELECT 1"
    assert evidence["rows"] == [["1"]]


def test_command_records_nonzero_exit_without_rewriting_business_verdict(tmp_path):
    context = CaseContext("demo.command", tmp_path)
    result = context.command([
        sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)",
    ])
    assert result.returncode == 7
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
    evidence = json.loads((tmp_path / context.evidence[0]).read_text())
    assert evidence["returncode"] == 7
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert events[-1]["kind"] == "command.finished"


def test_command_timeout_and_cancel_stop_child_process(tmp_path):
    context = CaseContext("demo.timeout", tmp_path / "timeout")
    with pytest.raises(TimeoutError):
        context.command([sys.executable, "-c", "import time; time.sleep(5)"], timeout_seconds=0.1)
    assert context.evidence

    checks = iter((False, True))
    cancelled = CaseContext("demo.cancel", tmp_path / "cancel", lambda: next(checks, True))
    with pytest.raises(Cancelled):
        cancelled.command([sys.executable, "-c", "import time; time.sleep(5)"])
    assert cancelled.evidence


def test_product_case_uses_shared_engine_through_web_task(tmp_path, monkeypatch):
    package = tmp_path / "products" / "demo"
    package.mkdir(parents=True)
    (package / "product.yaml").write_text(
        "id: demo\ntitle: Demo\nplugin_api: v1\ncapabilities:\n  tests: demo\n"
        "actions:\n  - id: tests.demo\n    title: Run demo\n"
        "    capability: tests\n    changes_environment: true\n",
        encoding="utf-8",
    )
    (package / "cases.py").write_text(
        "class Case:\n"
        "    def run(self, context):\n"
        "        context.step('assertion', 'Check the value')\n"
        "        return True\n"
        "CASES = {'demo.case': Case()}\n",
        encoding="utf-8",
    )
    (package / "provider.py").write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "from platform_app.providers import CommandSpec\n"
        "class Provider:\n"
        "    def command(self, settings, environment, action, target, parameters):\n"
        "        output = settings.data_dir / 'runs' / target\n"
        "        return CommandSpec([sys.executable, '-m', 'platform_regress.cli',\n"
        "            '--product-dir', str(Path(__file__).parent),\n"
        "            '--output-dir', str(output), target], settings.data_dir)\n"
        "    def discover(self, settings): return []\n"
        "    def observe_database(self, environment): return []\n"
        "    def observe_runtime(self, settings, environment): return []\n"
        "    def validate_target(self, settings, target): return True\n"
        "    def publish_result(self, store, settings, environment, task, terminal, reason):\n"
        "        output = settings.data_dir / 'runs' / task['target']\n"
        "        result = json.loads((output / 'result.json').read_text())\n"
        "        store.put_result(environment['product_id'], environment['id'],\n"
        "            task['target'], 'default', result['verdict'], result['reason'], str(output))\n"
        "        return terminal, reason\n"
        "PROVIDER = Provider()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Settings, "products_root", property(lambda self: package.parent))
    settings = settings_for(tmp_path)
    queued = []
    app = create_app(settings, enqueuer=queued.append)
    client = TestClient(app)
    client.post("/api/v1/environments", json={
        "id": "lab", "product_id": "demo", "title": "Lab",
        "host": "127.0.0.1", "port": 5432,
    }).raise_for_status()
    submitted = client.post("/api/v1/operations", json={
        "environment_id": "lab", "action": "tests.demo",
        "target": "demo.case", "acknowledge_change": True,
    })
    assert submitted.status_code == 202, submitted.text
    run_task(app.state.store, settings, queued[0])
    assert app.state.store.get_task(queued[0])["status"] == "SUCCEEDED"
    assert app.state.store.list_results("lab")[0]["status"] == "PASS"
    assert (settings.data_dir / "runs" / "demo.case" / "events.jsonl").is_file()


def test_fixture_cleanup_runs_when_case_fails(tmp_path, monkeypatch):
    context = CaseContext("demo.fixtures", tmp_path,
                          environment={"nodes": {"primary": {"host": "db", "port": 5432}}})
    calls = []
    context.defer_cleanup(lambda: calls.append("cleanup"))

    class FailingCase:
        def run(self, _context):
            raise AssertionError("expected failure")

    result = RegressionEngine().run(FailingCase(), context)
    assert result.verdict == "FAIL"
    assert calls == ["cleanup"]


def test_platform_process_and_tcp_protocol_lifecycle(tmp_path):
    import socket
    import time

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    source = (
        "import socket,sys\n"
        "s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
        "s.bind(('127.0.0.1',int(sys.argv[1]))); s.listen()\n"
        "while True:\n"
        " c,_=s.accept(); data=c.recv(1024); c.sendall(b'ACK:'+data); c.close()\n"
    )
    context = CaseContext("demo.protocol", tmp_path)
    process = context.start_process([sys.executable, "-c", source, str(port)],
                                    ready_host="127.0.0.1", ready_port=port)
    response = context.tcp_exchange("127.0.0.1", port, b"probe", expected_bytes=9)
    assert response == b"ACK:probe"
    context.cleanup_fixtures()
    assert process.poll() is not None
    assert any("protocol-" in item for item in context.evidence)
    assert any(item.endswith("process-1.log") for item in context.evidence)
