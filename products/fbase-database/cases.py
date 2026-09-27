"""FBase cases migrated to the shared platform regression engine."""

import json
import re
from pathlib import Path

from platform_regress import Blocked, run_sql_step
import psycopg


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
            if (step.get("type") != "sql" or step.get("user") != "postgres"
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
    supported = {"rows_equal", "output_contains_text", "sql_error", "sql_fails",
                 "command_succeeds"}
    # These targets intentionally stay on the legacy executor until their
    # product-specific privilege/session semantics have a platform contract.
    unsafe = {"mac.audit.log_access_restrictions"}
    selected = {}
    for target, definition in definitions.items():
        if target in unsafe:
            continue
        steps = definition.get("steps") or []
        fixtures = definition.get("fixtures") or []
        valid_fixture = fixtures == ["cluster"] or (
            len(fixtures) >= 2 and fixtures[0] == "cluster"
            and all(isinstance(item, dict) and item.get("type") in {"roles", "table"}
                    for item in fixtures[1:])
        )
        if (not valid_fixture or not steps
                or not all(
                    (step.get("type") == "cluster_action" and
                     step.get("action") == "reload" and
                     step.get("assertion", {}).get("type") == "command_succeeds")
                    or (step.get("type") == "sql"
                        and step.get("user", "postgres") == "postgres"
                        and step.get("assertion", {}).get("type") in supported)
                    for step in steps) is False):
            continue
        selected[target] = definition
    return selected


NATIVE_SQL_CASES = load_native_sql_cases()
FIXTURE_TARGETS = frozenset(target for target, definition in NATIVE_SQL_CASES.items()
                             if len(definition.get("fixtures", [])) > 1)


class LegacyFbaseCase:
    """Transition one exported target through the shared result lifecycle.

    The product's old fixture executor remains behind this boundary until its
    fixture and step kinds are replaced in the platform SDK. Only a fresh,
    matching structured summary may determine the business verdict.
    """

    def __init__(self, target):
        self.target = target

    def run(self, context):
        root = Path(context.environment.get("legacy_source") or
                    Path(__file__).parent / "regression" / "legacy").resolve()
        script = root / "run.sh"
        if not script.is_file():
            raise Blocked(f"旧用例执行入口不存在: {script}")
        cluster = self.target.split(".", 1)[0]
        result = context.command(
            [str(script), "run", cluster, self.target, "--enable-run-id"],
            cwd=root, timeout_seconds=7200,
        )
        match = re.search(r"^RUN ID: (run_[A-Za-z0-9_]+)$", result.stdout, re.MULTILINE)
        if not match:
            raise RuntimeError("旧用例未输出本次唯一 run ID；无法核对结果")
        summary_path = root / "output" / "runs" / cluster / match.group(1) / "summary.json"
        try:
            summary_text = summary_path.read_text(encoding="utf-8")
            summary = json.loads(summary_text)
        except (OSError, ValueError) as exc:
            raise RuntimeError("旧用例未生成本次结构化报告") from exc
        records = summary.get("cases") or []
        if summary.get("run_id") != match.group(1) or len(records) != 1 or records[0].get("id") != self.target:
            raise RuntimeError("旧用例报告与本次目标不匹配")
        context.attach_text("legacy-summary.json", summary_text)
        record = records[0]
        run_directory = summary_path.parent.resolve()
        sources = [root / record["report"]] if record.get("report") else []
        sources.extend(Path(value) for value in record.get("evidence") or [])
        for index, source in enumerate(sources, 1):
            resolved = source.resolve()
            if not resolved.is_relative_to(run_directory) or not resolved.is_file():
                raise RuntimeError(f"本次旧用例证据不存在或不属于本次运行: {source}")
            context.attach_file(f"legacy-{index}-{source.name}", resolved)
        status = record.get("status")
        context.step("legacy-verdict", "核对旧用例原始判定", status={
            "SUCCESS": "PASS", "FAILED": "FAIL", "BLOCKED": "BLOCKED",
        }.get(status, "ERROR"), details={"legacy_status": status, "run_id": match.group(1)})
        if status == "SUCCESS" and result.returncode == 0:
            return True
        if status == "FAILED":
            raise AssertionError(record.get("reason") or "旧用例业务断言失败")
        if status == "BLOCKED":
            raise Blocked(record.get("reason") or "旧用例前置条件未满足")
        raise RuntimeError(f"旧用例结果与退出码不一致: {status}/{result.returncode}")


CASES = {
    MmrRuntimePrerequisites.TARGET: MmrRuntimePrerequisites(),
    MmrClusterVerificationBasic.TARGET: MmrClusterVerificationBasic(),
    MacMetadataAccessRestrictions.TARGET: MacMetadataAccessRestrictions(),
}
CASES.update({target: MacMetadataDenialCase(case) for target, case in MAC_METADATA_DENIALS.items()})
CASES.update({target: MmrReadOnlyDeclarativeCase(case) for target, case in MMR_READ_ONLY_CASES.items()})
CASES.update({target: DeclarativeSqlCase(case) for target, case in NATIVE_SQL_CASES.items()
              if target not in CASES and target not in FIXTURE_TARGETS})
CASES.update({target: FixtureSqlCase(NATIVE_SQL_CASES[target])
              for target in FIXTURE_TARGETS if target not in CASES})
EXPORTED_TARGETS = {
    case["id"] for case in json.loads(
        (Path(__file__).parent / "regression" / "cases.json").read_text(encoding="utf-8")
    )["cases"]
}
CASES.update({target: LegacyFbaseCase(target) for target in EXPORTED_TARGETS - CASES.keys()})

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
