"""FBase cases migrated to the shared platform regression engine."""

import importlib.util
import json
import re
import uuid
from pathlib import Path

from platform_regress import (Blocked, run_declared_steps, run_sql_step,
                              SUPPORTED_COMMAND_ASSERTIONS, SUPPORTED_SQL_ASSERTIONS)
from platform_regress.requirements import evaluate_requirements, register_requirement
import psycopg


def _load_isolated_module():
    """Load this product's sibling module without a package import."""
    path = Path(__file__).parent / "isolated.py"
    spec = importlib.util.spec_from_file_location("_fbase_isolated", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 FBase 隔离集群 fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_fixtures_module():
    """Load this product's fixture registry without a package import."""
    path = Path(__file__).parent / "fixtures.py"
    spec = importlib.util.spec_from_file_location("_fbase_fixtures", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 FBase fixture 注册表")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


isolated = _load_isolated_module()
fixtures_mod = _load_fixtures_module()


def require_nodes(context, names):
    """Treat missing topology or unreachable primaries as preflight blockers."""
    if context.environment.get("topology_error"):
        raise Blocked("多活拓扑不可用: " + context.environment["topology_error"])
    for node in names:
        try:
            context.sql(node, "SELECT 1")
        except Blocked:
            raise
        except Exception as exc:
            raise Blocked(f"多活节点 {node} 不可连接: {exc}") from exc


class MmrRuntimePrerequisites:
    """Read-only checks from the MMR installation transfer case."""

    TARGET = "mmr.installation.runtime_prerequisites"

    def setup(self, context):
        require_nodes(context, ("mmr1", "mmr2"))

    @staticmethod
    def check(context, key, title, node, query, expected):
        actual = context.sql(node, query).rows
        if actual != expected:
            context.step(key, title, status="FAIL", details={
                "expected": expected, "actual": actual, "node": node,
            })
            raise AssertionError(f"{title}: 预期 {expected}，实际 {actual}")
        context.step(key, title, details={"node": node, "rows": len(actual)})

    def run(self, context):
        extension_sql = (
            "SELECT extname::text,extversion::text FROM pg_extension "
            "WHERE extname IN ('fdd_mmr','fb_license') ORDER BY extname"
        )
        required_sql = (
            "SELECT current_setting('wal_level')::text,"
            "current_setting('track_commit_timestamp')::text,"
            "current_setting('fdd.running_databases')::text,"
            "(position('fdd_mmr' in current_setting('shared_preload_libraries')) > 0)::text,"
            "(current_setting('listen_addresses') IN ('*','127.0.0.1','localhost'))::text"
        )
        recommended_sql = (
            "SELECT current_setting('log_min_messages')::text,"
            "current_setting('log_destination')::text,"
            "current_setting('logging_collector')::text,"
            "current_setting('logical_decoding_work_mem')::text,"
            "current_setting('fdd.log_conflicts_to_table')::text,"
            "current_setting('fdd.search_dead_tup_time_interval')::text,"
            "(current_setting('max_logical_replication_workers')::int >= 4)::text,"
            "(current_setting('max_sync_workers_per_subscription')::int >= 2)::text,"
            "(current_setting('max_replication_slots')::int >= 10)::text,"
            "(current_setting('max_wal_senders')::int >= 10)::text"
        )
        for node in ("mmr1", "mmr2"):
            self.check(context, node + "-extensions", "检查多活扩展", node,
                       extension_sql, (("fb_license", "1.0"), ("fdd_mmr", "2.0")))
            self.check(context, node + "-required", "检查多活强制启动配置", node,
                       required_sql, (("logical", "on", "postgres", "true", "true"),))
            self.check(context, node + "-recommended", "检查复制与日志参数", node,
                       recommended_sql, (("log", "stderr,csvlog", "on", "64MB", "on", "30s",
                                         "true", "true", "true", "true"),))
        self.check(
            context, "hba", "检查 host trust 规则", "mmr1",
            "SELECT EXISTS (SELECT 1 FROM pg_hba_file_rules "
            "WHERE error IS NULL AND type='host' AND database=ARRAY['all'] "
            "AND user_name=ARRAY['all'] AND auth_method='trust')::text",
            (("true",),),
        )
        self.check(
            context, "members", "检查两成员多活集群", "mmr1",
            "SELECT (count(*) >= 2)::text,bool_and(is_abnormal='OK')::text,"
            "bool_and(nodestate='ACTIVE')::text FROM fdd.show_node_info(true,false)",
            (("true", "true", "true"),),
        )
        return True


class MmrClusterVerificationBasic:
    """Verify all three MMR members and the UDF's local/global modes."""

    TARGET = "mmr.cluster_verification.basic"

    LOCAL_SUMMARY_SQL = """
SELECT count(*)::text,
       bool_and(nodestate = 'ACTIVE')::text,
       bool_and(real_nodestate = 'ACTIVE')::text,
       bool_and(is_abnormal = 'OK')::text,
       bool_and(detail = 'OK')::text
FROM fdd.show_node_info(false, false)
"""
    ALL_SUMMARY_SQL = """
SELECT count(*)::text,
       bool_and(nodestate = 'ACTIVE')::text,
       bool_and(real_nodestate = 'ACTIVE')::text,
       bool_and(is_abnormal = 'OK')::text,
       bool_and(detail = 'OK')::text
FROM fdd.show_node_info(true, false)
"""

    def setup(self, context):
        require_nodes(context, ("mmr1", "mmr2", "mmr3"))

    @staticmethod
    def show_rows(context, key, title, node, query):
        rows = context.sql(node, query).rows
        values = {value for row in rows for value in row}
        if not rows or "ACTIVE" not in values or "OK" not in values:
            context.step(key, title, status="FAIL", details={"node": node, "actual": rows})
            raise AssertionError(f"{title}: 未看到 ACTIVE 和 OK 状态")
        context.step(key, title, details={"node": node, "rows": len(rows)})

    def run(self, context):
        for node in ("mmr1", "mmr2", "mmr3"):
            self.show_rows(
                context, node + "-local", "检查本地多活节点状态", node,
                "SELECT nodeid,nodename,nodestate,real_nodestate,is_abnormal,detail "
                "FROM fdd.show_node_info(false, false)",
            )
            MmrRuntimePrerequisites.check(
                context, node + "-local-summary", "核对本地节点状态", node,
                self.LOCAL_SUMMARY_SQL, (("1", "true", "true", "true", "true"),),
            )
        self.show_rows(
            context, "all-nodes", "检查全集群节点状态", "mmr1",
            "SELECT nodeid,nodename,nodestate,real_nodestate,is_abnormal,detail "
            "FROM fdd.show_node_info(true, false) ORDER BY nodeid",
        )
        MmrRuntimePrerequisites.check(
            context, "all-summary", "核对全集群三成员状态", "mmr1",
            self.ALL_SUMMARY_SQL, (("3", "true", "true", "true", "true"),),
        )
        MmrRuntimePrerequisites.check(
            context, "differences", "确认全集群无异常差异", "mmr1",
            "SELECT nodeid,nodename,is_abnormal,detail "
            "FROM fdd.show_node_info(true, true) ORDER BY nodeid",
            (),
        )
        return True


class MacMetadataAccessRestrictions:
    """Preserve the four DBA denial checks from the legacy MAC case."""

    TARGET = "mac.separation_of_duties.dba_metadata_access_restrictions"

    def setup(self, context):
        if context.environment.get("topology_error"):
            raise Blocked("等保拓扑不可用: " + context.environment["topology_error"])
        try:
            rows = context.sql("primary", (
                "SELECT (NOT pg_is_in_recovery())::text, "
                "(EXISTS (SELECT 1 FROM pg_extension WHERE extname='fbase_mac'))::text, "
                "(position('fbase_mac' in current_setting('shared_preload_libraries')) > 0)::text, "
                "current_setting('fdb.separate_user')::text"
            )).rows
        except Exception as exc:
            raise Blocked(f"等保前置条件无法读取: {exc}") from exc
        if rows != (("true", "true", "true", "on"),):
            raise Blocked(f"等保前置条件不满足: {rows}")
        context.step("preflight", "确认等保扩展、三权分立配置和可写主节点")

    @staticmethod
    def expect_denied(context, key, title, query, expected, sqlstate=None):
        try:
            context.sql("primary", query)
        except psycopg.Error as exc:
            actual_state = getattr(exc, "sqlstate", None)
            if expected in str(exc).lower() and (sqlstate is None or actual_state == sqlstate):
                context.step(key, title, details={"sqlstate": actual_state, "error": str(exc)})
                return
            context.step(key, title, status="FAIL", details={
                "expected_error": expected, "expected_sqlstate": sqlstate,
                "actual_error": str(exc), "actual_sqlstate": actual_state,
            })
            raise AssertionError(f"{title}: 错误与旧用例预期不符") from exc
        context.step(key, title, status="FAIL", details={"expected_error": expected, "actual": "SQL 成功"})
        raise AssertionError(f"{title}: SQL 意外成功")

    def run(self, context):
        checks = (
            ("mac-policy-read", "DBA 不能读取强制访问控制元数据表",
             "SELECT * FROM fdb_mac.policy", "permission denied", "42501"),
            ("audit-rule-read", "DBA 不能读取审计元数据表",
             "SELECT * FROM fdb_audit.audit_rule", "permission denied", "42501"),
            ("mac-policy-rename", "DBA 不能重命名强制访问控制元数据表",
             "BEGIN; ALTER TABLE fdb_mac.policy RENAME TO fbase_regress_policy; ROLLBACK",
             "rename metadata relation", None),
            ("audit-rule-rename", "DBA 不能重命名审计元数据表",
             "BEGIN; ALTER TABLE fdb_audit.audit_rule RENAME TO fbase_regress_audit_rule; ROLLBACK",
             "rename metadata relation(audit_rule)", None),
        )
        for key, title, query, expected, sqlstate in checks:
            self.expect_denied(context, key, title, query, expected, sqlstate)
        return True


# These four product cases share the same cluster preflight and assertion
# shape. Validate their exported definitions before they become executable;
# a future fixture or non-transactional step must not silently inherit this
# runner's cleanup assumptions.
MAC_METADATA_DENIAL_TARGETS = frozenset({
    "mac.separation_of_duties.dba_metadata_function_restrictions",
    "mac.separation_of_duties.dba_metadata_index_restrictions",
    "mac.separation_of_duties.dba_metadata_sequence_restrictions",
    "mac.separation_of_duties.dba_metadata_view_restrictions",
})


class MacMetadataDenialCase(MacMetadataAccessRestrictions):
    def __init__(self, definition):
        self.definition = definition

    def run(self, context):
        for index, step in enumerate(self.definition["steps"], 1):
            assertion = step["assertion"]
            self.expect_denied(
                context, f"denial-{index}", step["title"], step["sql"],
                assertion["message_contains"],
            )
        return True


def load_mac_metadata_denials():
    path = Path(__file__).parent / "regression" / "cases.json"
    definitions = {case["id"]: case for case in json.loads(path.read_text(encoding="utf-8"))["cases"]}
    selected = {}
    for target in MAC_METADATA_DENIAL_TARGETS:
        case = definitions[target]
        if case.get("fixtures") != ["cluster"] or case.get("session"):
            raise ValueError(f"等保用例不符合无状态执行契约: {target}")
        for step in case["steps"]:
            sql = step.get("sql", "")
            if (step.get("type") != "sql" or step.get("user") not in {"postgres", "{env.user}"}
                    or step.get("assertion", {}).get("type") != "sql_fails"
                    or not sql.startswith("BEGIN;") or not sql.rstrip().endswith("ROLLBACK")):
                raise ValueError(f"等保用例存在未受保护的步骤: {target}")
        selected[target] = case
    return selected


MAC_METADATA_DENIALS = load_mac_metadata_denials()


MMR_READ_ONLY_TARGETS = frozenset({
    "mmr.cluster_verification.same_priority_errors",
})


class MmrReadOnlyDeclarativeCase:
    """Run read-only exported MMR SQL with platform-owned assertions."""

    def __init__(self, definition):
        self.definition = definition

    def setup(self, context):
        if context.environment.get("topology_error"):
            raise Blocked("多活拓扑不可用: " + context.environment["topology_error"])
        nodes = {step["node"].split(":")[-1] for step in self.definition["steps"]}
        require_nodes(context, sorted(nodes))

    def run(self, context):
        for index, step in enumerate(self.definition["steps"], 1):
            run_sql_step(context, step, index, step["node"].split(":")[-1])
        return True


class DeclarativeSqlCase:
    """Execute a catalog case whose complete contract is platform SQL steps.

    This is intentionally strict: only a plain cluster fixture, postgres
    connections and SDK-supported SQL assertions qualify.  Cases needing
    settings, roles, sessions or cluster actions remain explicit product
    implementations until those fixture protocols are available in the SDK.
    """

    def __init__(self, definition):
        self.definition = definition

    def setup(self, context):
        nodes = sorted({step["node"].split(":")[-1]
                        for step in self.definition["steps"]})
        require_nodes(context, nodes)

    def run(self, context):
        for index, step in enumerate(self.definition["steps"], 1):
            run_sql_step(context, step, index, step["node"].split(":")[-1])
        return True


class FixtureSqlCase(DeclarativeSqlCase):
    """Platform-native SQL case with disposable role/table fixtures."""

    def setup(self, context):
        super().setup(context)
        node = self.definition["steps"][0]["node"].split(":")[-1]
        for fixture in self.definition.get("fixtures", [])[1:]:
            if fixture.get("type") == "settings":
                node = fixture.get("node", self.definition["steps"][0].get("node", "primary"))
                node = node.split(":")[-1]
                user = context.expand(fixture.get("user"))
                for name, value in fixture.get("values", {}).items():
                    context.set_setting(node, name, str(value), user=user)
                continue
            if fixture.get("type") != "roles":
                if fixture.get("type") == "table":
                    context.defer_drop_table(node, fixture["name"])
                continue
            for role in fixture.get("create", []):
                attributes = "LOGIN" if role.get("login") else role.get("attributes", "")
                context.create_role(node, role["name"], attributes)


def load_mmr_read_only_cases():
    path = Path(__file__).parent / "regression" / "cases.json"
    definitions = {case["id"]: case for case in json.loads(path.read_text(encoding="utf-8"))["cases"]}
    selected = {}
    for target in MMR_READ_ONLY_TARGETS:
        case = definitions[target]
        if case.get("fixtures") != ["cluster"] or case.get("session"):
            raise ValueError(f"多活用例不符合只读执行契约: {target}")
        for step in case["steps"]:
            if (step.get("type") != "sql" or not step.get("sql", "").lstrip().upper().startswith(
                    ("SELECT ", "WITH ")) or step.get("assertion", {}).get("type") not in {
                        "rows_equal", "output_contains_text",
                    }):
                raise ValueError(f"多活用例存在非只读步骤: {target}")
        selected[target] = case
    return selected


MMR_READ_ONLY_CASES = load_mmr_read_only_cases()


def load_native_sql_cases():
    """Batch-register only cases fully expressible by the platform SDK."""
    path = Path(__file__).parent / "regression" / "cases.json"
    definitions = {case["id"]: case for case in json.loads(
        path.read_text(encoding="utf-8"))["cases"]}
    supported = {"rows_equal", "output_contains_text", "output_contains", "sql_error", "sql_fails",
                 "command_succeeds"}
    # These targets intentionally stay on the legacy executor until their
    # product-specific privilege/session semantics have a platform contract.
    unsafe = {"mac.audit.log_access_restrictions"}
    allowed_settings = {"mac.audit.server_audit_logs"}
    selected = {}
    for target, definition in definitions.items():
        if target in unsafe:
            continue
        steps = definition.get("steps") or []
        fixtures = definition.get("fixtures") or []
        if (any(isinstance(item, dict) and item.get("type") == "settings"
                for item in fixtures) and target not in allowed_settings):
            continue
        valid_fixture = fixtures == ["cluster"] or (
            len(fixtures) >= 2 and fixtures[0] == "cluster"
            and all(isinstance(item, dict) and item.get("type") in {"roles", "table", "settings"}
                    for item in fixtures[1:])
        )
        if (not valid_fixture or not steps
                or not all(
                    (step.get("type") == "cluster_action" and
                     step.get("action") == "reload" and
                     step.get("assertion", {}).get("type") == "command_succeeds")
                    or (step.get("type") == "wait_sql"
                        and step.get("assertion", {}).get("type") == "rows_equal")
                    or (step.get("type") == "sql"
                        and step.get("user", "postgres") in {"postgres", "{env.user}"}
                        and step.get("assertion", {}).get("type") in supported)
                    for step in steps)):
            continue
        selected[target] = definition
    return selected


NATIVE_SQL_CASES = load_native_sql_cases()
FIXTURE_TARGETS = frozenset(target for target, definition in NATIVE_SQL_CASES.items()
                             if len(definition.get("fixtures", [])) > 1)


def _scalar(context, node, sql):
    rows = context.sql(node, sql).rows
    return rows[0][0] if rows and rows[0] else None


@register_requirement("writable_node", before="system_time_control")
def _require_writable_node(context, requirements):
    """Product gate: a healthy writable node (pg_isready + recovery/health row)."""
    if not requirements.get("writable_node"):
        return
    cluster_name = context.environment.get("cluster_name") or \
        context.environment.get("cluster")
    node = context.resolve_node(requirements.get("node") or "primary")
    row = fixtures_mod.node_status_row(context, node)
    if not row or row[5] != "false" or row[6] != "healthy":
        raise Blocked("cluster %s 没有健康的可写节点" % cluster_name)


def check_requirements(context, definition):
    """Mirror the legacy requirement gate exactly (evaluate_requirements)."""
    evaluate_requirements(context, definition.get("requirements") or {})


class ExportedCommandCase:
    """Run one exported catalog case through the platform SDK natively.

    Every supported step/fixture/assertion shape is dispatched verbatim onto
    the platform primitives: psql subprocess SQL, command transport,
    background processes, node/cluster lifecycle actions, system-time shifts,
    declared fixtures and shared sessions all behave exactly like the legacy
    executor — including halt/BLOCKED and cleanup ordering.
    """

    def __init__(self, definition):
        # Legacy cases derived object names from a module-level uuid token
        # (evaluated once per executor process).  The exporter froze that
        # token into the catalog; "runtime_tokens" marks the literals to
        # re-randomize at case construction so every run gets fresh names
        # exactly like the legacy per-process TOKEN evaluation.
        tokens = definition.get("runtime_tokens") or []
        if tokens:
            rendered = json.dumps(definition, ensure_ascii=False)
            for token in tokens:
                rendered = rendered.replace(str(token), uuid.uuid4().hex[:12])
            definition = json.loads(rendered)
        self.definition = definition
        session = definition.get("session") or {}
        self.session_key = session.get("key")
        self.session_order = session.get("order", 0)
        self.default_enabled = definition.get("default_enabled", True)
        self._collectors = []
        self._core_before = {}

    def _isolated_mmr(self):
        """Legacy _uses_isolated_mmr_topology: only this fixture relaxes the
        shared-cluster health gates."""
        return any(isinstance(fixture, dict) and
                   fixture.get("type") == "isolated_mmr_node_creation"
                   for fixture in self.definition.get("fixtures") or [])

    def setup_session(self, context):
        """Initialize this case's shared session fixtures and steps once.

        Runs against the session-owned context whose cleanup drains when the
        session key changes or the run ends. Any failure propagates to the
        CLI, which stores it as the session error blocking every member case.
        """
        spec = self.definition["session"]
        session_def = {"id": "_session.%s" % spec["key"],
                       "steps": spec.get("steps") or []}
        for fixture in spec.get("fixtures") or []:
            fixtures_mod.setup_fixture(context, session_def, fixture)
        try:
            run_declared_steps(
                context, session_def["steps"],
                before_command=lambda argv: isolated.release_port_for_command(
                    context, session_def, argv),
                definition=session_def)
        except AssertionError as exc:
            raise RuntimeError("共享会话初始化步骤失败: %s" % exc)

    def setup(self, context):
        definition = self.definition
        # The core snapshot precedes every gate, exactly like the legacy
        # CoreCollector which starts before the blocker is consulted.
        roots = fixtures_mod.core_file_roots(context, context.output_dir)
        self._core_before = fixtures_mod._core_files(roots)

        def collect_cores():
            cores = fixtures_mod.collect_new_core_files(
                context, self._core_before, context.output_dir)
            if cores:
                context.attach_text("core-files.txt", "\n".join(cores) + "\n")

        context.defer_cleanup(collect_cores, priority=-600)
        if context.environment.get("topology_error"):
            raise Blocked("拓扑不可用: " + context.environment["topology_error"])
        isolated_mmr = self._isolated_mmr()
        if not isolated_mmr:
            blocker = fixtures_mod.environment_blocker(context)
            if blocker:
                raise Blocked(blocker)
            blocker, names = fixtures_mod.declared_nodes_blocker(
                context, definition)
            if blocker:
                raise Blocked(blocker)
        basic = dict(definition.get("requirements") or {})
        basic.pop("settings", None)
        if isolated_mmr:
            basic.pop("writable_node", None)
        check_requirements(context, dict(definition, requirements=basic))
        session_error = context.values.get("session_error")
        if session_error:
            raise Blocked(session_error)
        names = self._collector_nodes(context)
        self._collectors = fixtures_mod.start_log_collectors(
            context, names, context.output_dir)
        collectors = self._collectors

        def finish_collectors():
            errors = fixtures_mod.finish_log_collectors(context, collectors)
            if errors:
                raise RuntimeError(errors)

        context.defer_cleanup(finish_collectors, priority=-500)
        blocker, details = fixtures_mod.evaluate_setting_requirements(
            context, definition.get("requirements") or {})
        if details:
            context.attach_text(
                "setting-requirements.json",
                json.dumps(details, ensure_ascii=False, indent=2) + "\n")
        if blocker:
            raise Blocked(blocker)
        try:
            for fixture in definition.get("fixtures") or []:
                try:
                    fixtures_mod.setup_fixture(context, definition, fixture)
                except fixtures_mod.SafetyError as exc:
                    raise Blocked(str(exc))
        finally:
            fixture_settings = context.values.get("postgresql_settings")
            if fixture_settings:
                context.attach_text(
                    "fixture-settings.json",
                    json.dumps(fixture_settings, ensure_ascii=False, indent=2) + "\n")

    def _collector_nodes(self, context):
        """Declared nodes for server-log collection, tolerating gaps."""
        names = []
        try:
            _, names = fixtures_mod.declared_nodes_blocker(
                context, self.definition)
        except Exception:
            names = []
        if names:
            return names
        for selector in fixtures_mod.declared_nodes(self.definition):
            try:
                name = context.resolve_node(selector)
            except Exception:
                continue
            if name not in names:
                names.append(name)
        return names

    def run(self, context):
        definition = self.definition
        failure = ""
        try:
            run_declared_steps(
                context, definition["steps"],
                before_command=lambda argv: isolated.release_port_for_command(
                    context, definition, argv),
                definition=definition)
        except AssertionError as exc:
            failure = str(exc)
        # Fixture cleanup runs inside run() so its errors keep the legacy
        # FAILED verdict instead of surfacing as a cleanup ERROR.
        cleanup_error = ""
        try:
            context.cleanup_fixtures()
        except Exception as exc:
            cleanup_error = str(exc)
        parts = [part for part in (failure, cleanup_error) if part]
        if parts:
            raise AssertionError("; ".join(parts))
        return True


_STEP_TYPES = frozenset({
    "sql", "command", "wait_sql", "background_sql", "wait_background_sql",
    "cluster_action", "node_action", "system_time_shift",
})
_ALL_ASSERTIONS = frozenset(
    set(SUPPORTED_SQL_ASSERTIONS) | set(SUPPORTED_COMMAND_ASSERTIONS))


def _supported_fixture_list(fixtures):
    names = [item if isinstance(item, str) else item.get("type")
             for item in fixtures or []]
    return all(name in fixtures_mod.SUPPORTED_FIXTURES for name in names)


def _supported_step_list(steps):
    return all(
        step.get("type", "sql") in _STEP_TYPES and
        (step.get("assertion") or {}).get("type") in _ALL_ASSERTIONS
        for step in steps or [])


def load_exported_command_cases():
    """Batch-register exported cases every capability is now platform-owned.

    A case migrates only when its steps, fixtures, assertions and session
    definition are all expressible with the platform SDK; anything else stays
    on the legacy executor rather than being approximated.
    """
    path = Path(__file__).parent / "regression" / "cases.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    definitions = {case["id"]: case for case in payload["cases"]}
    selected = {}
    for target, case in definitions.items():
        steps = case.get("steps") or []
        fixtures = case.get("fixtures") or []
        if (not steps or not fixtures or not _supported_step_list(steps)
                or not _supported_fixture_list(fixtures)):
            continue
        session = case.get("session")
        if session:
            if not session.get("key"):
                continue
            if not _supported_fixture_list(session.get("fixtures") or []):
                continue
            if not _supported_step_list(session.get("steps") or []):
                continue
        connection = case.get("connection")
        if connection is not None and not isinstance(connection, dict):
            continue
        selected[target] = case
    return selected


EXPORTED_COMMAND_CASES = load_exported_command_cases()


def prepare_run(environment):
    """Start stopped managed nodes before any case evaluates requirements.

    Mirrors the legacy ``_ensure_environment_started`` prepare phase: a node
    whose ``pg_ctl status`` fails is started with the environment's FBase
    binaries before requirement probes run.
    """
    import subprocess
    bin_dir = environment.get("db_bin_dir")
    pg_ctl = str(Path(bin_dir) / "pg_ctl") if bin_dir else "pg_ctl"
    nodes = environment.get("nodes") or {}
    for name in environment.get("node_order") or list(nodes):
        endpoint = nodes.get(name) or {}
        data_dir = endpoint.get("data_dir")
        if not data_dir:
            continue
        status = subprocess.run(
            [pg_ctl, "-D", str(data_dir), "status"],
            capture_output=True, text=True)
        if status.returncode != 0:
            subprocess.run(
                [pg_ctl, "-D", str(data_dir), "-l",
                 str(Path(str(data_dir)) / "startup.log"), "-w", "start"],
                capture_output=True, text=True)


CASES = {
    MmrRuntimePrerequisites.TARGET: MmrRuntimePrerequisites(),
    MmrClusterVerificationBasic.TARGET: MmrClusterVerificationBasic(),
    MacMetadataAccessRestrictions.TARGET: MacMetadataAccessRestrictions(),
}
CASES.update({target: MacMetadataDenialCase(case) for target, case in MAC_METADATA_DENIALS.items()})
CASES.update({target: MmrReadOnlyDeclarativeCase(case) for target, case in MMR_READ_ONLY_CASES.items()})
# ExportedCommandCase carries the full legacy step/fixture semantics, so it
# wins over the earlier partial-migration classes wherever it can express the
# case.  The partial classes remain only for targets the exported loader
# deliberately skips.
CASES.update({target: ExportedCommandCase(case)
              for target, case in EXPORTED_COMMAND_CASES.items()
              if target not in CASES})
CASES.update({target: DeclarativeSqlCase(case) for target, case in NATIVE_SQL_CASES.items()
              if target not in CASES and target not in FIXTURE_TARGETS})
CASES.update({target: FixtureSqlCase(NATIVE_SQL_CASES[target])
              for target in FIXTURE_TARGETS if target not in CASES})
_CATALOG = json.loads(
    (Path(__file__).parent / "regression" / "cases.json").read_text(encoding="utf-8"))
CASE_ORDER = [case["id"] for case in _CATALOG["cases"]]

CASE_METADATA = [{
    "suite": "mmr",
    "suite_title": "mmr",
    "suite_description": "",
    "target": MmrRuntimePrerequisites.TARGET,
    "name": MmrRuntimePrerequisites.TARGET,
    "title": "多活安装后运行环境与关键配置",
    "core_id": "",
    "summary": "多活安装后运行环境与关键配置",
    "enabled": True,
    "tags": ["installation"],
}, {
    "suite": "mmr",
    "suite_title": "mmr",
    "suite_description": "",
    "target": MmrClusterVerificationBasic.TARGET,
    "name": MmrClusterVerificationBasic.TARGET,
    "title": "多活集群校验 UDF 基础参数组合",
    "core_id": "",
    "summary": "多活集群校验 UDF 基础参数组合",
    "enabled": True,
    "tags": ["cluster_verification"],
}, {
    "suite": "mac",
    "suite_title": "mac",
    "suite_description": "",
    "target": MacMetadataAccessRestrictions.TARGET,
    "name": MacMetadataAccessRestrictions.TARGET,
    "title": "DBA 禁止访问和修改等保元数据",
    "core_id": "",
    "summary": "三权分立功能转测 5.3.4.2-5.3.4.3",
    "enabled": True,
    "tags": ["separation_of_duties"],
}]
CASE_METADATA.extend({
    "suite": "mac", "suite_title": "mac", "suite_description": "",
    "target": target, "name": target, "title": case.get("name", target),
    "core_id": case.get("core_id", ""), "summary": case.get("name", target),
    "enabled": True, "tags": [case.get("group", "")],
} for target, case in sorted(MAC_METADATA_DENIALS.items()))
CASE_METADATA.extend({
    "suite": "mmr", "suite_title": "mmr", "suite_description": "",
    "target": target, "name": target, "title": case.get("name", target),
    "core_id": case.get("core_id", ""), "summary": case.get("name", target),
    "enabled": True, "tags": [case.get("group", "")],
} for target, case in sorted(MMR_READ_ONLY_CASES.items()))
CASE_METADATA.extend({
    "suite": target.split(".", 1)[0],
    "suite_title": target.split(".", 1)[0],
    "suite_description": "",
    "target": target, "name": target, "title": case.get("name", target),
    "core_id": case.get("core_id", ""), "summary": case.get("name", target),
    "enabled": case.get("default_enabled", True),
    "tags": [case.get("group", "")],
} for target, case in sorted(EXPORTED_COMMAND_CASES.items()))
