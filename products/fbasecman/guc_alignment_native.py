"""Hint/sql_parse GUC alignment: raw protocol checks with explicit coverage.

SQL-visible behavior and product instrumentation are separate requirements.
Missing instrumentation never becomes a SQL PASS. Every selected execution
archives the full plan, per-scenario facts and wire evidence.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from platform_regress.clients import pgwire as wire
from platform_regress.sdk import Blocked, Cancelled, CaseContext

from products.fbasecman.native import _console_query, _free_port, render_config

SCENARIOS = {
    "extended_boundary": (
        "extended_parse_no_execute", "extended_bind_describe_no_execute",
        "extended_execute_apply", "extended_statement_isolation",
        "extended_portal_isolation", "extended_candidate_cleanup", "local_commit_failure", "compatibility_scope",
    ),
    "transaction_sync": (
        "tx_set_commit", "tx_set_rollback", "tx_reset_commit_rollback",
        "tx_reset_all_commit_rollback", "tx_error_abort", "tx_batch_segments",
        "set_local_scope", "execute_registration_failure",
    ),
    "savepoint_report": ("savepoint_rollback", "savepoint_nested_release", "report_parameter_status"),
    "backend_redeploy": (
        "tx_disconnect_cleanup", "session_backend_redeploy", "routing_and_discard_boundaries",
        "mode_owner_isolation", "local_backend_reclaim",
    ),
}
# Mandatory internal acceptance stays visible alongside executable black-box checks.
def known_discard_q_registration_limit(plan):
    """Verified in Hint and sql_parse; do not exclude other reserve=no Qs."""
    return plan.scenario == "routing_and_discard_boundaries" and plan.protocol == "Q" and not plan.reserve


class BaselineLimit(Blocked):
    """A measured existing limitation, excluded by the product design."""


INTERNAL = {"local_commit_failure", "execute_registration_failure", "product_cache_boundaries"}
DESIGN_ITEMS = {
    1: ("extended_parse_no_execute", "extended_bind_describe_no_execute"),
    2: ("extended_execute_apply", "local_backend_reclaim"),
    3: ("extended_portal_isolation",), 4: ("tx_set_commit",), 5: ("tx_set_rollback",),
    6: ("savepoint_rollback", "savepoint_nested_release"), 7: ("report_parameter_status",),
    8: ("tx_set_commit", "tx_set_rollback"), 9: ("tx_batch_segments",),
    10: ("tx_batch_segments", "tx_error_abort"),
    11: ("savepoint_rollback", "report_parameter_status"),
    12: ("extended_execute_apply", "extended_candidate_cleanup"),
    13: ("extended_statement_isolation", "extended_portal_isolation", "extended_candidate_cleanup"),
    14: ("local_commit_failure", "execute_registration_failure", "mode_owner_isolation"),
    15: ("tx_batch_segments",), 16: ("extended_execute_apply", "extended_candidate_cleanup"),
}
# §8 is tracked independently: SQL success does not prove cache internals/reuse.
ACCEPTANCE = {
    "parse_cache_unchanged": ("extended_parse_no_execute", "extended_bind_describe_no_execute"),
    "recording_boundaries": ("tx_set_commit", "tx_set_rollback", "execute_registration_failure"),
    "transaction_and_savepoint": ("tx_set_commit", "tx_set_rollback", "savepoint_rollback"),
    "protocol_order": ("extended_execute_apply", "extended_candidate_cleanup"),
    "deploy_cache": ("session_backend_redeploy", "tx_set_commit"),
    "safe_backend_reclaim": ("local_backend_reclaim",),
    "implicit_segments": ("tx_batch_segments",),
    "savepoint_error_retention": ("savepoint_rollback", "report_parameter_status"),
    "mixed_baseline": ("extended_execute_apply", "extended_candidate_cleanup"),
    "configuration_scope": ("tx_set_commit", "extended_parse_no_execute", "compatibility_scope"),
    "failure_cleanup": ("local_commit_failure", "execute_registration_failure"),
    "sql_parse_unchanged": tuple(name for names in SCENARIOS.values() for name in names),
    "required_regressions": tuple(name for names in SCENARIOS.values() for name in names),
}
PROBE_SQL = (
    "SELECT current_setting('work_mem'), current_setting('statement_timeout'), "
    "current_setting('TimeZone'), current_setting('application_name'), "
    "inet_server_addr()::text, inet_server_port()::text, pg_backend_pid()::text, "
    "pg_is_in_recovery()::text"
)
FIELDS = ("work_mem", "statement_timeout", "TimeZone", "application_name", "host", "port", "pid", "recovery")


@dataclass(frozen=True)
class CheckPlan:
    topology: str
    pool: str
    reserve: bool
    protocol: str
    scenario: str
    enable_sync: bool = True

    @property
    def key(self):
        return f"{self.topology}-{self.pool}-{'reserve' if self.reserve else 'passthrough'}-{self.protocol}-{self.scenario}" + ("-sync-off" if not self.enable_sync else "")


def make_plan(group):
    plans = []
    for topology in ("mmr", "replication"):
        for name in SCENARIOS[group]:
            if name == "compatibility_scope":
                plans.append(CheckPlan(topology, "transaction", False, "E", name))
                for reserve in (True, False):
                    for protocol in ("Q", "E"):
                        plans.append(CheckPlan(topology, "transaction", reserve, protocol, name, False))
                continue
            if name in INTERNAL:
                plans.append(CheckPlan(topology, "transaction", True, "product", name))
                continue
            configs = [(pool, True, "E") for pool in ("transaction", "session")]
            if group != "extended_boundary" and name not in {"local_backend_reclaim", "mode_owner_isolation"}:
                configs += [(pool, reserve, "Q") for pool in ("transaction", "session") for reserve in (True, False)]
            for pool, reserve, protocol in configs:
                plans.append(CheckPlan(topology, pool, reserve, protocol, name))
    for topology in ("mmr", "replication"):
        plans.append(CheckPlan(topology, "transaction", True, "product", "product_cache_boundaries"))
    return sorted(plans, key=lambda p: (p.protocol == "product", p.topology, p.pool, p.reserve, p.enable_sync, p.scenario, p.protocol))


def normalize(name, value):
    if name not in {"work_mem", "statement_timeout"}:
        return value
    import re
    found = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(B|kB|MB|GB|TB|us|ms|s|min|h|d)?", value)
    if not found:
        raise ValueError(f"无法规范化 {name}={value!r}")
    from decimal import Decimal
    factors = {"B": 1, "kB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4} if name == "work_mem" else {
        "us": Decimal(".001"), "ms": 1, "s": 1000, "min": 60000, "h": 3600000, "d": 86400000}
    unit = found[2] or ("kB" if name == "work_mem" else "ms")
    if unit not in factors:
        raise ValueError(f"{name} 的单位不合法: {unit}")
    return Decimal(found[1]) * factors[unit]


def response_facts(messages):
    return {
        "received": [kind for kind, _ in messages],
        "rows": wire.data_rows(messages),
        "sqlstates": [wire.error_fields(body).get("C") for kind, body in messages if kind == "E"],
        "errors": [wire.error_fields(body) for kind, body in messages if kind == "E"],
        "tags": [body.rstrip(b"\0").decode("utf-8", "replace") for kind, body in messages if kind == "C"],
        "ready": [body.decode("ascii") for kind, body in messages if kind == "Z"],
        "parameters": [wire.parameter_status(body) for kind, body in messages if kind == "S"],
    }


SCENARIO_LABELS = {
    "extended_parse_no_execute": "仅解析、不执行", "extended_bind_describe_no_execute": "绑定和描述不提前生效",
    "extended_execute_apply": "Execute 后参数生效", "extended_statement_isolation": "语句候选互不覆盖",
    "extended_portal_isolation": "Portal 候选隔离", "extended_candidate_cleanup": "未执行候选关闭与清理",
    "tx_set_commit": "事务提交保留参数", "tx_set_rollback": "事务回滚恢复参数",
    "tx_reset_commit_rollback": "RESET 提交与回滚", "tx_reset_all_commit_rollback": "RESET ALL 提交与回滚",
    "tx_error_abort": "失败事务不提交参数", "tx_batch_segments": "多语句事务段独立收口",
    "set_local_scope": "SET LOCAL 的事务作用域", "savepoint_rollback": "回滚到保存点恢复参数",
    "savepoint_nested_release": "嵌套保存点与释放", "report_parameter_status": "客户端参数状态恢复",
    "session_backend_redeploy": "后端复用时保持会话隔离", "tx_disconnect_cleanup": "未提交断连清理",
    "routing_and_discard_boundaries": "路由与会话清理", "local_backend_reclaim": "本地参数执行后重部署",
    "product_cache_boundaries": "内部缓存写入边界", "local_commit_failure": "本地提交失败清理",
    "execute_registration_failure": "事务预记录失败清理", "compatibility_scope": "关闭同步与透传兼容",
}


def operation_label(operation):
    import re
    if PROBE_SQL in operation:
        return "读取会话参数与实际后端"
    if "未 E" in operation:
        return "只解析或绑定参数语句，尚未执行"
    sql = operation.split("：", 1)[-1].strip()
    if sql == "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY": return "切换到读侧"
    if sql == "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE": return "切换到写侧"
    if sql == "COMMIT": return "提交事务"
    if sql == "ROLLBACK": return "完整回滚事务"
    if sql.startswith("ROLLBACK TO"): return "回滚到保存点"
    if sql.startswith("SAVEPOINT"): return "建立保存点"
    if sql.startswith("RELEASE"): return "释放保存点"
    if sql.startswith("BEGIN"): return "开始事务"
    if sql == "RESET ALL": return "重置全部会话参数"
    if sql.startswith("RESET "): return "重置参数 " + sql[6:]
    if sql == "DISCARD ALL": return "清理会话参数和预处理状态"
    if "SELECT 1/0" == sql: return "执行除零语句，触发事务错误"
    match = re.fullmatch(r"SET (?:SESSION |LOCAL )?([\w]+)\s*=\s*(.+)", sql, re.IGNORECASE)
    if match:
        scope = "事务局部参数" if "SET LOCAL" in sql else "会话参数"
        return f"设置{scope} {match[1]}={match[2]}"
    return operation if len(operation) < 100 else operation[:97] + "…"


class Probe:
    def __init__(self, runner, label, user="postgres"):
        self.runner, self.label, self.user = runner, label, user
        self.sequence = 0
        self.parameters = {}
        self.last_operation = "连接建立"
        self.sock = wire.connect(runner.host, runner.port, runner.database, user,
                                 application_name=f"{runner.mode}_base", timeout=10)
        self.sock.settimeout(10)

    def close(self):
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            self.sock.sendall(wire.terminate_message())
        except OSError:
            pass
        self.sock.close()

    def exchange(self, packet, operation, expected, *, terminal=None, intent="action"):
        self.runner.context.check_cancel()
        if PROBE_SQL not in operation:
            self.last_operation = operation
        self.sequence += 1
        key = f"{self.runner.plan.key}-{self.label}-{self.sequence}"
        messages = []
        try:
            self.sock.sendall(packet)
            while True:
                kind, body = wire.read_message(self.sock)
                messages.append((kind, body))
                if kind == "S":
                    name, value = wire.parameter_status(body)
                    self.parameters[name] = value
                if kind in (terminal or {"Z"}) or (terminal and kind in {"E", "Z"}):
                    break
        except Exception as exc:
            self.runner.record(key, operation, expected, {**response_facts(messages), "transport_error": str(exc)},
                               False, packet, messages, intent=intent)
            if isinstance(exc, (OSError, RuntimeError)):
                raise AssertionError(f"{operation}：预期正常协议收口，实际断连/超时：{exc}") from exc  # noqa: TRY004 - protocol failure, not argument type
            raise
        facts = response_facts(messages)
        differences = {}
        for field, value in expected.items():
            if facts.get(field) != value:
                differences[field] = {"expected": value, "actual": facts.get(field)}
        self.runner.record(key, operation, expected, facts, not differences, packet, messages,
                           intent=intent, differences=differences)
        if differences:
            raise AssertionError(f"{operation}: {differences}")
        return facts

    def sql(self, sql, *, tag=None, ready="I", error=None, protocol=None):
        protocol = protocol or self.runner.plan.protocol
        if protocol == "E":
            packet = extended_packet(sql)
            operation = f"P/B/Describe Portal/E/Sync：{sql}"
        else:
            packet, operation = wire.simple_query_message(sql), f"Q：{sql}"
        expected = {"sqlstates": [error] if error else [], "ready": [ready]}
        if tag is not None:
            expected["tags"] = [tag]
        return self.exchange(packet, operation, expected, intent="verify" if error else "action")

    def snapshot(self, expected=None, ready=None, *, intent="verify"):
        # Measurement is an independent Q after the operation's completed
        # Sync; it must not add a reused PreparedStatement cache contract to
        # a GUC value assertion. Named GUC statement/portal checks stay E.
        facts = self.sql(PROBE_SQL, ready=ready or "I", tag="SELECT 1", protocol="Q")
        if len(facts["rows"]) != 1 or len(facts["rows"][0]) != len(FIELDS):
            self.runner.verify("查询恰好一行参数及后端身份", {"rows": 1, "columns": len(FIELDS)}, facts, False)
        values = dict(zip(FIELDS, facts["rows"][0]))
        if expected:
            differences = {name: {"expected": value, "actual": values[name]}
                           for name, value in expected.items()
                           if normalize(name, values[name]) != normalize(name, value)}
            operation = getattr(self, "last_operation", "前序操作")
            comparison = "；".join(f"{name}：期望 {value}，实际 {values[name]}" for name, value in expected.items())
            self.runner.verify(f"{operation_label(operation)}后，核对参数值", expected,
                               {**{name: values[name] for name in expected}, "后端": identity(values)},
                               not differences, command=operation, analysis=comparison, intent=intent)
        return values


def extended_packet(sql, statement="", portal="", execute=True):
    packet = wire.message("P", wire.parse_payload(statement, sql))
    packet += wire.message("B", wire.bind_payload(portal, statement))
    packet += wire.message("D", wire.describe_portal_payload(portal))
    if execute:
        packet += wire.message("E", wire.execute_payload(portal))
    return packet + wire.sync_message()


def identity(values):
    return tuple(values[name] for name in ("host", "port", "pid"))


class ScenarioRunner:
    def __init__(self, context, plan, mode, port):
        self.context, self.plan, self.mode, self.port = context, plan, mode, port
        self.host = context.environment.get("local_host", "127.0.0.1")
        self.database = "mmr_group" if plan.topology == "mmr" else "rep_group"
        self.sequence = 0
        self.results = []

    def record(self, key, title, expected, actual, passed, packet=b"", messages=(), intent="verify", differences=None, analysis=None, command=None):
        evidence = self.context.attach_text(f"{key}-wire.json", json.dumps({
            "scope": asdict(self.plan), "expected": expected, "actual": actual,
            "sent_hex": packet.hex(), "received": [{"kind": k, "payload_hex": b.hex()} for k, b in messages],
        }, ensure_ascii=False, indent=2))
        self.context.step(key, f"{'MMR' if self.plan.topology == 'mmr' else '主备'}／{self.mode}／{SCENARIO_LABELS.get(self.plan.scenario, self.plan.scenario)}：{operation_label(title)}",
                          status="PASS" if passed else "FAIL", details={
                              "intent": intent if passed else "verify", "command": command or title, "expected": expected, "actual": actual,
                              "assertion": {"type": "fields_equal", "passed": passed, "differences": differences or {}},
                              "analysis": analysis or ("协议执行记录；业务结果见参数、事务或缓存检查" if intent == "action" and passed else f"期望 {expected}；实测 {actual}" if passed else f"字段差异：{differences or actual}"),
                              "evidence": evidence,
                          })
        self.results.append({"key": key, "passed": passed})

    def verify(self, title, expected, actual, passed, *, command=None, analysis=None, intent="verify"):
        self.sequence += 1
        self.record(f"{self.plan.key}-assert-{self.sequence}", title, expected, actual, passed, command=command, analysis=analysis, intent=intent)
        if not passed:
            raise AssertionError(f"{title}: 期望 {expected}，实际 {actual}")

    @contextmanager
    def client(self, label="A", user="postgres"):
        probe = Probe(self, label, user)
        try:
            yield probe
        finally:
            probe.close()
            self.sequence += 1
            self.context.step(f"{self.plan.key}-close-{self.sequence}", f"关闭客户端 {label}",
                              details={"intent": "cleanup", "expected": "客户端关闭", "actual": "socket 已关闭"})

    def baseline(self, probe):
        initial = probe.snapshot()
        if self.plan.pool == "session":
            # Session-pool setup must modify the pinned physical connection.
            # A Q local bypass after snapshot would only seed frontend state.
            probe.sql("BEGIN READ WRITE", tag="BEGIN", ready="T", protocol="Q")
        ready = "T" if self.plan.pool == "session" else "I"
        probe.sql("SET work_mem='8MB'", tag="SET", ready=ready)
        probe.sql("SET statement_timeout='7s'", tag="SET", ready=ready)
        probe.sql("SET TimeZone='UTC'", tag="SET", ready=ready)
        if self.plan.pool == "session":
            probe.sql("COMMIT", tag="COMMIT", protocol="Q")
        probe.snapshot({"work_mem": "8MB", "statement_timeout": "7s", "TimeZone": "UTC"}, intent="prepare")
        return initial

    def redeploy(self, probe, expected):
        if self.plan.pool == "session":
            # A session pool pins the physical connection. Cross-backend
            # deployment belongs to transaction-pool variants, not this one.
            before = probe.snapshot(expected)
            after = probe.snapshot(expected)
            self.verify("session pool 原会话后端保持且参数正确", identity(before), identity(after),
                        identity(before) == identity(after))
            return
        if self.mode == "hint":
            probe.sql("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE", protocol="Q")
            before = probe.snapshot(expected)
            probe.sql("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY", protocol="Q")
            after = probe.snapshot(expected)
            probe.sql("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE", protocol="Q")
            probe.snapshot(expected)
        else:
            probe.sql("BEGIN READ WRITE", tag="BEGIN", ready="T")
            before = probe.snapshot(expected, ready="T")
            probe.sql("COMMIT", tag="COMMIT")
            probe.sql("BEGIN READ ONLY", tag="BEGIN", ready="T")
            after = probe.snapshot(expected, ready="T")
            probe.sql("COMMIT", tag="COMMIT")
        if identity(before) == identity(after):
            raise Blocked(f"未构造出物理后端切换：{identity(before)}")
        self.verify("同一客户端切换物理后端", "身份不同且参数均匹配", {"before": identity(before), "after": identity(after)}, True)
        if self.plan.topology == "replication":
            self.verify("复制组读侧为备库", "true", after["recovery"], after["recovery"] == "true")

    def begin_write_transaction(self, probe, *, protocol=None):
        """Pin write routing before BEGIN; Hint labels must stay outside TX."""
        if self.mode == "hint":
            probe.sql("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE", protocol="Q")
            probe.sql("BEGIN", tag="BEGIN", ready="T", protocol=protocol)
        else:
            probe.sql("BEGIN READ WRITE", tag="BEGIN", ready="T", protocol=protocol)

    def run(self):
        name = self.plan.scenario
        if name in INTERNAL:
            from products.fbasecman.guc_instrumentation import ProductChecks
            checks = ProductChecks(self, self.config, self.binary)
            if name == "product_cache_boundaries":
                return checks.boundary()
            if name == "local_commit_failure":
                return checks.faults([("E", point) for point in ("response_construct", "apply", "response_queue")])
            return checks.faults([(protocol, point) for protocol in ("Q", "E")
                                  for point in ("pre_record", "pending", "outstanding", "forward")])
        if name == "compatibility_scope":
            return self.compatibility()
        if name == "mode_owner_isolation":
            return self.mode_isolation()
        if name == "session_backend_redeploy":
            return self.pool_reuse()
        if name in {"tx_disconnect_cleanup", "local_backend_reclaim"}:
            return self.backend_cleanup(name)
        with self.client() as probe:
            initial = self.baseline(probe)
            if name.startswith("extended_"):
                return self.extended(probe, initial, name)
            if name == "tx_batch_segments":
                return self.batches(probe)
            if name.startswith("savepoint_"):
                return self.savepoints(probe, name)
            if name == "report_parameter_status":
                return self.report(probe)
            if name == "routing_and_discard_boundaries":
                self.redeploy(probe, {"work_mem": "8MB"})
                if known_discard_q_registration_limit(self.plan):
                    raise BaselineLimit("既有共用问题 DISCARD-Q-NO-RESERVE：Hint/sql_parse 的 Q 不入 outstanding，DISCARD 打标失败导致 OD_STOP；仅排除此 DISCARD 配置分支，路由及其他 reserve=no Q 仍验证")
                # Success and rejected cleanup are independent contracts.
                # Do not append a second DISCARD to the known sql_parse
                # rejected-DISCARD/reprepare combination excluded by the user.
                probe.sql("DISCARD ALL", tag="DISCARD ALL")
                probe.snapshot({k: initial[k] for k in ("work_mem", "statement_timeout", "TimeZone")})
                self.baseline(probe)
                self.begin_write_transaction(probe)
                probe.sql("DISCARD ALL", ready="E", error="25001")
                probe.sql("ROLLBACK", tag="ROLLBACK")
                probe.snapshot({"work_mem": "8MB"})
                self.context.step(f'{self.plan.key}-discard-combined-limit',
                                  '已排除 sql_parse 既有 DISCARD 组合问题', status='SKIPPED', details={
                                      'intent': 'verify', 'expected': '独立成功/拒绝分支仍须满足原断言',
                                      'actual': '不追加拒绝回滚后的第二次 DISCARD/PreparedStatement 复用专项',
                                      'analysis': '用户明确本次不处理 sql_parse 既有问题；排除不计通过覆盖'})
                return
            return self.transaction(probe, initial, name)

    def transaction(self, probe, initial, name):
        if name in {"tx_set_commit", "tx_set_rollback"}:
            end = "COMMIT" if name.endswith("commit") else "ROLLBACK"
            probe.sql("BEGIN", tag="BEGIN", ready="T")
            probe.sql("SET work_mem='32MB'", tag="SET", ready="T")
            probe.sql(f"SET application_name='{self.mode}_tx'", tag="SET", ready="T")
            probe.snapshot({"work_mem": "32MB", "application_name": f"{self.mode}_tx"}, ready="T")
            probe.sql(end, tag=end)
            expected = {"work_mem": "32MB" if end == "COMMIT" else "8MB",
                        "application_name": f"{self.mode}_tx" if end == "COMMIT" else initial["application_name"]}
            probe.snapshot(expected)
            self.redeploy(probe, expected)
            if self.plan.protocol == "Q":
                probe.sql(f"BEGIN; SET application_name='{self.mode}_batch'; {end};", protocol="Q")
                probe.snapshot({"application_name": f"{self.mode}_batch" if end == "COMMIT" else expected["application_name"]})
            return
        if name in {"tx_reset_commit_rollback", "tx_reset_all_commit_rollback"}:
            reset = "RESET ALL" if "all" in name else "RESET work_mem"
            # R0 is collected directly from an independent clean physical backend,
            # not from the RESET path being tested.
            reset_values = self.default_values()
            names = ("work_mem", "statement_timeout", "TimeZone") if "all" in name else ("work_mem",)
            for end in ("ROLLBACK", "COMMIT"):
                probe.sql("BEGIN", tag="BEGIN", ready="T")
                probe.sql(reset, tag="RESET", ready="T")
                probe.snapshot({k: reset_values[k] for k in names}, ready="T")
                probe.sql(end, tag=end)
                expected = ({"work_mem": "8MB", "statement_timeout": "7s", "TimeZone": "UTC"}
                            if end == "ROLLBACK" else {k: reset_values[k] for k in names})
                probe.snapshot(expected)
                self.redeploy(probe, expected)
            return
        if name == "set_local_scope":
            for end in ("ROLLBACK", "COMMIT"):
                probe.sql("BEGIN", tag="BEGIN", ready="T")
                probe.sql("SET SESSION work_mem='16MB'", tag="SET", ready="T")
                probe.sql("SET LOCAL work_mem='32MB'", tag="SET", ready="T")
                probe.snapshot({"work_mem": "32MB"}, ready="T")
                probe.sql(end, tag=end)
                expected = {"work_mem": "8MB" if end == "ROLLBACK" else "16MB"}
                probe.snapshot(expected)
                self.redeploy(probe, expected)
            return
        if name == "tx_error_abort":
            for end, invalid in (("ROLLBACK", False), ("COMMIT", False), ("ROLLBACK", True)):
                probe.sql("BEGIN", tag="BEGIN", ready="T")
                probe.sql("SET work_mem='32MB'", tag="SET", ready="T")
                probe.sql("SET work_mem='not_a_memory_size'" if invalid else "SELECT 1/0", ready="E", error="22023" if invalid else "22012")
                probe.sql("SET work_mem='64MB'", ready="E", error="25P02")
                probe.sql(end, tag="ROLLBACK")
                probe.snapshot({"work_mem": "8MB"})
                self.redeploy(probe, {"work_mem": "8MB"})
            return
        raise ValueError(f"未实现检查 {name}")

    def default_values(self):
        nodes = self.context.environment.get("nodes") or {}
        values = []
        for node in ("mmr1", "mmr2"):
            if node in nodes:
                values.append(clean_backend_defaults(self.context, node))
        if len(values) != 2 or values[0] != values[1]:
            raise Blocked("两个物理后端的 RESET 默认基线缺失或不一致")
        return values[0]

    def batches(self, probe):
        # Each entire string is one Q, never split into separate client requests.
        cases = [
            ("BEGIN; SET work_mem='32MB'; COMMIT; SELECT current_setting('work_mem');", "32MB", None),
            ("BEGIN; SET work_mem='64MB'; ROLLBACK; SELECT current_setting('work_mem');", "32MB", None),
            ("SET work_mem='64MB'; SELECT 1;", "64MB", None),
            ("SET work_mem='32MB'; SELECT 1/0;", "64MB", "22012"),
            ("BEGIN; SET work_mem='32MB'; COMMIT; BEGIN; SET work_mem='64MB'; ROLLBACK; SELECT current_setting('work_mem');", "32MB", None),
            ("BEGIN; SET work_mem='16MB'; COMMIT; SET work_mem='64MB'; SELECT 1/0;", "16MB", "22012"),
            ("SET work_mem='32MB'; COMMIT; SELECT 1/0;", "32MB", "22012"),
            ("SET work_mem='64MB'; ROLLBACK; SELECT current_setting('work_mem');", "32MB", None),
            ("SET work_mem='16MB'; COMMIT; SET work_mem='64MB'; SELECT 1/0;", "16MB", "22012"),
        ]
        for sql, expected, error in cases:
            facts = probe.sql(sql, error=error, protocol="Q")
            if "SELECT current_setting" in sql and not error:
                self.verify("单 Q 内末尾查询已反映事务段结算", [[expected]], facts["rows"], facts["rows"] == [[expected]])
            probe.snapshot({"work_mem": expected})
            self.redeploy(probe, {"work_mem": expected})
        for sql, expected, error in (
            ("SET TimeZone='Asia/Shanghai'; SELECT 1;", "Asia/Shanghai", None),
            ("SET TimeZone='Asia/Tokyo'; SELECT 1/0;", "Asia/Shanghai", "22012"),
            ("BEGIN; SET TimeZone='UTC'; COMMIT; SET TimeZone='Asia/Tokyo'; SELECT 1/0;", "UTC", "22012"),
        ):
            probe.sql(sql, error=error, protocol="Q")
            probe.snapshot({"TimeZone": expected})
            self.verify("隐式段 report 客户端最终值", expected, probe.parameters.get("TimeZone"), probe.parameters.get("TimeZone") == expected)
            self.redeploy(probe, {"TimeZone": expected})

    def savepoints(self, probe, name):
        if name == "savepoint_nested_release":
            for outer, inner in (("outer_sp", "inner_sp"), ('"外层保存点"', '"内层保存点"'), ("same_sp", "same_sp")):
                probe.sql("BEGIN", tag="BEGIN", ready="T")
                probe.sql("SET work_mem='16MB'", tag="SET", ready="T")
                probe.sql(f"SAVEPOINT {outer}", tag="SAVEPOINT", ready="T")
                probe.sql("SET work_mem='32MB'", tag="SET", ready="T")
                probe.sql(f"SAVEPOINT {inner}", tag="SAVEPOINT", ready="T")
                probe.sql("SET work_mem='64MB'", tag="SET", ready="T")
                probe.sql(f"RELEASE SAVEPOINT {inner}", tag="RELEASE", ready="T")
                probe.snapshot({"work_mem": "64MB"}, ready="T")
                probe.sql(f"ROLLBACK TO SAVEPOINT {outer}", tag="ROLLBACK", ready="T")
                probe.snapshot({"work_mem": "16MB"}, ready="T")
                probe.sql("COMMIT", tag="COMMIT")
                self.redeploy(probe, {"work_mem": "16MB"})
            return
        for operation in ("SET work_mem='32MB'", "RESET work_mem", "RESET ALL", "error"):
            probe.sql("BEGIN", tag="BEGIN", ready="T")
            probe.sql("SET work_mem='16MB'", tag="SET", ready="T")
            probe.sql("SAVEPOINT sp", tag="SAVEPOINT", ready="T")
            probe.sql("SET work_mem='32MB'" if operation == "error" else operation,
                      tag="SET" if operation.startswith("SET") or operation == "error" else "RESET", ready="T")
            if operation == "error":
                probe.sql("SET work_mem='not_a_memory_size'", error="22023", ready="E")
            probe.sql("ROLLBACK TO SAVEPOINT sp", tag="ROLLBACK", ready="T")
            probe.snapshot({"work_mem": "16MB", "statement_timeout": "7s"}, ready="T")
            probe.sql("COMMIT", tag="COMMIT")
            self.redeploy(probe, {"work_mem": "16MB", "statement_timeout": "7s"})

    def prepare_report_timezone(self, probe):
        if self.plan.pool == "session":
            self.begin_write_transaction(probe)
            probe.sql("SET TimeZone='UTC'", tag="SET", ready="T")
            probe.sql("COMMIT", tag="COMMIT")
        else:
            probe.sql("SET TimeZone='UTC'", tag="SET")

    def report(self, probe):
        for end in ("COMMIT", "ROLLBACK", "savepoint", "error"):
            self.prepare_report_timezone(probe)
            self.begin_write_transaction(probe)
            probe.sql("SET TimeZone='Asia/Shanghai'", tag="SET", ready="T")
            probe.snapshot({"TimeZone": "Asia/Shanghai"}, ready="T")
            if end == "savepoint":
                probe.sql("SAVEPOINT sp", tag="SAVEPOINT", ready="T")
                probe.sql("SET TimeZone='Asia/Tokyo'", tag="SET", ready="T")
                probe.sql("SELECT 1/0", error="22012", ready="E")
                probe.sql("ROLLBACK TO sp", tag="ROLLBACK", ready="T")
                probe.snapshot({"TimeZone": "Asia/Shanghai"}, ready="T")
            if end == "error":
                probe.sql("SELECT 1/0", error="22012", ready="E")
            command = end if end in {"COMMIT", "ROLLBACK"} else ("COMMIT" if end == "savepoint" else "ROLLBACK")
            probe.sql(command, tag=command)
            expected = "Asia/Shanghai" if command == "COMMIT" else "UTC"
            probe.snapshot({"TimeZone": expected})
            self.verify("ParameterStatus 与当前事务视图一致", expected, probe.parameters.get("TimeZone"), probe.parameters.get("TimeZone") == expected)
            self.redeploy(probe, {"TimeZone": expected})

    def extended(self, probe, initial, name):
        def parse(sql, statement):
            return wire.message("P", wire.parse_payload(statement, sql))
        def bind(statement, portal):
            return wire.message("B", wire.bind_payload(portal, statement))
        def execute(portal):
            return wire.message("E", wire.execute_payload(portal))
        def describe(portal):
            return wire.message("D", wire.describe_portal_payload(portal))
        if name in {"extended_parse_no_execute", "extended_bind_describe_no_execute", "extended_execute_apply"}:
            for transaction in (False, True):
                for index, sql in enumerate(("SET work_mem='32MB'", "RESET work_mem", "RESET ALL")):
                    self.baseline(probe)
                    if transaction:
                        probe.sql("BEGIN", tag="BEGIN", ready="T")
                        probe.snapshot({"work_mem": "8MB"}, ready="T")
                    ready = "T" if transaction else "I"
                    branches = ("P",) if name == "extended_parse_no_execute" else ("PB", "PDS", "PBDP")
                    if name == "extended_execute_apply":
                        branches = ("PBDP",)
                    for branch in branches:
                        stmt, portal = f"guc_{transaction}_{index}_{branch}", f"portal_{transaction}_{index}_{branch}"
                        packet = parse(sql, stmt)
                        expected_kinds = ["1"]
                        if branch in {"PB", "PBDP"}:
                            packet += bind(stmt, portal)
                            expected_kinds.append("2")
                        if branch == "PDS":
                            packet += wire.message("D", wire.describe_statement_payload(stmt))
                            expected_kinds += ["t", "n"]
                        elif branch == "PBDP":
                            packet += describe(portal)
                            expected_kinds.append("n")
                        # Finish the no-E cycle, then observe on the same client.
                        expected = {"sqlstates": [], "tags": [], "ready": [ready]}
                        if self.mode == "hint":
                            expected["received"] = expected_kinds + ["Z"]
                        # sql_parse may defer ParseComplete until Bind/Execute;
                        # keep its existing phase timing and check the GUC
                        # side effect independently, rather than fixing sql_parse.
                        probe.exchange(packet + wire.sync_message(), f"{branch}/Sync（未 E）：{sql}", expected)
                        probe.snapshot({"work_mem": "8MB", "statement_timeout": "7s"}, ready=ready)
                        if name == "extended_execute_apply":
                            defaults = self.default_values()
                            expected = {"work_mem": "32MB"} if index == 0 else {"work_mem": defaults["work_mem"]}
                            if index == 2:
                                expected.update(statement_timeout=defaults["statement_timeout"], TimeZone=defaults["TimeZone"])
                            # New Bind is required after a transaction-ending Sync.
                            if transaction:
                                packet = execute(portal)
                            else:
                                packet = bind(stmt, portal) + execute(portal)
                            probe.exchange(packet + wire.sync_message(), f"E/Sync：{sql}",
                                           {"sqlstates": [], "tags": ["SET" if index == 0 else "RESET"], "ready": [ready]})
                            probe.snapshot(expected, ready=ready)
                        probe.exchange(wire.message("C", wire.close_statement_payload(stmt)) + wire.sync_message(),
                                       f"Close Statement/Sync：{stmt}", {"sqlstates": [], "tags": [], "ready": [ready]})
                    if transaction:
                        probe.sql("ROLLBACK", tag="ROLLBACK")
            if name == "extended_execute_apply":
                # Local-only Flush phases prove no P/B/D side effect and no RFQ.
                self.baseline(probe)
                probe.exchange(parse("SET work_mem='32MB'", "flush_set") + wire.flush_message(),
                               "Parse/Flush", {"received": ["1"], "ready": [], "tags": [], "sqlstates": []}, terminal={"1"})
                probe.exchange(bind("flush_set", "flush_portal") + wire.flush_message(),
                               "Bind/Flush", {"received": ["2"], "ready": [], "tags": [], "sqlstates": []}, terminal={"2"})
                probe.exchange(describe("flush_portal") + wire.flush_message(),
                               "Describe Portal/Flush", {"received": ["n"], "ready": [], "tags": [], "sqlstates": []}, terminal={"n"})
                probe.exchange(execute("flush_portal") + wire.sync_message(), "Execute/Sync",
                               {"tags": ["SET"], "ready": ["I"], "sqlstates": []})
                probe.snapshot({"work_mem": "32MB"})
                local = extended_packet("SET work_mem='16MB'", "all_local_1", "all_local_p1")[:-5]
                local += extended_packet("SET statement_timeout='9s'", "all_local_2", "all_local_p2")[:-5]
                probe.exchange(local + wire.sync_message(), "全本地双 Execute，共一个 Sync",
                               {"tags": ["SET", "SET"], "ready": ["I"], "sqlstates": []})
                probe.snapshot({"work_mem": "16MB", "statement_timeout": "9s"})
                self.mixed(probe)
            elif name == "extended_parse_no_execute":
                self.unexecuted_disconnect(probe)
            return
        if name == "extended_statement_isolation":
            for stem in ("first", "cache_hit"):
                for label, sql in (("a", "SET work_mem='32MB'"), ("b", "SET work_mem='64MB'"), ("reset", "RESET work_mem")):
                    probe.exchange(parse(sql, f"{stem}_{label}") + wire.sync_message(), f"Parse {stem}_{label}",
                                   {"ready": ["I"], "tags": [], "sqlstates": []})
                for label, value in (("b", "64MB"), ("a", "32MB"), ("a", "32MB")):
                    probe.exchange(bind(f"{stem}_{label}", "") + execute("") + wire.sync_message(), f"Bind/Execute {stem}_{label}",
                                   {"tags": ["SET"], "ready": ["I"], "sqlstates": []})
                    probe.snapshot({"work_mem": value})
            # Replacing unnamed statement after binding a named portal must not
            # mutate the bound portal's candidate; no intermediate Sync.
            packet = parse("SET work_mem='16MB'", "") + bind("", "saved")
            packet += parse("SET statement_timeout='9s'", "") + bind("", "new")
            packet += execute("saved") + execute("new") + wire.sync_message()
            probe.exchange(packet, "未命名 statement 替换，已绑定候选保留",
                           {"tags": ["SET", "SET"], "ready": ["I"], "sqlstates": []})
            probe.snapshot({"work_mem": "16MB", "statement_timeout": "9s"})
            return
        if name == "extended_portal_isolation":
            packet = parse("SET work_mem='32MB'", "shared") + bind("shared", "p1") + bind("shared", "p2")
            packet += wire.message("C", wire.close_portal_payload("p1")) + execute("p2")
            packet += parse("SET statement_timeout='9s'", "other") + bind("other", "p3") + execute("p3")
            packet += execute("p2") + wire.sync_message()
            probe.exchange(packet, "双 portal、关闭 p1、交错及重复执行 p2",
                           {"tags": ["SET", "SET", "SET"], "ready": ["I"], "sqlstates": []})
            probe.snapshot({"work_mem": "32MB", "statement_timeout": "9s"})
            return
        if name == "extended_candidate_cleanup":
            packet = parse("SET work_mem='32MB'", "closed") + bind("closed", "closed_p")
            packet += wire.message("C", wire.close_portal_payload("closed_p"))
            packet += wire.message("C", wire.close_statement_payload("closed")) + wire.sync_message()
            probe.exchange(packet, "关闭未执行 portal/statement", {"tags": [], "ready": ["I"], "sqlstates": []})
            probe.snapshot({"work_mem": "8MB"})
            probe.exchange(execute("closed_p") + wire.sync_message(), "关闭 portal 后 Execute",
                           {"tags": [], "ready": ["I"], "sqlstates": ["34000"]})
            probe.exchange(bind("closed", "unknown") + wire.sync_message(), "关闭 statement 后 Bind",
                           {"tags": [], "ready": ["I"], "sqlstates": ["26000"]})
            probe.exchange(extended_packet("SET work_mem='16MB'", "closed", "rebuilt"), "同名重建不同候选",
                           {"tags": ["SET"], "ready": ["I"], "sqlstates": []})
            probe.snapshot({"work_mem": "16MB"})
            self.mixed(probe, errors_only=True)
            self.unexecuted_disconnect(probe)
            return
        raise ValueError(name)

    def unexecuted_disconnect(self, probe):
        self.baseline(probe)
        if self.mode == "hint":
            probe.sql("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE", protocol="Q")
        packet = extended_packet("SET work_mem='64MB'", "disconnect_unexecuted", "disconnect_portal", execute=False)
        probe.exchange(packet, "P/B/D/Sync 后不 E 并断连", {"tags": [], "ready": ["I"], "sqlstates": []})
        before = probe.snapshot({"work_mem": "8MB"})
        probe.close()
        with self.client("unexecuted_replacement") as replacement:
            if self.mode == "hint":
                replacement.sql("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE", protocol="Q")
            after = replacement.snapshot({"work_mem": self.default_values()["work_mem"]})
            if identity(before) != identity(after):
                raise Blocked(f"未 E 断连后未复用原物理后端：{identity(before)} -> {identity(after)}")
            self.verify("未 E 断连复用原后端无污染", identity(before), identity(after), True)

    @contextmanager
    def sql_parse_baseline_runner(self):
        if self.mode == "sql_parse":
            yield self
            return
        old_port = self.context.environment.get("proxy_port")
        port = _free_port()
        folder = self.context.output_dir / "sql-parse-baselines" / self.plan.key
        config = folder / "fbasecman.conf"
        try:
            self.context.environment["proxy_port"] = port
            render_alignment_config(self.context, config, self.plan, "sql_parse")
        finally:
            self.context.environment["proxy_port"] = old_port
        process = self.context.start_process([str(self.binary.resolve()), str(config.resolve())],
            cwd=folder.resolve(), ready_host=self.host, ready_port=port)
        try:
            wait_routing(self.context, port, self.plan.topology)
            baseline = ScenarioRunner(self.context, self.plan, "sql_parse", port)
            yield baseline
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            self.context.attach_file(f"{self.plan.key}-sql-parse-baseline.conf", config)
            log = config.with_suffix('.log')
            if log.exists():
                self.context.attach_file(f"{self.plan.key}-sql-parse-baseline.log", log)

    def mixed(self, probe, errors_only=False):
        # These complex combinations are gated by a real sql_parse execution
        # in this same proxy/configuration, never inferred from PG support.
        with self.sql_parse_baseline_runner() as baseline_runner:
            supported_count = 0
            limits = []
            variants = [(True, False), (False, False), (True, True)] if not errors_only else [(True, True)]
            for backend_first, error in variants:
                backend = "SELECT 1/0" if error else "SELECT 42::int"
                def cycle(sql, stem):
                    return extended_packet(sql, f"{stem}_stmt", f"{stem}_portal")[:-5]
                backend_packet = cycle(backend, "backend")
                local_packet = cycle("SET work_mem='32MB'", "local")
                packet = (backend_packet + local_packet if backend_first else local_packet + backend_packet) + wire.sync_message()
                with baseline_runner.client(f"sql_parse_baseline_{backend_first}_{error}") as baseline:
                    baseline.sql("SET work_mem='8MB'", tag="SET", protocol="Q")
                    base = baseline.exchange(packet, "sql_parse 混合请求基线（一次发送，无客户端 Flush）", {}, intent="action")
                    measured = baseline.exchange(wire.simple_query_message(PROBE_SQL), "sql_parse 基线参数观测", {}, intent="action")
                    supported = (base["sqlstates"] == (["22012"] if error else []) and base["ready"] == ["I"]
                                 and base["tags"] == (["SELECT 1", "SET"] if backend_first and not error else
                                                       ["SET", "SELECT 1"] if not backend_first else [])
                                 and len(measured["rows"]) == 1
                                 and measured["rows"][0][0] == ("8MB" if error else "32MB"))
                    freeze_mixed_baseline(self, backend_first, error, base, measured, supported)
                    if not supported:
                        reason = f"sql_parse 既有混合限制：前序后端={backend_first}、错误={error}，实际={base}"
                        limits.append(reason)
                        self.context.step(f'{self.plan.key}-baseline-limit-{backend_first}-{error}',
                                          '记录开发前已存在的混合组合限制', status='SKIPPED', details={
                                              'intent': 'verify', 'expected': '仅对已支持组合要求 Hint 等价',
                                              'actual': base, 'analysis': reason})
                        continue
                    supported_count += 1
                probe.sql("SET work_mem='8MB'", tag="SET", protocol="Q")
                actual = probe.exchange(packet, "混合请求按 sql_parse 基线核对", {
                    field: base[field] for field in ("received", "sqlstates", "tags", "ready")})
                self.verify("混合周期响应及 RFQ 归属", base["received"], actual["received"], actual["received"] == base["received"])
                probe.snapshot({"work_mem": "8MB" if error else "32MB"})
            if not supported_count and limits:
                raise BaselineLimit('; '.join(limits))

    def pool_reuse(self):
        if self.plan.pool == "session":
            # Session pooling cannot hand off a live client's physical backend.
            with self.client("A") as a, self.client("B") as b:
                a.sql("SET work_mem='32MB'", tag="SET")
                b.sql("SET work_mem='64MB'", tag="SET")
                a.snapshot({"work_mem": "32MB"})
                b.snapshot({"work_mem": "64MB"})
                with self.client("C") as c:
                    initial = c.snapshot()
                    expected = self.default_values()["work_mem"]
                    self.verify("新 session pool 客户端无会话污染", expected, initial,
                                normalize("work_mem", initial["work_mem"]) == normalize("work_mem", expected))
            return
        with self.client("A") as a, self.client("B") as b:
            def enter(p, expected):
                if self.mode == "hint":
                    p.sql("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE", protocol="Q")
                p.sql("BEGIN READ WRITE" if self.mode == "sql_parse" else "BEGIN", tag="BEGIN", ready="T")
                return p.snapshot(expected, ready="T")
            first = enter(a, {})
            a.sql("SET work_mem='32MB'", tag="SET", ready="T")
            a.sql("COMMIT", tag="COMMIT")
            second = enter(b, {})
            if identity(first) != identity(second):
                raise Blocked(f"B 未复用 A 原后端：{identity(first)} -> {identity(second)}")
            b.sql("SET work_mem='64MB'", tag="SET", ready="T")
            b.sql("COMMIT", tag="COMMIT")
            third = enter(a, {"work_mem": "32MB"})
            self.verify("A 再次复用同一物理后端", identity(first), identity(third), identity(first) == identity(third))
            a.sql("COMMIT", tag="COMMIT")
            fourth = enter(b, {"work_mem": "64MB"})
            self.verify("B 会话参数隔离", identity(first), identity(fourth), identity(first) == identity(fourth))
            b.sql("COMMIT", tag="COMMIT")
        with self.client("C") as c:
            defaults = self.default_values()
            current = c.snapshot({"work_mem": defaults["work_mem"]})
            self.verify("新客户端 C 复用原后端且无旧值", identity(first), identity(current), identity(first) == identity(current))

    def mode_isolation(self):
        raise BaselineLimit("按用户要求只使用现有 postgres；同进程双模式交错专项不执行，不声称独立模式运行证明了 owner 隔离")

    def compatibility(self):
        # This is transmission/configuration behavior, never claimed as
        # transactional GUC alignment while reserve or synchronization is off.
        with self.client("compatibility") as p:
            self.begin_write_transaction(p, protocol="Q")
            p.sql("SET work_mem='8MB'", protocol="Q", tag="SET", ready="T")
            p.snapshot({"work_mem": "8MB"}, ready="T")
            if self.plan.protocol == "E":
                p.exchange(wire.message('P', wire.parse_payload('compat_stmt', "SET work_mem='32MB'")) + wire.sync_message(),
                           '兼容路径仅 P，不 E', {'tags': [], 'sqlstates': [], 'ready': ['T']})
                p.snapshot({'work_mem': '8MB'}, ready='T')
                p.exchange(wire.message('B', wire.bind_payload('', 'compat_stmt')) + wire.message('E', wire.execute_payload('')) + wire.sync_message(),
                           '兼容路径真实 E', {'tags': ['SET'], 'sqlstates': [], 'ready': ['T']})
            else:
                p.sql("SET work_mem='32MB'", protocol='Q', tag='SET', ready='T')
            p.snapshot({'work_mem': '32MB'}, ready='T')
            p.sql('ROLLBACK', protocol='Q', tag='ROLLBACK')
            # Backend PG transaction scope is sufficient here; we explicitly
            # do not require a frontend tx cache or cross-backend persistence.
            p.sql('SELECT 42::int', protocol='Q', tag='SELECT 1')

    def backend_cleanup(self, name):
        if name == "local_backend_reclaim":
            with self.client("unattached") as p:
                # This is the first business request: no preliminary SELECT.
                p.sql("SET work_mem='32MB'", tag="SET", protocol="E")
                p.snapshot({"work_mem": "32MB"})
                p.sql("RESET work_mem", tag="RESET", protocol="E")
                p.snapshot({"work_mem": self.default_values()["work_mem"]})
            with self.client("attached") as p:
                p.snapshot()
                p.sql("SET work_mem='64MB'", tag="SET", protocol="E")
                p.snapshot({"work_mem": "64MB"})
            # SQL alone does not prove detach/offline state and no physical
            # portal destruction; mandatory instrumentation is separately listed.
            return
        with self.client("disconnected") as p:
            self.baseline(p)
            p.sql("BEGIN READ WRITE" if self.mode == "sql_parse" else "BEGIN", tag="BEGIN", ready="T")
            p.sql("SET work_mem='32MB'", tag="SET", ready="T")
            before = p.snapshot({"work_mem": "32MB"}, ready="T")
        with self.client("replacement") as p:
            current = p.snapshot({"work_mem": self.default_values()["work_mem"]})
            if identity(before) != identity(current):
                raise Blocked(f"断连后取得新后端，未证明原连接清理：{identity(before)} -> {identity(current)}")


def clean_backend_defaults(context, node):
    """No SDK/startup timeout: startup options also change pg_settings.reset_val."""
    import psycopg
    endpoint = context.environment['nodes'][node]
    user = context.environment.get('user', 'postgres')
    password = (context.environment.get('users') or {}).get(user, {}).get('password')
    sql = ("SELECT current_setting('work_mem'), current_setting('statement_timeout'), "
           "current_setting('TimeZone')")
    with psycopg.connect(host=endpoint['host'], port=endpoint['port'], dbname='postgres',
                         user=user, password=password, options='', connect_timeout=10,
                         autocommit=True) as connection:
        rows = connection.execute(sql).fetchall()
    if len(rows) != 1:
        raise Blocked(f'{node} 无干净默认值')
    defaults = dict(zip(('work_mem', 'statement_timeout', 'TimeZone'), rows[0]))
    context.attach_text(f'clean-defaults-{node}.json', json.dumps({
        'node': node, 'sql': sql, 'startup_options': '', 'values': defaults}, ensure_ascii=False, indent=2))
    return defaults


def freeze_mixed_baseline(runner, backend_first, error, response, measured, supported):
    """Freeze actual sql_parse observations once, compare on every later run."""
    root = Path(runner.context.environment.get("guc_alignment_baseline_dir") or
                Path(__file__).resolve().parents[2] / "data/regression/fbasecman/guc-alignment-baselines")
    root.mkdir(parents=True, exist_ok=True)
    key = f"{runner.plan.topology}-{runner.plan.pool}-{runner.plan.reserve}-{backend_first}-{error}.json"
    path = root / key
    observation = {field: response[field] for field in ("received", "sqlstates", "tags", "ready")}
    observation['work_mem'] = measured['rows'][0][0] if len(measured['rows']) == 1 else None
    if path.exists():
        reference = json.loads(path.read_text())
        runner.verify('sql_parse 混合结果保持开发前基线', reference['observation'], observation,
                      reference['observation'] == observation)
    else:
        reference = {'observation': observation, 'supported': supported,
                     'binary_sha256': hashlib.sha256(Path(runner.context.environment['fbasecman_bin']).read_bytes()).hexdigest()}
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(reference, ensure_ascii=False, indent=2))
        temporary.replace(path)
    runner.context.attach_text(f'mixed-baseline-{key}', json.dumps(reference, ensure_ascii=False, indent=2))


def render_alignment_config(context, path, plan, mode):
    nodes = context.environment.get("nodes") or {}
    if "mmr1" not in nodes or "mmr2" not in nodes:
        raise Blocked("GUC 对齐需要明确的 mmr1/mmr2 节点")
    if plan.topology == "replication":
        standby = (context.environment.get("extra_nodes") or {}).get("pg_3")
        if not standby:
            raise Blocked("主备阶段缺少 pg_3 健康备库，不能只在主库运行替代")
    rows = context.sql("mmr1", "SELECT group_name FROM fdd.mmr_group ORDER BY group_name").rows
    declared = context.environment.get("mmr_group_name")
    if declared:
        if (declared,) not in rows:
            raise Blocked(f"指定 MMR group 不存在：{declared}")
        group_name = declared
    elif len(rows) == 1:
        group_name = rows[0][0]
    else:
        raise Blocked("存在多个或没有 MMR group；请在环境上下文明确 mmr_group_name")
    database = "mmr_group" if plan.topology == "mmr" else "rep_group"
    standby_names = {}
    for alias, primary, legacy_name in (("pg_3", "mmr1", "pg_240"), ("pg_4", "mmr2", "pg_250")):
        endpoint = (context.environment.get("extra_nodes") or {}).get(alias)
        if endpoint:
            declared_name = endpoint.get('application_name')
            if declared_name:
                standby_names[alias] = declared_name
            else:
                available = {row[0] for row in context.sql(primary, 'SELECT application_name FROM pg_stat_replication').rows}
                candidates = [name for name in (alias, legacy_name) if name in available]
                if len(candidates) != 1:
                    raise Blocked(f'{alias} 的复制 application_name 无法唯一确定；请在 extra_nodes 声明，实际={sorted(available)}')
                standby_names[alias] = candidates[0]
    def transform(text):
        if not plan.enable_sync:
            text = text.replace("enable_guc_sync yes", "enable_guc_sync no", 1)
        if plan.protocol == "product":
            text = text.replace("coroutine_stack_size 16", "coroutine_stack_size 64").replace("workers 8", "workers 2")
        for alias, legacy_name in (("pg_3", "pg_240"), ("pg_4", "pg_250")):
            endpoint = (context.environment.get("extra_nodes") or {}).get(alias)
            if endpoint:
                text = text.replace(f'application_name "{legacy_name}"',
                                    f'application_name {json.dumps(standby_names[alias])}')
        text = text.replace('group_names "mmr_group"', f'group_names "{database}"', 1)
        text = text.replace('pool "transaction"', f'pool "{plan.pool}"', 1)
        start = text.index('user "postgres" {')
        end = text.index('user "admin" {', start)
        rule = text[start:end].replace('pool_size 20', f'pool_size {3 if plan.pool == "session" else 1}')
        rule = rule.replace('pool_reserve_prepared_statement yes',
                            f'pool_reserve_prepared_statement {"yes" if plan.reserve else "no"}')
        return text[:start] + rule + text[end:]
    return render_config(context, path.resolve(), mode=mode, transform=transform, mmr_group_name=group_name)


def wait_routing(context, port, topology):
    import time

    from products.fbasecman.observations import parse_group_routing
    database = "mmr_group" if topology == "mmr" else "rep_group"
    deadline = time.monotonic() + 30
    latest = ""
    while time.monotonic() < deadline:
        context.check_cancel()
        result = _console_query(context, context.environment.get("psql_bin", "/usr/bin/psql"), port,
                                f"SHOW GROUP_ROUTING {database};")
        latest = result.stdout or ""
        rows = [item.details for item in parse_group_routing(latest)]
        roles = {row.get("effective_grouprole") for row in rows
                 if row.get("effective_state") == "active" and str(row.get("route_status", "")).upper() in {"OK", "READY", "AVAILABLE"}}
        ready = "write-leader" in roles if topology == "mmr" else ("primary" in roles and bool(roles & {"standby", "replica"}))
        if ready:
            context.step(f"{database}-ready-{port}", "路由候选已就绪", details={
                "intent": "prepare", "command": f"SHOW GROUP_ROUTING {database}",
                "expected": "MMR 可写或主备 primary/standby 均健康", "actual": latest})
            return
        time.sleep(.2)
    raise Blocked(f"{database} 路由未就绪：{latest}")


class GucAlignmentCase:
    def __init__(self, group, mode):
        if group not in SCENARIOS or mode not in {"hint", "sql_parse"}:
            raise ValueError((group, mode))
        self.group, self.mode = group, mode
        self.title = f"{mode} GUC {group} 对齐回归"
        self.summary = "验证 GUC 执行边界、事务作用域、协议响应和后端重部署；内部缓存/失败清理以产品侧证据独立验收。"
        self.coverage = []

    def run(self, context: CaseContext):
        plans = make_plan(self.group)
        selected = context.environment.get("guc_alignment_scenarios")
        if selected is not None and (not isinstance(selected, list) or not selected or not set(selected).issubset(set(SCENARIOS[self.group]) | {"product_cache_boundaries"})):
            raise ValueError("guc_alignment_scenarios 必须是本综合场景的非空子场景数组")
        selected_topologies = context.environment.get("guc_alignment_topologies", ["mmr", "replication"])
        if not selected_topologies or not set(selected_topologies).issubset({"mmr", "replication"}):
            raise ValueError("guc_alignment_topologies 无效")
        plan_file = context.attach_text("guc-alignment-plan.json", json.dumps({
            "mode": self.mode, "group": self.group, "plan": [asdict(p) | {"key": p.key} for p in plans],
            "design_items": DESIGN_ITEMS, "acceptance": ACCEPTANCE,
        }, ensure_ascii=False, indent=2))
        context.step("alignment-plan", "归档完整测试计划及验收映射", details={
            "intent": "prepare", "expected": "完整计划归档，选择复跑不冒充完整覆盖",
            "actual": {"planned": len(plans), "selected_scenarios": selected, "topologies": selected_topologies},
            "evidence": plan_file})
        binary = Path(context.environment.get("fbasecman_bin", ""))
        if not binary.is_file():
            self.coverage = [asdict(p) | {"key": p.key, "status": "BLOCKED", "executed": False,
                                          "reason": "缺少可执行的 fbasecman_bin"} for p in plans]
            self.finish(context)
            raise Blocked("缺少可执行的 fbasecman_bin")
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        context.attach_text("tested-build.json", json.dumps({"binary": str(binary), "sha256": digest}, indent=2))
        self.coverage = []
        process = None
        current_config = None
        startup_failure = None
        try:
            for plan in plans:
                context.check_cancel()
                row = asdict(plan) | {"key": plan.key, "status": "UNKNOWN", "executed": False}
                self.coverage.append(row)
                if (selected is not None and plan.scenario not in selected) or plan.topology not in selected_topologies:
                    row.update(status="BLOCKED", reason="选择复跑，本计划项未执行")
                    continue
                config_key = (plan.topology, plan.pool, plan.reserve, plan.enable_sync, plan.protocol == "product")
                if config_key != current_config:
                    if process is not None:
                        context.stop_processes()
                        process = None
                    current_config = config_key
                    startup_failure = None
                    context.environment["proxy_port"] = _free_port()
                    folder = context.output_dir / "configs" / f"{plan.topology}-{plan.pool}-{plan.reserve}-{plan.enable_sync}-{plan.protocol == 'product'}"
                    path = folder / "fbasecman.conf"
                    try:
                        port = render_alignment_config(context, path, plan, self.mode)
                        context.attach_file(f"config-{plan.topology}-{plan.pool}-{plan.reserve}.conf", path)
                        tested = binary
                        if plan.protocol == "product":
                            from products.fbasecman.guc_instrumentation import (
                                build_test_proxy,
                            )
                            tested = build_test_proxy(context)
                        process = context.start_process([str(tested.resolve()), str(path.resolve())], cwd=context.output_dir.resolve(), ready_host=context.environment.get("local_host", "127.0.0.1"), ready_port=port)
                        wait_routing(context, port, plan.topology)
                    except Blocked as exc:
                        startup_failure = ("BLOCKED", str(exc))
                    except Cancelled:
                        raise
                    except Exception as exc:  # noqa: BLE001 - preserve per-config infrastructure verdict
                        # Some current builds reject session+reserve entirely.
                        # Keep the required design branch blocked, never silently
                        # turn off reserve and pretend to test the original config.
                        startup_log = "\n".join(p.read_text(errors="replace") for p in context.output_dir.glob("process-*.log"))
                        if "pool type can only be 'transaction'" in startup_log and plan.pool == "session" and self.mode == "hint":
                            startup_failure = ("SKIPPED", "当前构建的 Hint 只允许 transaction pool；session 设计分支无法执行")
                        elif 'prepared statements support in session pool' in startup_log and plan.pool == "session" and plan.reserve:
                            startup_failure = ("SKIPPED", "当前构建拒绝 session pool + prepared statement reserve；设计配置分支无法执行")
                        else:
                            startup_failure = ("ERROR", str(exc))
                if startup_failure:
                    row.update(status=startup_failure[0], reason=startup_failure[1])
                    continue
                runner = ScenarioRunner(context, plan, self.mode, port)
                runner.config, runner.binary = path, tested
                try:
                    row["executed"] = True
                    runner.run()
                    row.update(status="PASS")
                except BaselineLimit as exc:
                    row.update(status="SKIPPED", reason=str(exc))
                except Blocked as exc:
                    row.update(status="BLOCKED", reason=str(exc))
                except AssertionError as exc:
                    row.update(status="FAIL", reason=str(exc))
                except Cancelled:
                    row.update(status="CANCELLED", reason="用户取消")
                    raise
                except Exception as exc:  # noqa: BLE001 - isolate independent scenarios after saving evidence
                    row.update(status="ERROR", reason=str(exc))
                row["steps"] = runner.results
                context.step(plan.key, f"{plan.topology}/{self.mode}/{plan.scenario} 子场景结论",
                             status=row["status"], details={
                                 "intent": "verify", "expected": "本子场景所有必需检查与清理通过",
                                 "actual": row, "analysis": row.get("reason", "分项断言全部通过"),
                                 "assertion": {"type": "all_steps_pass", "passed": row["status"] == "PASS"}})
        finally:
            context.stop_processes()
            for path in (context.output_dir / "configs").glob("*/fbasecman.log"):
                context.attach_file(f"{path.parent.name}-fbasecman.log", path)
            # If cancelled before all items, preserve the remaining denominator.
            seen = {row["key"] for row in self.coverage}
            self.coverage += [asdict(p) | {"key": p.key, "status": "BLOCKED", "executed": False,
                                         "reason": "执行中断，未执行"} for p in plans if p.key not in seen]
            self.finish(context)
        statuses = {row["status"] for row in self.coverage}
        if "FAIL" in statuses:
            raise AssertionError("GUC 对齐存在业务断言失败，详见子场景与报文证据")
        if "ERROR" in statuses:
            raise RuntimeError("GUC 对齐存在基础设施/协议异常，详见子场景证据")
        if not statuses.issubset({"PASS", "SKIPPED"}) or "PASS" not in statuses:
            reasons = list(dict.fromkeys(row.get("reason", "未执行") for row in self.coverage if row["status"] in {"BLOCKED", "CANCELLED"}))
            raise Blocked("GUC 必需检查未完成：" + "；".join(reasons[:3] or ["没有支持范围内的通过检查"]))
        return True

    def finish(self, context):
        counts = Counter(row["status"] for row in self.coverage)
        def traced(names):
            relevant = [row for row in self.coverage if row["scenario"] in names]
            statuses = {row["status"] for row in relevant}
            state = ("NOT_IN_CASE" if not relevant else "FAIL" if "FAIL" in statuses else
                     "ERROR" if "ERROR" in statuses else "SKIPPED" if statuses == {"SKIPPED"} else
                     "PASS" if statuses.issubset({"PASS", "SKIPPED"}) else "BLOCKED")
            return {"status": state, "planned": len(relevant), "executed": sum(r["executed"] for r in relevant),
                    "checks": [r["key"] for r in relevant]}
        design_coverage = {str(item): traced(names) for item, names in DESIGN_ITEMS.items()}
        acceptance_coverage = {item: traced(names) for item, names in ACCEPTANCE.items()}
        internal_rows = [row for row in self.coverage if row["scenario"] in INTERNAL]
        internal_statuses = {row["status"] for row in internal_rows}
        internal_state = ("FAIL" if "FAIL" in internal_statuses else "ERROR" if "ERROR" in internal_statuses
                          else "PASS" if internal_statuses == {"PASS"} else "BLOCKED")
        for item in ("parse_cache_unchanged", "recording_boundaries", "safe_backend_reclaim", "failure_cleanup"):
            if acceptance_coverage[item]["status"] == "PASS" and internal_state != "PASS":
                acceptance_coverage[item].update(status=internal_state, reason="内部边界/故障检查尚未通过")
        summary = {
            "design_coverage": design_coverage, "acceptance_coverage": acceptance_coverage,
            "planned": len(self.coverage), "executed": sum(row["executed"] for row in self.coverage),
            "passed": counts["PASS"], "failed": counts["FAIL"], "blocked": counts["BLOCKED"],
            "errors": counts["ERROR"], "baseline_limits": counts["SKIPPED"], "unexecuted": sum(not row["executed"] for row in self.coverage),
            "checks": self.coverage,
            "internal_acceptance": {"status": internal_state, "checks": [row["key"] for row in internal_rows]},
        }
        evidence = context.attach_text("guc-alignment-coverage.json", json.dumps(summary, ensure_ascii=False, indent=2))
        context.step("alignment-coverage", "完整覆盖统计及内部验收边界",
                     status="FAIL" if counts["FAIL"] else "ERROR" if counts["ERROR"] else "BLOCKED" if counts["BLOCKED"] else "PASS",
                     details={"intent": "verify", "expected": "完整矩阵及产品侧验收全部通过",
                              "actual": {key: value for key, value in summary.items() if key not in {"checks", "design_coverage", "acceptance_coverage"}},
                              "analysis": "保留同次计划、失败及未执行分母；SQL 通过不等于内部缓存已验证", "evidence": evidence})
