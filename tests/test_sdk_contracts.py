"""Behavioral contracts for SDK v2: assertions, isolation, SQL and lifecycles."""

import ast
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from platform_app.product_catalog import ProductManifestError, load_manifest
from platform_regress import sdk
from platform_regress.sdk import (
    COMMON_REQUIREMENTS,
    Blocked,
    Cancelled,
    CaseContext,
    CaseFailure,
    RegressionEngine,
    RequirementRegistry,
    RuntimeBinding,
    RuntimeExecutorCase,
)


def events(context):
    return [
        json.loads(line)
        for line in (context.output_dir / "events.jsonl").read_text().splitlines()
    ]


def test_failed_check_records_assertion_and_controls_verdict(tmp_path):
    class Case:
        def run(self, context):
            context.check("count", "Count matches", False, expected=2, actual=1)
            return True

    context = CaseContext("demo.check", tmp_path)
    result = RegressionEngine().run(Case(), context)
    assert result.verdict == result.business_verdict == "FAIL"
    assertion = next(e for e in events(context) if e["kind"] == "step.finished")
    assert assertion["payload"] == {
        "step_key": "count",
        "title": "Count matches",
        "status": "FAIL",
        "details": {"expected": 2, "actual": 1},
    }
    assert result.reason == "Count matches: 预期 2，实际 1"


def test_step_is_a_fact_and_check_is_an_assertion(tmp_path):
    context = CaseContext("demo.facts", tmp_path)
    context.step("expected-error", "Expected rejection observed", status="FAIL")
    context.check(
        "rejection",
        "Rejection was expected",
        True,
        expected="rejected",
        actual="rejected",
    )
    with pytest.raises(TypeError):
        context.check("invalid", "Invalid outcome", 1)
    with pytest.raises(ValueError):
        context.step("invalid", "Invalid status", status="SUCCESS")


def test_requirements_are_owned_and_frozen():
    first = COMMON_REQUIREMENTS.copy()
    second = COMMON_REQUIREMENTS.copy()
    calls = []
    first.register(
        "custom", lambda c, r: calls.append("first"), before="system_time_control"
    )
    second.register("custom", lambda c, r: calls.append("second"))
    assert first.keys.index("custom") < first.keys.index("system_time_control")
    assert "custom" not in COMMON_REQUIREMENTS.keys
    with pytest.raises(ValueError, match="duplicate"):
        first.register("custom", lambda c, r: None)
    first.freeze()
    with pytest.raises(RuntimeError, match="frozen"):
        first.register("extra", lambda c, r: None)
    with pytest.raises(RuntimeError, match="frozen"):
        COMMON_REQUIREMENTS.register("extra", lambda c, r: None)
    registry = RequirementRegistry()
    with pytest.raises(KeyError):
        registry.register("extra", lambda c, r: None, before="missing")
    assert registry.keys == ()


def test_reloading_product_does_not_pollute_requirements():
    path = Path(__file__).parents[1] / "products/fbase-database/cases.py"
    before = COMMON_REQUIREMENTS.keys
    modules = []
    for i in range(2):
        spec = importlib.util.spec_from_file_location(f"_sdk_product_{i}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    assert COMMON_REQUIREMENTS.keys == before
    assert all(m.PRODUCT_REQUIREMENTS.keys.count("writable_node") == 1 for m in modules)
    assert modules[0].PRODUCT_REQUIREMENTS is not modules[1].PRODUCT_REQUIREMENTS


@pytest.mark.parametrize("value", ["1", "3", None, 2])
def test_product_requires_exact_sdk_version(tmp_path, value):
    import yaml

    root = tmp_path / "demo"
    root.mkdir()
    (root / "product.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "demo",
                "title": "Demo",
                "plugin_api": "v1",
                "capabilities": {"tests": "demo"},
                "regression_sdk": value,
            }
        )
    )
    with pytest.raises(ProductManifestError, match="regression_sdk"):
        load_manifest(root)


def test_no_obsolete_contract_entry_points_or_product_private_imports():
    assert sdk.SDK_VERSION == "2"
    assert not hasattr(sdk, "ProductCase")
    assert not hasattr(sdk, "CaseRuntime")
    assert not hasattr(sdk, "RegressionContext")
    assert not hasattr(CaseContext, "tcp_probe")
    root = Path(__file__).parents[1] / "products"
    private = {
        "platform_regress.engine",
        "platform_regress.contracts",
        "platform_regress.runtime",
        "platform_regress.sql",
        "platform_regress.commands",
        "platform_regress.fixtures",
        "platform_regress.suites.executor",
        "platform_regress.environment.context",
        "platform_regress.evidence.recorder",
    }
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in private, f"{path}:{node.lineno}"


@pytest.mark.parametrize("environment", [{"nodes": []}, {"users": "bad"}, []])
def test_environment_rejects_invalid_containers(tmp_path, environment):
    with pytest.raises(ValueError, match="mapping"):
        CaseContext("demo.invalid", tmp_path, environment=environment)


@pytest.mark.parametrize("port", [True, 0, 65536, "5432"])
def test_endpoint_errors_are_precondition_blockers(tmp_path, port):
    context = CaseContext(
        "demo.endpoint",
        tmp_path,
        environment={
            "nodes": {"primary": {"host": "localhost", "port": port}},
        },
    )
    with pytest.raises(Blocked):
        context.node_endpoint()


class SqlConnection:
    def __init__(self):
        self.calls = []
        self.transaction_events = []
        self.closed = False
        self.rows = [(7, True, None)]
        self.description = [
            type("Column", (), {"name": key})() for key in ("n", "flag", "missing")
        ]
        self.statusmessage = "SELECT 1"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, query, parameters=None):
        self.calls.append((query, parameters))
        if query == "BAD":
            raise RuntimeError("query rejected")

    def fetchall(self):
        return self.rows

    @contextmanager
    def transaction(self):
        self.transaction_events.append("begin")
        try:
            yield self
        except BaseException:
            self.transaction_events.append("rollback")
            raise
        else:
            self.transaction_events.append("commit")


@pytest.fixture
def sql_context(tmp_path, monkeypatch):
    connections = []
    connect_options = []

    def connect(**kwargs):
        connect_options.append(kwargs)
        connection = SqlConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr("platform_regress.sql.psycopg.connect", connect)
    context = CaseContext(
        "demo.sql",
        tmp_path,
        environment={
            "nodes": {"primary": {"host": "localhost", "port": 5432}},
        },
    )
    return context, connections, connect_options


def test_sql_session_reuses_connection_and_preserves_types(sql_context):
    context, connections, options = sql_context
    with context.sql_session(
        "primary", preserve_types=True, statement_timeout_seconds=25
    ) as session:
        first = session.execute("SELECT %s", (7,))
        second = session.execute("SELECT 7")
        assert first.rows == second.rows == ((7, True, None),)
    assert len(connections) == 1
    assert connections[0].closed
    assert options[0]["options"] == "-c statement_timeout=25000"
    assert connections[0].calls == [("SELECT %s", (7,)), ("SELECT 7", None)]
    artifact = json.loads((context.output_dir / context.evidence[0]).read_text())
    assert artifact["parameters"] == [7]
    assert len(set(context.evidence)) == 2
    with pytest.raises(RuntimeError, match="closed"):
        session.execute("SELECT 7")


def test_sql_defaults_remain_textual_and_timeout_can_be_disabled(sql_context):
    context, connections, options = sql_context
    assert context.sql("primary", "SELECT 7").rows == (("7", "True", None),)
    with context.sql_session("primary", statement_timeout_seconds=None) as session:
        session.execute("SELECT 7")
    assert options[-1]["options"] == "-c statement_timeout=0"
    assert len(connections) == 2


def test_sql_transaction_rolls_back_failure_and_can_commit_next(sql_context):
    context, connections, _ = sql_context
    with context.sql_session("primary") as session:
        with pytest.raises(CaseFailure):
            with session.transaction():
                session.execute("SELECT 7")
                context.check("n", "Count", False, expected=8, actual=7)
        with session.transaction():
            session.execute("SELECT 7")
    assert connections[0].transaction_events == ["begin", "rollback", "begin", "commit"]


def test_sql_transaction_checks_cancellation_before_commit(sql_context):
    context, connections, _ = sql_context
    cancelled = False
    context._cancelled = lambda: cancelled
    with context.sql_session("primary") as session:
        with pytest.raises(Cancelled):
            with session.transaction():
                session.execute("SELECT 7")
                cancelled = True
    assert connections[0].transaction_events == ["begin", "rollback"]


def test_query_failure_and_connection_failure_both_keep_evidence(
    sql_context, monkeypatch
):
    context, _, _ = sql_context
    with pytest.raises(RuntimeError, match="query rejected"):
        context.sql("primary", "BAD")

    def fail(**kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr("platform_regress.sql.psycopg.connect", fail)
    with pytest.raises(OSError, match="refused"):
        context.sql("primary", "SELECT 7")
    errors = [
        json.loads((context.output_dir / ref).read_text()) for ref in context.evidence
    ]
    assert [item["error"] for item in errors] == [
        "query rejected",
        "connection refused",
    ]
    assert [e["kind"] for e in events(context)].count("sql.failed") == 2


@pytest.mark.parametrize("timeout", [0, -1])
def test_sql_timeout_validation_prevents_connect(sql_context, timeout):
    context, connections, _ = sql_context
    with pytest.raises(ValueError):
        context.sql("primary", "SELECT 7", statement_timeout_seconds=timeout)
    assert connections == []


class Runtime:
    def __init__(self, calls, fail_setup=False, fail_teardown=False):
        self.calls = calls
        self.fail_setup = fail_setup
        self.fail_teardown = fail_teardown

    def __enter__(self):
        self.calls.append("enter")
        if self.fail_setup:
            raise Blocked("not ready")
        return self

    def __exit__(self, exc_type, value, traceback):
        self.calls.append("exit")
        if self.fail_teardown:
            raise OSError("cleanup failed")

    def finish(self, status, reason=None):
        self.calls.append(("finish", status))

    def stop(self):
        self.calls.append("stop")


def make_runtime_case(
    calls,
    *,
    teardown_before_finish=True,
    fail_setup=False,
    fail_teardown=False,
    failure=None,
):
    def execute(context, runtime):
        runtime.calls.append("execute")
        if failure:
            raise failure

    def resolve(context):
        return RuntimeBinding(
            spec="case",
            runtime_factory=lambda ctx, spec: Runtime(calls, fail_setup, fail_teardown),
            execute=execute,
            pass_reason="verified",
            teardown_before_finish=teardown_before_finish,
            finalize=lambda ctx, runtime: calls.append("finalize"),
        )

    return RuntimeExecutorCase("demo.runtime", resolve)


@pytest.mark.parametrize("before", [True, False])
def test_runtime_order_and_sequential_reuse(tmp_path, before):
    calls = []
    case = make_runtime_case(calls, teardown_before_finish=before)
    for i in range(2):
        assert (
            RegressionEngine()
            .run(case, CaseContext("demo.runtime", tmp_path / str(i)))
            .verdict
            == "PASS"
        )
    middle = ["exit", ("finish", "PASS")] if before else [("finish", "PASS"), "exit"]
    assert calls == (["enter", "execute"] + middle + ["finalize"]) * 2


@pytest.mark.parametrize(
    "failure,verdict",
    [
        (CaseFailure("bad"), "FAIL"),
        (RuntimeError("broken"), "ERROR"),
        (Blocked("not ready"), "BLOCKED"),
        (Cancelled("cancelled"), "CANCELLED"),
    ],
)
def test_runtime_failures_preserve_verdict_and_clean_once(tmp_path, failure, verdict):
    calls = []
    case = make_runtime_case(calls, failure=failure)
    result = RegressionEngine().run(case, CaseContext("demo.runtime", tmp_path))
    assert result.verdict == verdict
    assert calls.count("exit") == calls.count("finalize") == 1


def test_runtime_partial_setup_is_cleaned_and_can_be_retried(tmp_path):
    calls = []
    case = make_runtime_case(calls, fail_setup=True)
    for i in range(2):
        result = RegressionEngine().run(
            case, CaseContext("demo.runtime", tmp_path / str(i))
        )
        assert result.verdict == "BLOCKED"
    assert calls == ["enter", "exit", "finalize"] * 2


def test_runtime_teardown_failure_is_a_cleanup_error(tmp_path):
    calls = []
    result = RegressionEngine().run(
        make_runtime_case(calls, fail_teardown=True),
        CaseContext("demo.runtime", tmp_path),
    )
    assert result.verdict == "ERROR"
    assert result.cleanup.status == "ERROR"
    assert result.business_verdict == "PASS"
    assert calls == ["enter", "execute", "exit", ("finish", "PASS"), "finalize"]


def test_context_cannot_be_reused_for_another_execution(tmp_path):
    class Case:
        def run(self, context):
            return True

    context = CaseContext("demo.once", tmp_path)
    assert RegressionEngine().run(Case(), context).verdict == "PASS"
    before = (tmp_path / "result.json").read_bytes()
    with pytest.raises(RuntimeError, match="one execution"):
        RegressionEngine().run(Case(), context)
    assert (tmp_path / "result.json").read_bytes() == before


def test_result_status_types_reject_unknown_values():
    assert sdk.Verdict.FAIL == "FAIL"
    assert sdk.CleanupStatus.ERROR == "ERROR"
    with pytest.raises(ValueError):
        sdk.CleanupResult("SUCCESS")


def test_public_exports_are_complete():
    assert all(hasattr(sdk, name) for name in sdk.__all__)
    assert {"SetupCase", "CleanupCase", "Verdict", "ReportRuntime"} <= set(sdk.__all__)


def test_report_runtime_preserves_baseline_report_bytes(tmp_path):
    from datetime import datetime

    spec = sdk.ReportSpec(
        "sample", "demo.sample", "Report preservation", "demo", ("section 1",)
    )
    runtime = sdk.ReportRuntime(
        tmp_path, spec, output_root=tmp_path / "output", context_data={}
    )
    runtime.started_at = datetime(2026, 9, 30, 10, 0, 0)
    runtime.finished_at = datetime(2026, 9, 30, 10, 0, 1)
    runtime.record_step("Check count", "SELECT 1", "1 row", "1 row", "PASS")
    runtime.write_report("PASS", "verified")
    baseline = Path(__file__).parent / "assets/sdk-report-v2.txt"
    assert (runtime.run_root / "report.txt").read_bytes() == baseline.read_bytes()
