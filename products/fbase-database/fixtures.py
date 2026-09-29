"""FBase regression fixtures ported verbatim onto the platform primitives.

Every fixture below mirrors the legacy fbase_regress ``framework.fixtures``
implementation: identical SQL, identical identifier validation, identical
cleanup priorities and identical safety semantics.  ``SafetyError`` maps to a
platform BLOCKED verdict, ``ConfigError``/``OperationError`` map to step or
setup failures, and cleanup callbacks keep the legacy priority ordering
(bindings before subscriptions before replication sets before tables).
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from platform_regress import Blocked
from platform_regress import steps as platform_steps
from platform_regress.engine import resolve_selector
from platform_regress.execution.forensics import CoreSnapshot


class SafetyError(Exception):
    """Fixture refuses to mutate shared state (legacy SafetyError → BLOCKED)."""


class ConfigError(ValueError):
    """Invalid fixture/step definition (legacy ConfigError → FAILED)."""


class OperationError(RuntimeError):
    """A command or SQL statement failed (legacy OperationError → FAILED)."""


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_GUC_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
TMP_PREFIX = os.environ.get("FBASE_REGRESS_TMP_PREFIX", "/tmp/fbase_regress_")

def _env(context, key, default):
    """Read an environment-overridable literal injected by the provider."""
    env = getattr(context, "environment", None) or {}
    value = env.get(key)
    return value if value not in (None, "") else default




def _identifier(value):
    if not _IDENTIFIER.match(value or ""):
        raise ConfigError("fixture 中包含非法 PostgreSQL 标识符: %s" % value)
    return '"%s"' % value.replace('"', '""')


def _literal(value):
    return "'%s'" % str(value).replace("'", "''")


def _load_isolated_module():
    path = Path(__file__).parent / "isolated.py"
    spec = importlib.util.spec_from_file_location("_fbase_isolated_fx", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 FBase 隔离集群 fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


isolated = _load_isolated_module()


# ---------------------------------------------------------------------------
# Legacy adapter layer: command_runner / postgres / manager equivalents.
# ---------------------------------------------------------------------------

def runner_run(context, argv, check=True, timeout=None, input_text=None):
    """Run argv like the legacy CommandRunner (merged stderr, rc=124)."""
    argv = [str(item) for item in argv]
    effective = timeout if timeout is not None else platform_steps.DEFAULT_COMMAND_TIMEOUT
    try:
        result = context.command(argv, timeout_seconds=effective,
                                 input_text=input_text, merge_stderr=True)
        returncode, output = result.returncode, result.stdout
    except TimeoutError as exc:
        returncode = 124
        output = (getattr(exc, "partial_stdout", "") or
                  "") + "\n命令执行超时（%ss）" % effective
    completed = subprocess.CompletedProcess(argv, returncode, output, None)
    if check and completed.returncode != 0:
        raise OperationError("命令执行失败(%s): %s\n%s" % (
            completed.returncode, " ".join(argv), completed.stdout.rstrip()))
    return completed


def binary(context, name):
    """Return the managed FBase binary path used by the environment."""
    return platform_steps._db_binary(context, name)


def pg_ctl(context, node, action, check=True):
    """Run pg_ctl on a managed node like the legacy environment manager."""
    endpoint = context.node_endpoint(node)
    argv = platform_steps._pg_ctl_argv(context, endpoint, action)
    completed = runner_run(context, argv, check=False)
    if check and completed.returncode != 0:
        raise OperationError("命令执行失败(%s): %s\n%s" % (
            completed.returncode, " ".join(argv), completed.stdout.rstrip()))
    return completed


def managed_node_order(context):
    return platform_steps._managed_node_order(context)


def cluster_apply(context, action):
    """Apply a cluster-wide lifecycle action with legacy manager semantics."""
    order = managed_node_order(context)
    if action == "reload":
        for node in order:
            pg_ctl(context, node, "reload")
    elif action == "start":
        for node in order:
            if pg_ctl(context, node, "status", check=False).returncode != 0:
                pg_ctl(context, node, "start")
    elif action == "stop":
        for node in reversed(order):
            if pg_ctl(context, node, "status", check=False).returncode == 0:
                pg_ctl(context, node, "stop")
    elif action == "restart":
        for node in order:
            status = pg_ctl(context, node, "status", check=False).returncode
            pg_ctl(context, node, "restart" if status == 0 else "start")
    else:
        raise ConfigError("不支持的 cluster action: %s" % action)


def postgres_execute(context, node, user, database, sql, structured=False,
                     timeout=None, connection=None):
    return platform_steps.execute_psql(
        context, node, sql, user=user, database=database,
        structured=structured, timeout=timeout, connection=connection)


def postgres_scalar(context, node, database, sql, user="postgres"):
    result = postgres_execute(context, node, user, database, sql,
                              structured=True)
    if result.returncode != 0:
        raise OperationError(result.output.strip() or "SQL 执行失败")
    if len(result.rows) != 1 or len(result.rows[0]) != 1:
        raise OperationError("预期单行单列，实际为 %s 行" % len(result.rows))
    return result.rows[0][0]


def postgres_checked(context, node, user, database, sql, structured=False):
    result = postgres_execute(context, node, user, database, sql,
                              structured=structured)
    if result.returncode != 0:
        raise OperationError(result.output.strip() or "SQL 执行失败")
    return result


def query_value(context, node, database, sql, timeout=5):
    """Read one scalar without allowing a health probe to block a run."""
    endpoint = context.node_endpoint(node)
    completed = runner_run(context, [
        binary(context, "psql"), "-X", "-v", "ON_ERROR_STOP=1", "-A", "-t",
        "-h", str(endpoint["host"]), "-p", str(endpoint["port"]),
        "-U", _env(context, "user", "postgres"), "-d", database, "-c", sql,
    ], check=True, timeout=timeout)
    lines = completed.stdout.strip().splitlines()
    return lines[-1].strip() if lines else ""


def session_psql(context, port, statement, timeout=60):
    """Execute SQL against a disposable session node without node selectors."""
    return runner_run(context, [
        binary(context, "psql"), "-X", "-v", "ON_ERROR_STOP=1",
        "-h", _env(context, "local_host", "127.0.0.1"), "-p", str(port), "-U", _env(context, "user", "postgres"),
        "-d", "postgres", "-c", statement], timeout=timeout)


def defer(context, title, callback, priority=0):
    """Register cleanup, preserving the legacy "title: error" aggregation."""
    def action():
        try:
            callback()
        except Exception as exc:  # noqa: BLE001 - legacy aggregates every error
            raise RuntimeError("%s: %s" % (title, exc))
    context.defer_cleanup(action, priority=priority)


def _groups(context):
    return context.environment.get("node_groups") or {}


def _plugins(context):
    detail = context.environment.get("plugins_detail")
    if detail is not None:
        return detail
    return {name: {} for name in context.environment.get("plugins") or []}


def inspect_setting_configuration(context, node, name):
    """Return the pg_file_settings entries that define one GUC."""
    if not _GUC_NAME.match(name):
        raise OperationError("非法 PostgreSQL 参数名: %s" % name)
    sql = (
        "SELECT sourcefile AS config_file, sourceline AS line, "
        "format('%%s = %%L', name, setting) AS configuration, "
        "applied, COALESCE(error, '') AS error "
        "FROM pg_file_settings WHERE name = '%s' ORDER BY seqno" % name)
    result = postgres_execute(context, node, _env(context, "user", "postgres"), "postgres", sql,
                              structured=True)
    return {
        "sql": sql,
        "output": result.display_output,
        "error": "" if result.returncode == 0 else
                 (result.output.strip() or "pg_file_settings 查询失败"),
    }


def check_setting(context, node, spec):
    """Evaluate one SHOW/pg_file_settings requirement like the legacy client."""
    name = spec["name"]
    if not _GUC_NAME.match(name):
        raise OperationError("非法 PostgreSQL 参数名: %s" % name)
    configuration = inspect_setting_configuration(context, node, name)
    sql = "SHOW %s" % name
    result = postgres_execute(context, node, _env(context, "user", "postgres"), "postgres", sql,
                              structured=True)
    actual = (result.rows[0][0] if result.returncode == 0 and
              len(result.rows) == 1 and len(result.rows[0]) == 1
              else "<查询失败>")
    if "equals" in spec:
        expected = str(spec["equals"])
        requirement = "等于 %s" % expected
        matched = str(actual).lower() == expected.lower()
    else:
        expected = str(spec["contains"])
        requirement = "包含 %s" % expected
        values = {item.strip() for item in str(actual).split(",")}
        matched = expected in values or expected in str(actual)
    matched = result.returncode == 0 and matched
    return {
        "source": spec.get("source", "requirement"),
        "node": node, "name": name, "sql": sql,
        "output": result.display_output, "actual": actual,
        "config_sql": configuration["sql"],
        "config_output": configuration["output"],
        "config_error": configuration["error"],
        "requirement": requirement, "purpose": spec["purpose"],
        "matched": matched,
        "error": "" if result.returncode == 0 else
                 (result.output.strip() or "SHOW 执行失败"),
    }


def evaluate_setting_requirements(context, requirements):
    """Return (blocker, details) for a requirements.settings spec list."""
    requirements = requirements or {}
    details = []
    blocker = ""
    specs = requirements.get("settings") or []
    for spec in specs:
        selector = spec.get("node", requirements.get("node", "primary"))
        try:
            node = resolve_selector(context.environment, selector)
        except Blocked as exc:
            return str(exc), details
        name = spec["name"]
        if not _GUC_NAME.match(name):
            return "非法 PostgreSQL 参数名: %s" % name, details
        try:
            detail = check_setting(context, node, spec)
        except Exception as exc:  # noqa: BLE001 - probe errors are detail rows
            detail = {
                "source": "requirement", "node": node,
                "name": name, "sql": "SHOW %s" % name,
                "config_sql": "SELECT ... FROM pg_file_settings WHERE name = '%s'" % name,
                "config_output": "<查询失败>", "config_error": str(exc),
                "output": "<查询失败>", "actual": "<查询失败>",
                "requirement": ("等于 %s" % spec["equals"] if "equals" in spec
                                else "包含 %s" % spec["contains"]),
                "purpose": spec["purpose"], "matched": False,
                "error": str(exc),
            }
        details.append(detail)
        if not detail["matched"] and not blocker:
            if detail.get("error"):
                blocker = "无法读取 PostgreSQL 参数 %s: %s" % (name, detail["error"])
            else:
                blocker = "PostgreSQL 参数 %s 不满足要求（实际=%s，要求=%s）" % (
                    name, detail["actual"], detail["requirement"])
    return blocker, details


# ---------------------------------------------------------------------------
# Managed-cluster health probes (legacy EnvironmentHealthMixin).
# ---------------------------------------------------------------------------

def _node_health_role(role, process_state, recovery):
    if process_state != "running" or recovery not in ("true", "false"):
        return "unhealthy"
    expects_standby = role == "standby" or role.startswith("mmr_standby:")
    return "healthy" if (recovery == "true") == expects_standby else "unhealthy"


def _mmr_rows_healthy(rows):
    return all(row.get("is_abnormal") == "OK" and
               row.get("nodestate") == "ACTIVE" and
               row.get("real_nodestate") == "ACTIVE" for row in rows)


def _mmr_udf_check(context, node, database, all_nodes=True):
    sql = (
        "SELECT coalesce(json_agg(row_to_json(status))::text, '[]') FROM ("
        "SELECT nodename,nodestate,real_nodestate,is_abnormal,detail "
        "FROM fdd.show_node_info(%s,false) ORDER BY nodename) status" %
        ("true" if all_nodes else "false"))
    raw = query_value(context, node, database, sql)
    try:
        return json.loads(raw)
    except ValueError:
        raise OperationError("无法解析 fdd.show_node_info 返回值: %s" % raw)


def _plugins_healthy(context, node):
    plugins = _plugins(context)
    extensions = sorted(plugins)
    extension_names = ", ".join(_literal(name) for name in extensions)
    installed = int(query_value(
        context, node, "postgres",
        "SELECT count(*) FROM pg_extension WHERE extname IN (%s)" % extension_names))
    if installed != len(extensions):
        return False
    required_preload = {
        name for name in extensions
        if (plugins.get(name) or {}).get("preload", True)}
    configured = query_value(context, node, "postgres", "SHOW shared_preload_libraries")
    actual_preload = {name.strip() for name in configured.split(",") if name.strip()}
    return required_preload.issubset(actual_preload)


def node_status_row(context, name):
    """Compute one (name, role, host, port, state, recovery, health, check, dir)."""
    nodes = context.environment.get("nodes") or {}
    endpoint = nodes.get(name)
    if not isinstance(endpoint, dict):
        return None
    host = str(endpoint.get("host", ""))
    port = endpoint.get("port", "")
    data_dir = endpoint.get("data_dir")
    role = endpoint.get("role") or "standalone"
    if data_dir and not Path(str(data_dir)).exists():
        process_state, recovery = "not_created", "-"
    else:
        completed = runner_run(context, [
            binary(context, "pg_isready"), "-h", host, "-p", str(port),
            "-d", "postgres", "-U", _env(context, "user", "postgres"), "-t", "2",
        ], check=False)
        process_state = "running" if completed.returncode == 0 else "stopped"
        if process_state == "running":
            try:
                recovery_value = query_value(
                    context, name, "postgres", "SELECT pg_is_in_recovery()")
                recovery = {"t": "true", "f": "false"}.get(
                    recovery_value.lower(), recovery_value)
            except Exception:  # noqa: BLE001 - unknown recovery state
                recovery = "unknown"
        else:
            recovery = "-"
    health = _node_health_role(role, process_state, recovery)
    plugins = _plugins(context)
    health_check = "plugins" if plugins else "postgres"
    if health == "healthy" and plugins:
        try:
            if not _plugins_healthy(context, name):
                health = "unhealthy"
        except OperationError:
            health = "unhealthy"
    if role.startswith("mmr_primary:"):
        health_check = "fdd_udf"
        if health == "healthy":
            databases = (plugins.get("fdd_mmr") or {}).get(
                "enabled_databases", ["postgres"])
            try:
                for database in databases:
                    udf_rows = _mmr_udf_check(context, name, database,
                                            all_nodes=False)
                    if len(udf_rows) != 1 or not _mmr_rows_healthy(udf_rows):
                        health = "unhealthy"
                        break
            except OperationError:
                health = "unhealthy"
    return (name, role, host, port, process_state, recovery, health,
            health_check, str(data_dir or ""))


def status_rows(context):
    return [row for row in
            (node_status_row(context, name)
             for name in managed_node_order(context))
            if row is not None]


def environment_blocker(context):
    """Mirror the legacy run-level environment blocker."""
    try:
        rows = status_rows(context)
    except Exception as exc:  # noqa: BLE001 - probes must not crash preflight
        return "无法检查 cluster 状态: %s" % exc
    if not any(row[4] == "running" and row[6] == "healthy" for row in rows):
        return "cluster %s 没有健康节点" % (
            context.environment.get("cluster_name") or "-")
    return ""


def declared_nodes(definition):
    """Resolve every node a case may touch, in legacy declaration order."""
    selectors = list(definition.get("evidence_nodes") or [])
    for step in definition.get("steps") or []:
        selectors.append(step.get("node", "primary"))
    for fixture in definition.get("fixtures") or []:
        if not isinstance(fixture, dict):
            continue
        selectors.extend(fixture.get("nodes") or [])
        if fixture.get("node"):
            selectors.append(fixture["node"])
        elif fixture.get("type") in ("database", "roles", "settings"):
            selectors.append("primary")
    if not selectors:
        selectors.append("primary")
    return selectors


def declared_nodes_blocker(context, definition, environment=None):
    """Legacy _case_node_blocker: every declared node must be running+healthy."""
    environment = environment or context.environment
    try:
        names = []
        for selector in declared_nodes(definition):
            if any(step.get("type") == "cluster_action"
                   for step in definition.get("steps") or []):
                names = managed_node_order(context)
                break
            name = resolve_selector(environment, selector)
            if name not in names:
                names.append(name)
    except Exception as exc:  # noqa: BLE001 - mirrors ConfigError propagation
        return "无法检查用例涉及节点: %s" % exc, []
    status_by_node = {}
    try:
        status_by_node = {row[0]: row for row in status_rows(context)}
    except Exception as exc:  # noqa: BLE001
        return "无法检查用例涉及节点: %s" % exc, names
    unhealthy = []
    for name in names:
        row = status_by_node.get(name)
        if not row:
            unhealthy.append("%s=未返回状态" % name)
        elif row[4] != "running" or row[6] != "healthy":
            unhealthy.append("%s=process:%s,health:%s" % (name, row[4], row[6]))
    if unhealthy:
        return "用例涉及节点不可用: %s" % "; ".join(unhealthy), names
    return "", names


# ---------------------------------------------------------------------------
# Server-log and core-file evidence collectors (legacy ServerLogCollector /
# CoreCollector), kept evidence-neutral: errors surface as case failures via
# the caller, never silently.
# ---------------------------------------------------------------------------

_LOG_DESTINATIONS = {"stderr": "postgresql.log", "csvlog": "postgresql.csv"}


class ServerLogCollector:
    """Collect postmaster log tails for one managed node into the case dir."""

    def __init__(self, context, node, case_dir, label=None):
        self.context = context
        self.node = node
        self.case_dir = Path(case_dir)
        endpoint = context.node_endpoint(node)
        self.data_dir = Path(str(endpoint.get("data_dir") or ""))
        self.host = str(endpoint.get("host", ""))
        self.before = {}
        self.errors = []
        self.label = label
        self.finished = False

    def _output_name(self, output_name):
        if not self.label:
            return output_name
        path = Path(output_name)
        return "%s.%s%s" % (path.stem, self.label, path.suffix)

    def start(self):
        for output_name in _LOG_DESTINATIONS.values():
            path = self.case_dir / self._output_name(output_name)
            if path.exists():
                path.unlink()
        self.before = self._snapshot()

    def finish(self, allow_missing_before=False):
        if self.finished:
            return []
        self.finished = True
        after = self._snapshot(ignore_errors=allow_missing_before)
        evidence = []
        for destination, output_name in _LOG_DESTINATIONS.items():
            output_name = self._output_name(output_name)
            segments = []
            before_path, before_size = self.before.get(destination, (None, 0))
            after_path, _ = after.get(destination, (None, 0))
            if before_path:
                segments.append((before_path, before_size))
            if after_path and after_path != before_path:
                segments.append((after_path, 0))
            content = b""
            for path, offset in segments:
                try:
                    if self.context.is_local(self.host):
                        with path.open("rb") as stream:
                            stream.seek(offset)
                            content += stream.read()
                    else:
                        transport = (self.context.environment.get("cluster", {})
                                     .get("transport") or {})
                        import os as _os
                        ssh_user = (transport.get("ssh_user") or
                                    self.context.environment.get("ssh_user") or
                                    _os.environ.get("USER") or "postgres")
                        ssh_port = (transport.get("ssh_port") or
                                    self.context.environment.get("ssh_port") or 22)
                        result = self.context.command(
                            ["ssh", "-p", str(ssh_port), "%s@%s" % (ssh_user, self.host),
                             "tail -c +%s %s" % (offset + 1, path)],
                            timeout_seconds=60, merge_stderr=True)
                        if result.returncode != 0:
                            raise OSError(result.stdout.strip() or "远端日志读取失败")
                        content += result.stdout.encode("utf-8")
                except OSError as exc:
                    if not (allow_missing_before and path == before_path):
                        self.errors.append("%s: %s" % (path, exc))
            if content:
                reference = self.context.attach_bytes(output_name, content)
                evidence.append(reference)
        return evidence

    def _snapshot(self, ignore_errors=False):
        result = {}
        for destination in _LOG_DESTINATIONS:
            try:
                relative = query_value(
                    self.context, self.node, "postgres",
                    "SELECT coalesce(pg_current_logfile('%s'), '')" % destination)
                if not relative:
                    continue
                path = Path(relative)
                if not path.is_absolute():
                    path = self.data_dir / path
                if self.context.is_local(self.host):
                    size = path.stat().st_size
                else:
                    continue
                result[destination] = (path, size)
            except Exception as exc:  # noqa: BLE001 - snapshot probe
                if not ignore_errors:
                    self.errors.append("%s: %s" % (destination, exc))
        return result


def start_log_collectors(context, names, case_dir):
    multiple = len(names) > 1
    collectors = [ServerLogCollector(context, name, case_dir,
                                     label=name if multiple else None)
                  for name in names]
    for collector in collectors:
        collector.start()
    return collectors


def finish_log_collectors(context, collectors):
    """Finish collectors; return the combined error string ("" when clean)."""
    errors = []
    for collector in collectors:
        collector.finish(
            allow_missing_before=context.values.get("server_log_reset", False))
        errors.extend(collector.errors)
    return "服务端日志收集失败: %s" % "; ".join(errors) if errors else ""


def core_file_roots(context, output_dir):
    nodes = context.environment.get("nodes") or {}
    roots = [Path(output_dir)]
    for endpoint in nodes.values():
        if not isinstance(endpoint, dict):
            continue
        data_dir = endpoint.get("data_dir")
        if data_dir and context.is_local(endpoint.get("host", "")):
            roots.append(Path(str(data_dir)))
    return roots


def _core_files(roots):
    return CoreSnapshot(roots).before


def collect_new_core_files(context, before, output_dir):
    """Return core files created or modified since the ``before`` snapshot."""
    snapshot = CoreSnapshot(core_file_roots(context, output_dir))
    snapshot.before = dict(before)
    return [str(p) for p in snapshot.detect_new_cores()]


# ---------------------------------------------------------------------------
# Fixture registry ported verbatim from the legacy executor.
# ---------------------------------------------------------------------------

def _cluster(unused_context, unused_definition, unused_options):
    return None


def _node_running_guard(context, definition, options):
    """Ensure a deliberately stopped member is brought back before reporting."""
    node = context.resolve_node(options.get("node", "primary"))
    status = pg_ctl(context, node, "status", check=False)
    if status.returncode != 0:
        raise SafetyError("%s 初始未运行，拒绝执行成员连接失败场景" % node)

    def cleanup():
        endpoint = context.node_endpoint(node)
        ready = runner_run(
            context, [binary(context, "pg_isready"), "-h", str(endpoint["host"]),
                      "-p", str(endpoint["port"]), "-d", "postgres", "-U", _env(context, "user", "postgres")],
            check=False, timeout=10)
        if ready.returncode != 0:
            pg_ctl(context, node, "stop_immediate", check=False)
            pg_ctl(context, node, "start")

    defer(context, "恢复成员 %s 为可连接运行状态" % node, cleanup, priority=250)


def _faketime_node_guard(context, definition, options):
    """Restore a member under the real host clock after a faketime start."""
    node = context.resolve_node(options.get("node", "primary"))
    if runner_run(context, ["sh", "-c", "command -v faketime"],
                  check=False).returncode != 0:
        raise SafetyError("缺少 faketime，无法在同机成员间制造可控时差")
    if pg_ctl(context, node, "status", check=False).returncode != 0:
        raise SafetyError("%s 初始未运行，拒绝修改其进程时间" % node)

    def cleanup():
        status = pg_ctl(context, node, "status", check=False)
        data_dir = Path(str(context.node_endpoint(node)["data_dir"]))
        fake_clock = False
        pid_file = data_dir / "postmaster.pid"
        if status.returncode == 0 and pid_file.exists():
            try:
                pid = pid_file.read_text(encoding="utf-8").splitlines()[0]
                environment = Path("/proc") / pid / "environ"
                fake_clock = b"FAKETIME=" in environment.read_bytes()
            except (OSError, IndexError):
                fake_clock = True
        if status.returncode != 0 or fake_clock:
            pg_ctl(context, node, "stop", check=False)
            pg_ctl(context, node, "start")

    defer(context, "以真实主机时钟重启成员 %s" % node, cleanup, priority=250)


def _mmr_node_source_guard(context, definition, options):
    """Restore the one metadata source_node_id changed by a document scenario."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    node_id = options.get("node_id")
    if not isinstance(node_id, int) or node_id <= 0:
        raise ConfigError("mmr_node_source_guard 的 node_id 必须为正整数")
    original = postgres_scalar(
        context, node, database,
        "SELECT source_node_id::text FROM fdd.mmr_node WHERE node_id=%s" % node_id)
    if original is None:
        raise SafetyError("未找到 node_id=%s 的 MMR 节点元数据" % node_id)

    def cleanup():
        postgres_checked(
            context, node, _env(context, "user", "postgres"), database,
            "UPDATE fdd.mmr_node SET source_node_id=%s WHERE node_id=%s" %
            (original, node_id))

    defer(context, "恢复 MMR 节点 %s 的 source_node_id" % node_id, cleanup,
          priority=150)


def _mmr_node_state_guard(context, definition, options):
    """Capture one MMR node state and restore it even when a case aborts."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    node_id = options.get("node_id")
    expected_state = options.get("expected_state", "ACTIVE")
    if not isinstance(node_id, int) or node_id <= 0:
        raise ConfigError("mmr_node_state_guard 的 node_id 必须为正整数")
    if expected_state not in (
            "NONE", "CREATED", "JOIN_START", "DATASYNC", "CATCHUP", "ACTIVE",
            "PART_START", "PARTING", "PART_CATCHUP", "PARTED"):
        raise ConfigError("mmr_node_state_guard 的 expected_state 非法: %s" %
                          expected_state)
    state = postgres_scalar(
        context, node, database,
        "SELECT node_state::text FROM fdd.mmr_node WHERE node_id = %s" % node_id)
    if state != expected_state:
        raise SafetyError(
            "MMR 节点 %s 初始状态为 %s，预期为 %s，拒绝修改元数据" %
            (node_id, state, expected_state))

    def cleanup():
        postgres_checked(
            context, node, _env(context, "user", "postgres"), database,
            "UPDATE fdd.mmr_node SET node_state = %s::fdd.mmr_node_state "
            "WHERE node_id = %s" % (_literal(state), node_id))

    defer(context, "恢复 MMR 节点 %s 的元数据状态" % node_id, cleanup,
          priority=150)


def _mmr_check_node_conf_empty(context, definition, options):
    """Reserve empty per-node check configuration for one MMR case."""
    selectors = options.get("nodes") or []
    if not selectors:
        raise ConfigError("mmr_check_node_conf_empty 必须声明 nodes")
    database = options.get("database", "postgres")
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        count = postgres_scalar(
            context, node, database,
            "SELECT count(*) FROM fdd.mmr_check_node_conf")
        if count != "0":
            raise SafetyError(
                "%s 的 fdd.mmr_check_node_conf 非空，拒绝覆盖已有集群校验配置" % node)

    def cleanup():
        errors = []
        for node in nodes:
            try:
                postgres_checked(
                    context, node, _env(context, "user", "postgres"), database,
                    "TRUNCATE TABLE fdd.mmr_check_node_conf")
            except Exception as exc:
                errors.append("%s: %s" % (node, exc))
        if errors:
            raise OperationError("; ".join(errors))

    defer(context, "清空本用例的 MMR 集群校验配置", cleanup, priority=150)


def _mmr_node_failover_guard(context, definition, options):
    """Preserve one locally stored MMR failover flag through a case."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    node_name = options.get("node_name")
    expected_state = options.get("expected_state", "true")
    if not _IDENTIFIER.match(node_name or ""):
        raise ConfigError("mmr_node_failover_guard 的 node_name 非法: %s" % node_name)
    if expected_state not in ("true", "false"):
        raise ConfigError("mmr_node_failover_guard 的 expected_state 必须为 true 或 false")
    state = postgres_scalar(
        context, node, database,
        "SELECT failover::text FROM fdd.mmr_node WHERE node_name = %s" %
        _literal(node_name))
    if state != expected_state:
        raise SafetyError(
            "MMR 节点 %s 的初始 failover=%s，预期为 %s，拒绝修改" %
            (node_name, state, expected_state))

    def cleanup():
        postgres_checked(
            context, node, _env(context, "user", "postgres"), database,
            "SELECT fdd.alter_node_failover(%s, %s, false)" %
            (_literal(node_name), state))

    defer(context, "恢复 MMR 节点 %s 的 failover" % node_name, cleanup,
          priority=150)


def _mmr_global_failover_guard(context, definition, options):
    """Preserve a target node's failover flag on every MMR member."""
    controller = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    node_name = options.get("node_name")
    selectors = options.get("nodes") or []
    expected_state = options.get("expected_state", "true")
    if not _IDENTIFIER.match(node_name or ""):
        raise ConfigError("mmr_global_failover_guard 的 node_name 非法: %s" % node_name)
    if not selectors:
        raise ConfigError("mmr_global_failover_guard 必须声明 nodes")
    if expected_state not in ("true", "false"):
        raise ConfigError("mmr_global_failover_guard 的 expected_state 必须为 true 或 false")
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        state = postgres_scalar(
            context, node, database,
            "SELECT failover::text FROM fdd.mmr_node WHERE node_name = %s" %
            _literal(node_name))
        if state != expected_state:
            raise SafetyError(
                "%s 中 MMR 节点 %s 的初始 failover=%s，预期为 %s，拒绝修改" %
                (node, node_name, state, expected_state))

    def cleanup():
        postgres_checked(
            context, controller, _env(context, "user", "postgres"), database,
            "SELECT fdd.alter_node_failover(%s, %s, true)" %
            (_literal(node_name), expected_state))

    defer(context, "全局恢复 MMR 节点 %s 的 failover" % node_name, cleanup,
          priority=150)


def _mmr_streaming_parallel_guard(context, definition, options):
    """Reserve an all-parallel MMR streaming topology and restore it by UDF."""
    controller = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    selectors = options.get("nodes") or []
    node_name = options.get("node_name")
    if not _IDENTIFIER.match(node_name or ""):
        raise ConfigError("mmr_parallel_guard 的 node_name 非法: %s" % node_name)
    if not selectors:
        raise ConfigError("mmr_parallel_guard 必须声明 nodes")
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        row = postgres_scalar(
            context, node, database,
            "SELECT count(*)::text || '|' || bool_and(streaming = 'p')::text "
            "FROM fdd.mmr_node")
        expected = "%s|true" % len(nodes)
        if row != expected:
            raise SafetyError(
                "%s 的 MMR streaming 初始状态为 %s，预期为 %s；拒绝覆盖已有配置" %
                (node, row, expected))

    def cleanup():
        postgres_checked(
            context, controller, _env(context, "user", "postgres"), database,
            "SELECT fdd.alter_node_info('streaming', %s, 'parallel', true)" %
            _literal(node_name))
        for node in nodes:
            postgres_checked(
                context, node, _env(context, "user", "postgres"), database,
                "DO $$ DECLARE item record; BEGIN "
                "FOR item IN SELECT subname FROM pg_subscription "
                "WHERE subname LIKE 'fmmr_%%' LOOP "
                "EXECUTE format('ALTER SUBSCRIPTION %I DISABLE', item.subname); "
                "EXECUTE format('ALTER SUBSCRIPTION %I ENABLE', item.subname); "
                "END LOOP; END $$")
        deadline = time.time() + 30
        last = ""
        while time.time() < deadline:
            last = postgres_scalar(
                context, controller, database,
                "SELECT count(*)::text FROM fdd.show_node_info(true,false) "
                "WHERE is_abnormal <> 'OK'")
            if last == "0":
                return
            time.sleep(1)
        raise OperationError("恢复 MMR streaming=parallel 后 30 秒仍未健康，异常数=%s" % last)

    defer(context, "全局恢复 MMR streaming=parallel", cleanup, priority=150)


def _mmr_streaming_mode(context, definition, options):
    """Temporarily set every member to one documented streaming mode."""
    controller = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    node_name = options.get("node_name")
    mode = options.get("mode")
    if not _IDENTIFIER.match(node_name or ""):
        raise ConfigError("mmr_mode 的 node_name 非法: %s" % node_name)
    if mode not in ("off", "on", "parallel"):
        raise ConfigError("mmr_mode 的 mode 必须为 off/on/parallel")
    original = postgres_scalar(
        context, controller, database,
        "SELECT CASE WHEN count(DISTINCT streaming) = 1 THEN "
        "CASE min(streaming) WHEN 'f' THEN 'off' WHEN 't' THEN 'on' "
        "WHEN 'p' THEN 'parallel' END ELSE '' END FROM fdd.mmr_node")
    if original not in ("off", "on", "parallel"):
        raise SafetyError("多活成员 streaming 初始值不一致，拒绝覆盖: %s" % original)

    def cleanup():
        postgres_checked(
            context, controller, _env(context, "user", "postgres"), database,
            "SELECT fdd.alter_node_info('streaming', %s, %s, true)" %
            (_literal(node_name), _literal(original)))

    defer(context, "恢复 MMR streaming=%s" % original, cleanup, priority=150)
    postgres_checked(
        context, controller, _env(context, "user", "postgres"), database,
        "SELECT fdd.alter_node_info('streaming', %s, %s, true)" %
        (_literal(node_name), _literal(mode)))


def _mmr_subscriptions_enabled_guard(context, definition, options):
    """Reserve enabled local MMR subscriptions and restore them via the UDF."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    expected_count = options.get("expected_count", 2)
    if not isinstance(expected_count, int) or expected_count <= 0:
        raise ConfigError("mmr_subscriptions_enabled_guard 的 expected_count 必须为正整数")
    metadata = postgres_scalar(
        context, node, database,
        "SELECT count(*)::text || '|' || bool_and(sub_enabled)::text "
        "FROM fdd.mmr_subscription WHERE sub_name LIKE 'fmmr_%%'")
    runtime = postgres_scalar(
        context, node, database,
        "SELECT count(*)::text || '|' || bool_and(subenabled)::text "
        "FROM pg_subscription WHERE subname LIKE 'fmmr_%%'")
    expected = "%s|true" % expected_count
    if metadata != expected or runtime != expected:
        raise SafetyError(
            "%s 的多活订阅初始状态不全为 enabled（元数据=%s，运行时=%s，预期=%s）" %
            (node, metadata, runtime, expected))

    def cleanup():
        postgres_checked(context, node, _env(context, "user", "postgres"), database,
                         "SELECT fdd.alter_subscription_enable()")

    defer(context, "恢复本节点全部 MMR 订阅为 enabled", cleanup, priority=150)


def _mmr_replication_sets_empty(context, definition, options):
    """Reserve run-scoped replication-set names and drop partial creations."""
    controller = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    names = options.get("names") or []
    if not names or any(not _IDENTIFIER.match(name or "") for name in names):
        raise ConfigError("mmr_replication_sets_empty 必须声明合法 names")
    for name in names:
        count = postgres_scalar(
            context, controller, database,
            "SELECT count(*) FROM fdd.mmr_replication_set WHERE set_name = %s" %
            _literal(name))
        if count != "0":
            raise SafetyError("复制集 %s 已存在，拒绝覆盖" % name)

    def cleanup():
        errors = []
        for name in reversed(names):
            try:
                exists = postgres_scalar(
                    context, controller, database,
                    "SELECT count(*) FROM fdd.mmr_replication_set WHERE set_name = %s"
                    % _literal(name))
                if exists != "0":
                    postgres_checked(
                        context, controller, _env(context, "user", "postgres"), database,
                        "SELECT fdd.drop_replication_set(%s)" % _literal(name))
            except Exception as exc:
                errors.append("%s: %s" % (name, exc))
        if errors:
            raise OperationError("; ".join(errors))

    defer(context, "删除多活复制集测试对象", cleanup, priority=150)


def _mmr_replication_set_table_bindings(context, definition, options):
    """Remove run-scoped table bindings before subscription/set cleanup."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    set_name = options.get("set_name")
    session_tables = [
        "immediate_parallel_conflict",
        "immediate_parallel_target_column",
        "immediate_parallel_source_column",
    ]
    tables = list(dict.fromkeys(session_tables + (options.get("tables") or [])))
    if not _IDENTIFIER.match(set_name or ""):
        raise ConfigError("mmr_replication_set_table_bindings 的 set_name 非法")
    if not tables or any(not _IDENTIFIER.match(name or "") for name in tables):
        raise ConfigError("mmr_replication_set_table_bindings 必须声明合法 tables")

    def cleanup():
        errors = []
        for table in reversed(tables):
            try:
                set_exists = postgres_scalar(
                    context, node, database,
                    "SELECT EXISTS (SELECT 1 FROM fdd.mmr_replication_set "
                    "WHERE set_name=%s)::text" % _literal(set_name))
                table_exists = postgres_scalar(
                    context, node, database,
                    "SELECT (to_regclass('public.%s') IS NOT NULL)::text" % table)
                if set_exists == "true" and table_exists == "true":
                    postgres_checked(
                        context, node, _env(context, "user", "postgres"), database,
                        "SELECT fdd.replication_set_remove_table('public.%s'::regclass,%s,true)"
                        % (_identifier(table), _literal(set_name)))
            except Exception as exc:
                errors.append("%s: %s" % (table, exc))
        if errors:
            raise OperationError("; ".join(errors))

    defer(context, "移除多活复制集测试表绑定", cleanup, priority=250)


def _mmr_sub_repsets_guard(context, definition, options):
    """Preserve a node's replication-set subscription list through the UDF."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    original = postgres_scalar(
        context, node, database,
        "SELECT quote_literal(sub_repsets::text) FROM fdd.mmr_local_node")
    if not original:
        raise SafetyError("%s 的 sub_repsets 为空，拒绝覆盖订阅复制集配置" % node)

    def cleanup():
        postgres_checked(
            context, node, _env(context, "user", "postgres"), database,
            "SELECT fdd.alter_node_replication_sets(%s::text[])" % original)
        postgres_checked(
            context, node, _env(context, "user", "postgres"), database,
            "SELECT fdd.check_and_adjust_sub_repsets(%s::text[])" % original)

    defer(context, "恢复本节点订阅复制集", cleanup, priority=200)


def _mmr_two_phase_disabled(context, definition, options):
    """Require the document's non-two-phase MMR topology before table sync."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    enabled = postgres_scalar(
        context, node, database,
        "SELECT count(*) FROM fdd.mmr_node WHERE two_phase")
    if enabled != "0":
        raise SafetyError(
            "当前多活集群存在 %s 个 two_phase 节点；5.2 测试一的 "
            "replication_set_async_execute 默认复制集路径要求 two_phase=false" % enabled)


def _mmr_async_set_mode_recovery(context, definition, options):
    """Use the product UDF to settle set_mode after a table-operation failure."""
    selectors = options.get("nodes") or []
    if not selectors:
        raise ConfigError("mmr_async_set_mode_recovery 必须声明 nodes")
    database = options.get("database", "postgres")
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)

    def cleanup():
        errors = []
        for node in nodes:
            try:
                postgres_checked(
                    context, node, _env(context, "user", "postgres"), database,
                    "SELECT fdd.check_async_record()")
            except Exception as exc:
                errors.append("%s: %s" % (node, exc))
        if errors:
            raise OperationError("; ".join(errors))

    defer(context, "恢复 MMR 异步复制集状态", cleanup, priority=-100)


def _mmr_remote_sql_tables(context, definition, options):
    """Reserve and remove run-scoped tables used by the remote SQL UDF case."""
    selectors = options.get("nodes") or []
    table_names = options.get("tables") or []
    database = options.get("database", "postgres")
    if not selectors:
        raise ConfigError("mmr_remote_sql_tables 必须声明 nodes")
    if not table_names:
        raise ConfigError("mmr_remote_sql_tables 必须声明 tables")
    tables = [(name, _identifier(name)) for name in table_names]
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        for name, _ in tables:
            exists = postgres_scalar(
                context, node, database,
                "SELECT (to_regclass(%s) IS NOT NULL)::text" %
                _literal("public.%s" % name))
            if exists != "false":
                raise SafetyError(
                    "%s 上测试表 public.%s 已存在，拒绝覆盖" % (node, name))

    def cleanup():
        errors = []
        for node in nodes:
            for name, quoted_name in tables:
                try:
                    postgres_checked(
                        context, node, _env(context, "user", "postgres"), database,
                        "DROP TABLE IF EXISTS public.%s" % quoted_name)
                except Exception as exc:
                    errors.append("%s.%s: %s" % (node, name, exc))
        if errors:
            raise OperationError("; ".join(errors))

    defer(context, "删除远程 SQL 用例测试表", cleanup, priority=100)


def _mmr_global_sequence_probe(context, definition, options):
    """Own one run-scoped global sequence and restore all members on cleanup."""
    controller = context.resolve_node(options.get("node", "primary"))
    selectors = options.get("nodes") or []
    name = options.get("name")
    schema = options.get("schema", "public")
    database = options.get("database", "postgres")
    if not name:
        raise ConfigError("mmr_global_sequence_probe 缺少 name")
    if not selectors:
        raise ConfigError("mmr_global_sequence_probe 必须声明 nodes")
    quoted_name = _identifier(name)
    quoted_schema = schema if schema == "public" else _identifier(schema)
    regclass_name = "%s.%s" % (schema, name)
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        exists = postgres_scalar(
            context, node, database,
            "SELECT (to_regclass(%s) IS NOT NULL)::text" % _literal(regclass_name))
        if exists != "false":
            raise SafetyError(
                "%s 上测试序列 %s 已存在，拒绝覆盖" % (node, regclass_name))

    def cleanup():
        errors = []
        try:
            sequence_exists = [postgres_scalar(
                context, node, database,
                "SELECT (to_regclass(%s) IS NOT NULL)::text" % _literal(regclass_name))
                for node in nodes]
            if all(value == "true" for value in sequence_exists):
                metadata_exists = postgres_scalar(
                    context, controller, database,
                    "SELECT EXISTS (SELECT 1 FROM fdd.mmr_global_sequence "
                    "WHERE seq_name = %s::regclass)::text" % _literal(regclass_name))
                if metadata_exists == "true":
                    postgres_checked(
                        context, controller, _env(context, "user", "postgres"), database,
                        "SELECT fdd.delete_global_seq(%s::regclass, true, true)" %
                        _literal(regclass_name))
            else:
                for node in nodes:
                    postgres_checked(
                        context, node, _env(context, "user", "postgres"), database,
                        "DROP SEQUENCE IF EXISTS %s.%s" % (quoted_schema, quoted_name))
                    postgres_checked(
                        context, node, _env(context, "user", "postgres"), database,
                        "SELECT fdd.clean_invalid_global_seq()")
        except Exception as exc:
            errors.append("删除全局序列元数据: %s" % exc)
        for node in nodes:
            try:
                postgres_checked(
                    context, node, _env(context, "user", "postgres"), database,
                    "DROP SEQUENCE IF EXISTS %s.%s" % (quoted_schema, quoted_name))
            except Exception as exc:
                errors.append("%s.%s: %s" % (node, name, exc))
        if errors:
            raise OperationError("; ".join(errors))

    defer(context, "删除全局序列测试对象 %s" % name, cleanup, priority=150)


def _mmr_global_sequences_empty(context, definition, options):
    """Prevent a document-required all-sequence operation touching user data."""
    selectors = options.get("nodes") or []
    database = options.get("database", "postgres")
    if not selectors:
        raise ConfigError("mmr_global_sequences_empty 必须声明 nodes")
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        count = postgres_scalar(
            context, node, database,
            "SELECT count(*)::text FROM fdd.mmr_global_sequence")
        if count != "0":
            raise SafetyError(
                "%s 存在 %s 条全局序列元数据，拒绝执行 refresh_global_seq(NULL)" %
                (node, count))


def _mmr_schemas_empty(context, definition, options):
    """Reserve disposable schemas on all MMR members and remove them on exit."""
    selectors = options.get("nodes") or []
    schemas = options.get("schemas") or []
    database = options.get("database", "postgres")
    if not selectors or not schemas:
        raise ConfigError("mmr_schemas_empty 必须声明 nodes 和 schemas")
    quoted_schemas = [(schema, _identifier(schema)) for schema in schemas]
    nodes = []
    for selector in selectors:
        node = context.resolve_node(selector)
        if node not in nodes:
            nodes.append(node)
    for node in nodes:
        for schema, _ in quoted_schemas:
            exists = postgres_scalar(
                context, node, database,
                "SELECT (to_regnamespace(%s) IS NOT NULL)::text" % _literal(schema))
            if exists != "false":
                raise SafetyError("%s 上测试 schema %s 已存在，拒绝覆盖" % (node, schema))

    def cleanup():
        errors = []
        for node in nodes:
            for schema, quoted_schema in quoted_schemas:
                try:
                    postgres_checked(
                        context, node, _env(context, "user", "postgres"), database,
                        "DROP SCHEMA IF EXISTS %s CASCADE" % quoted_schema)
                except Exception as exc:
                    errors.append("%s.%s: %s" % (node, schema, exc))
        if errors:
            raise OperationError("; ".join(errors))

    defer(context, "删除多活序列测试 schema", cleanup, priority=100)


def _system_clock(context, definition, options):
    """Save the host clock and restore it if a case advances system time."""
    now = runner_run(context, ["date", "+%s"]).stdout.strip()
    ntp = runner_run(
        context, ["timedatectl", "show", "-p", "NTP", "--value"],
        check=False).stdout.strip()
    if not now.isdigit():
        raise OperationError("无法读取当前系统时间")
    state = {"epoch": now, "ntp": ntp, "changed": False}
    context.values["system_clock"] = state

    def cleanup():
        if not state["changed"]:
            return
        runner_run(context, ["sudo", "-n", "timedatectl", "set-ntp", "false"])
        runner_run(context, ["sudo", "-n", "date", "-s", "@%s" % state["epoch"]])
        if state["ntp"] in ("yes", "true", "1"):
            runner_run(context, ["sudo", "-n", "timedatectl", "set-ntp", "true"])

    defer(context, "恢复系统时间和 NTP 状态", cleanup, priority=200)


def _shared_mmr_conflict_topology(context, definition, options):
    """Create one reusable non-2PC streaming-conflict MMR topology per run."""
    root = Path(options.get("data_dir", TMP_PREFIX + "mmr_conflict_session"))
    source_port = str(options.get("source_port", "15651"))
    target_port = str(options.get("target_port", "15652"))
    if not str(root).startswith(TMP_PREFIX):
        raise ConfigError(
            "shared_mmr_conflict_topology data_dir 必须位于 %s 下" % TMP_PREFIX)
    source, target = root / "node134", root / "node135"
    isolated._remove_stale_cluster_root(context, definition, root)
    isolated._reserve_ports(context, definition, [source_port, target_port])
    mapping = context.values["isolated_mmr_port_mapping"]
    source_port, target_port = mapping[source_port], mapping[target_port]

    def init_node(data_dir, port, debug_mode):
        runner_run(context, [
            binary(context, "initdb"), "-D", str(data_dir), "-U", _env(context, "user", "postgres"),
            "--auth-local=trust", "--auth-host=trust"], timeout=40)
        license_file = Path(_env(context, "license_file", "/home/postgres/license/license.dat"))
        if not license_file.is_file():
            raise SafetyError("缺少共享 MMR 会话 license 文件: %s" % license_file)
        shutil.copy2(str(license_file), str(data_dir / "license.dat"))
        with (data_dir / "postgresql.conf").open("a", encoding="utf-8") as stream:
            stream.write("\nshared_preload_libraries = 'fdd_mmr'\n")
            stream.write("fdd.running_databases = 'postgres'\n")
            stream.write("wal_level = logical\ntrack_commit_timestamp = on\n")
            stream.write("max_worker_processes = 8\nmax_logical_replication_workers = 4\n")
            stream.write("max_replication_slots = 10\nmax_wal_senders = 10\n")
            stream.write("debug_logical_replication_streaming = '%s'\n" % debug_mode)
            stream.write("logical_decoding_work_mem = '64kB'\n")
            stream.write("listen_addresses = '" + _env(context, "local_host", "127.0.0.1") + "'\nport = %s\n" % port)
        isolated._release_port(context, port)
        runner_run(context, [
            binary(context, "pg_ctl"), "-D", str(data_dir), "-l",
            str(data_dir / "start.log"), "-w", "start"], timeout=40)
        session_psql(context, port, "CREATE EXTENSION fdd_mmr")
        session_psql(context, port, "CREATE EXTENSION fb_license")

    try:
        root.mkdir(parents=True)
        init_node(source, source_port, "immediate")
        init_node(target, target_port, "buffered")
        session_psql(context, source_port,
                     "CREATE TABLE public.streaming_join_probe(id int PRIMARY KEY)")
        session_psql(context, target_port,
                     "CREATE TABLE public.streaming_join_probe(id int PRIMARY KEY)")
        session_psql(context, source_port,
                     "SELECT fdd.create_node('node134', "
                     "'host=" + _env(context, "local_host", "127.0.0.1") + " port=%s user=" + _env(context, "user", "postgres") + " dbname=postgres',true,'parallel',false)" % source_port)
        session_psql(context, source_port, "SELECT fdd.create_group('g1')")
        session_psql(context, target_port,
                     "SELECT fdd.create_node('node135', "
                     "'host=" + _env(context, "local_host", "127.0.0.1") + " port=%s user=" + _env(context, "user", "postgres") + " dbname=postgres',true,'parallel',true)" % target_port)
        session_psql(context, target_port,
                     "SELECT fdd.join_group('g1','host=" + _env(context, "local_host", "127.0.0.1") + " port=%s user=" + _env(context, "user", "postgres") + " dbname=postgres',true,'all','table_exist_error')" % source_port,
                     timeout=90)
    except Exception:
        for data_dir in (target, source):
            runner_run(context, [
                binary(context, "pg_ctl"), "-D", str(data_dir),
                "stop", "-m", "immediate"], check=False, timeout=30)
        shutil.rmtree(str(root), ignore_errors=True)
        raise

    def cleanup():
        for data_dir in (target, source):
            runner_run(context, [
                binary(context, "pg_ctl"), "-D", str(data_dir),
                "stop", "-m", "immediate"], check=False, timeout=30)
        shutil.rmtree(str(root), ignore_errors=True)

    defer(context, "删除共享 streaming 冲突隔离集群", cleanup, priority=300)


def _shared_mmr_conflict_reset(context, definition, options):
    """Reset case-owned objects while retaining a reusable MMR topology."""
    source_port = str(options.get("source_port", "15651"))
    target_port = str(options.get("target_port", "15652"))
    set_name = options.get("set_name", "set1")
    tables = options.get("tables") or []
    resolvers = {
        "delete_missing": "skip",
        "delete_recently_updated": "skip",
        "insert_exists": "update_if_newer",
        "update_missing": "skip",
        "update_pkey_exists": "update_if_newer",
    }
    resolvers.update(options.get("resolvers") or {})
    if not _IDENTIFIER.match(set_name):
        raise ConfigError("shared_mmr_conflict_reset set_name 非法: %s" % set_name)
    if any(not _IDENTIFIER.match(table) for table in tables):
        raise ConfigError("shared_mmr_conflict_reset tables 包含非法名称")
    if (not isinstance(resolvers, dict) or
            any(not _IDENTIFIER.match(conflict) or not _IDENTIFIER.match(resolver)
                for conflict, resolver in resolvers.items())):
        raise ConfigError("shared_mmr_conflict_reset resolvers 包含非法名称")
    exists = session_psql(
        context, source_port,
        "SELECT count(*) FROM fdd.mmr_replication_set WHERE set_name=%s" %
        _literal(set_name)).stdout
    if re.search(r"(?m)^\s*1\s*$", exists):
        for port in (source_port, target_port):
            session_psql(context, port, "SELECT fdd.check_async_record()")
        for table in tables:
            session_psql(
                context, source_port,
                "SELECT fdd.replication_set_remove_table(to_regclass(%s),%s,true) "
                "WHERE to_regclass(%s) IS NOT NULL" %
                (_literal("public.%s" % table), _literal(set_name),
                 _literal("public.%s" % table)), timeout=60)
        for port in (source_port, target_port):
            session_psql(context, port, "SELECT fdd.check_async_record()")
    else:
        session_psql(context, source_port,
                     "SELECT fdd.create_replication_set(%s,true,true,true,true,false,false,false)"
                     % _literal(set_name))
    for table in tables:
        for port in (target_port, source_port):
            session_psql(context, port,
                         "DROP TABLE IF EXISTS public.%s" % _identifier(table))
    for conflict, resolver in sorted(resolvers.items()):
        session_psql(
            context, target_port,
            "SELECT fdd.alter_local_node_set_conflict_resolver(%s,%s)" %
            (_literal(conflict), _literal(resolver)))
    for port in (source_port, target_port):
        session_psql(
            context, port,
            "DO $$ DECLARE item record; BEGIN "
            "FOR item IN SELECT subname FROM pg_subscription LOOP "
            "EXECUTE format('ALTER SUBSCRIPTION %I DISABLE',item.subname); "
            "EXECUTE format('ALTER SUBSCRIPTION %I ENABLE',item.subname); "
            "END LOOP; END $$")
    for port in (source_port, target_port):
        session_psql(context, port, "TRUNCATE fdd.mmr_conflict_history")


def _mmr_forwarding_objects(context, definition, options):
    """Own the external publisher and disposable normal-forwarding objects."""
    source = context.resolve_node(options.get("source", "mmr:mmr1"))
    target = context.resolve_node(options.get("target", "mmr:mmr2"))
    table = options.get("table")
    subscription = options.get("subscription")
    all_nodes = bool(options.get("all_nodes", False))
    data_dir = Path(options.get("data_dir", TMP_PREFIX + "mmr_forward"))
    for label, value in (("table", table), ("subscription", subscription)):
        if not _IDENTIFIER.match(value or ""):
            raise ConfigError("mmr_forwarding_objects 的 %s 非法: %s" % (label, value))
    if not str(data_dir).startswith(TMP_PREFIX):
        raise ConfigError("mmr_forwarding_objects data_dir 必须位于 %s 下" % TMP_PREFIX)
    existing = postgres_scalar(
        context, source, "postgres",
        "SELECT count(*)::text FROM fdd.mmr_subscription "
        "WHERE origin_node_id=2 AND target_node_id=1 AND forward_origins IS NOT NULL")
    if existing != "0":
        raise SafetyError("MMR 转发配置已有 %s 条，拒绝覆盖" % existing)

    def cleanup():
        errors = []
        try:
            postgres_checked(
                context, source, _env(context, "user", "postgres"), "postgres",
                "SELECT fdd.alter_forward_subs((SELECT sub_id FROM fdd.mmr_subscription "
                "WHERE origin_node_id=2 AND target_node_id=1), NULL)")
        except Exception as exc:
            errors.append("清空 MMR 普通订阅转发配置: %s" % exc)
        try:
            postgres_checked(
                context, target, _env(context, "user", "postgres"), "postgres",
                "DROP SUBSCRIPTION IF EXISTS %s" % _identifier(subscription))
        except Exception as exc:
            errors.append("删除普通订阅: %s" % exc)
        if all_nodes:
            try:
                postgres_checked(
                    context, source, _env(context, "user", "postgres"), "postgres",
                    "SELECT fdd.run_on_all_nodes(%s)" % _literal(
                        "DROP TABLE IF EXISTS public.%s" % _identifier(table)))
            except Exception as exc:
                errors.append("删除所有 MMR 成员上的测试表: %s" % exc)
        else:
            for node in (source, target):
                try:
                    postgres_checked(
                        context, node, _env(context, "user", "postgres"), "postgres",
                        "DROP TABLE IF EXISTS public.%s" % _identifier(table))
                except Exception as exc:
                    errors.append("删除 %s 上测试表: %s" % (node, exc))
        runner_run(context, [
            binary(context, "pg_ctl"), "-D", str(data_dir),
            "stop", "-m", "immediate"], check=False, timeout=30)
        shutil.rmtree(str(data_dir), ignore_errors=True)
        if errors:
            raise OperationError("; ".join(errors))

    defer(context, "删除普通逻辑复制转发测试对象", cleanup, priority=250)


def _certificate_store_empty(context, definition, options):
    """Reserve an empty certificate store for a cross-role TLCP lifecycle test."""
    node = context.resolve_node(options.get("node", "primary"))
    for table in ("certs_info", "key_meta_data"):
        count = postgres_scalar(
            context, node, "postgres", "SELECT count(*) FROM fdb_mac.%s" % table)
        if count != "0":
            raise SafetyError(
                "TLCP 证书元数据表 fdb_mac.%s 非空，拒绝清理或覆盖已有证书" % table)


def _logical_slot(context, definition, options):
    name = options.get("name")
    if not _IDENTIFIER.match(name or ""):
        raise ConfigError("logical_slot fixture 包含非法槽名: %s" % name)
    node = context.resolve_node(options.get("node", "primary"))

    def cleanup():
        exists = postgres_scalar(
            context, node, "postgres",
            "SELECT count(*) FROM pg_replication_slots WHERE slot_name = %s" %
            _literal(name))
        if exists != "0":
            postgres_checked(
                context, node, _env(context, "user", "postgres"), "postgres",
                "SELECT pg_drop_replication_slot(%s)" % _literal(name))

    defer(context, "删除逻辑复制槽 %s" % name, cleanup, priority=80)


def _logical_replication_objects(context, definition, options):
    """Own a dedicated native logical publication/subscription and its tables."""
    source = context.resolve_node(options.get("source", "primary"))
    target = context.resolve_node(options.get("target", "subscriber"))
    database = options.get("database", "postgres")
    values = {
        "source_table": options.get("source_table"),
        "target_table": options.get("target_table", options.get("source_table")),
        "publication": options.get("publication"),
        "subscription": options.get("subscription"),
    }
    for label, value in values.items():
        if not _IDENTIFIER.match(value or ""):
            raise ConfigError("logical_replication_objects 的 %s 非法: %s" %
                              (label, value))
    source_ref = "public.%s" % _identifier(values["source_table"])
    target_ref = "public.%s" % _identifier(values["target_table"])

    def cleanup():
        postgres_checked(
            context, target, _env(context, "user", "postgres"), database,
            "DROP SUBSCRIPTION IF EXISTS %s" % _identifier(values["subscription"]))
        postgres_checked(
            context, source, _env(context, "user", "postgres"), database,
            "DROP PUBLICATION IF EXISTS %s" % _identifier(values["publication"]))
        postgres_checked(
            context, target, _env(context, "user", "postgres"), database,
            "DROP TABLE IF EXISTS %s" % target_ref)
        postgres_checked(
            context, source, _env(context, "user", "postgres"), database,
            "DROP TABLE IF EXISTS %s" % source_ref)

    defer(context, "删除专属普通逻辑复制对象", cleanup, priority=200)


def _failover_delay_objects(context, definition, options):
    """Remove the dedicated publication, subscription, slot, and tables."""
    database = options.get("database", "postgres")
    source_table = options.get("source_table")
    target_table = options.get("target_table", source_table)
    publication = options.get("publication")
    subscription = options.get("subscription")
    slot = options.get("slot")
    for label, value in (
            ("source_table", source_table), ("target_table", target_table),
            ("publication", publication), ("subscription", subscription),
            ("slot", slot)):
        if not _IDENTIFIER.match(value or ""):
            raise ConfigError("failover_delay_objects 的 %s 非法: %s" %
                              (label, value))

    primary = context.resolve_node(options.get("primary", "primary"))
    subscriber = context.resolve_node(options.get("subscriber", "subscriber"))
    source_ref = "public.%s" % _identifier(source_table)
    target_ref = "public.%s" % _identifier(target_table)

    def cleanup():
        postgres_checked(
            context, subscriber, _env(context, "user", "postgres"), database,
            "DROP SUBSCRIPTION IF EXISTS %s" % _identifier(subscription))
        exists = postgres_scalar(
            context, primary, database,
            "SELECT count(*) FROM pg_replication_slots WHERE slot_name = %s" %
            _literal(slot), user=_env(context, "user", "postgres"))
        if exists != "0":
            postgres_checked(
                context, primary, _env(context, "user", "postgres"), database,
                "SELECT pg_drop_replication_slot(%s)" % _literal(slot))
        postgres_checked(
            context, primary, _env(context, "user", "postgres"), database,
            "DROP PUBLICATION IF EXISTS %s" % _identifier(publication))
        postgres_checked(
            context, subscriber, _env(context, "user", "postgres"), database,
            "DROP TABLE IF EXISTS %s" % target_ref)
        postgres_checked(
            context, primary, _env(context, "user", "postgres"), database,
            "DROP TABLE IF EXISTS %s" % source_ref)

    defer(context, "删除故障转移槽延迟提交测试对象", cleanup, priority=200)


def _hba_password_auth(context, definition, options):
    """Require password authentication for one disposable local role only."""
    role = options.get("role")
    if not _IDENTIFIER.match(role or ""):
        raise ConfigError("hba_password_auth fixture 包含非法角色名: %s" % role)
    node = context.resolve_node(options.get("node", "primary"))
    endpoint = context.node_endpoint(node)
    if not context.is_local(endpoint.get("host")):
        raise ConfigError("hba_password_auth 目前只支持本地节点")
    path = Path(str(endpoint["data_dir"])) / "pg_hba.conf"
    original = path.read_text(encoding="utf-8")
    rule = ("# fbase_regress temporary password-auth rule for %s\n"
            "host all %s " + _env(context, "local_host", "127.0.0.1") + "/32 password\n"
            "host all %s ::1/128 password\n") % (role, role, role)

    def reload_hba():
        postgres_checked(context, node, _env(context, "user", "postgres"), "postgres",
                         "SELECT pg_reload_conf()")

    def cleanup():
        path.write_text(original, encoding="utf-8")
        reload_hba()

    defer(context, "恢复 %s 的 pg_hba.conf" % role, cleanup, priority=90)
    path.write_text(rule + original, encoding="utf-8")
    reload_hba()


def _topology(context, definition, options):
    groups = options.get("groups") or []
    missing = [name for name in groups if name not in _groups(context)]
    if missing:
        raise ConfigError("cluster 缺少关系组: %s" % ",".join(missing))


def _roles(context, definition, options):
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    setup = options.get("setup", True)
    cleanup_priority = options.get("cleanup_priority", 0)
    for role in options.get("create", []):
        if isinstance(role, str):
            role = {"name": role}
        name = role["name"]
        attributes = role.get("attributes", "LOGIN")
        password = role.get("password")
        login_user = role.get("login", False)

        def cleanup(role_name=name, node_name=node, db=database):
            postgres_checked(
                context, node_name, _env(context, "user", "postgres"), db,
                "DROP ROLE IF EXISTS %s" % _identifier(role_name))
        defer(context, "删除角色 %s" % name, cleanup, priority=cleanup_priority)
        if setup:
            if login_user and not attributes and password is None:
                sql = "CREATE USER %s" % _identifier(name)
            else:
                sql = "CREATE ROLE %s %s" % (_identifier(name), attributes)
            if password is not None:
                sql += " PASSWORD %s" % _literal(password)
            postgres_checked(context, node, _env(context, "user", "postgres"), database, sql)


def _database(context, definition, options):
    name = options.get("name")
    if not name:
        return
    node = context.resolve_node(options.get("node", "primary"))
    if options.get("setup", True):
        sql = "CREATE DATABASE %s" % _identifier(name)
        if options.get("template"):
            sql += " TEMPLATE %s" % _identifier(options["template"])
        if options.get("encoding"):
            sql += " ENCODING %s" % _literal(options["encoding"])
        if options.get("locale"):
            sql += " LOCALE %s" % _literal(options["locale"])
        postgres_checked(context, node, _env(context, "user", "postgres"), "postgres", sql)

    def cleanup():
        postgres_checked(
            context, node, _env(context, "user", "postgres"), "postgres",
            "DROP DATABASE IF EXISTS %s WITH (FORCE)" % _identifier(name))
    defer(context, "删除数据库 %s" % name, cleanup)


def _table(context, definition, options):
    """Create or only clean up a disposable table."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    schema = options.get("schema", "public")
    name = options.get("name")
    if not name:
        raise ConfigError("table fixture 缺少 name")
    table_ref = "%s.%s" % (_identifier(schema), _identifier(name))

    def cleanup():
        postgres_checked(
            context, node, _env(context, "user", "postgres"), database,
            "DROP TABLE IF EXISTS %s" % table_ref)
    defer(context, "删除测试表 %s" % name, cleanup,
          priority=options.get("cleanup_priority", 0))

    if options.get("setup", True):
        columns = options.get("columns", "id integer PRIMARY KEY")
        postgres_checked(
            context, node, _env(context, "user", "postgres"), database,
            "CREATE TABLE %s (%s)" % (table_ref, columns))


def _sequence(context, definition, options):
    """Create or only clean up a disposable PostgreSQL sequence."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    schema = options.get("schema", "public")
    name = options.get("name")
    if not name:
        raise ConfigError("sequence fixture 缺少 name")
    sequence_ref = "%s.%s" % (_identifier(schema), _identifier(name))

    def cleanup():
        postgres_checked(
            context, node, _env(context, "user", "postgres"), database,
            "DROP SEQUENCE IF EXISTS %s" % sequence_ref)
    defer(context, "删除测试序列 %s" % name, cleanup,
          priority=options.get("cleanup_priority", 0))

    if options.get("setup", True):
        postgres_checked(
            context, node, _env(context, "user", "postgres"), database,
            "CREATE SEQUENCE %s" % sequence_ref)


def _table_grants(context, definition, options):
    """Grant disposable table privileges required as a prior test state."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    schema = options.get("schema", "public")
    table = options.get("table")
    if not table:
        raise ConfigError("table_grants fixture 缺少 table")
    table_ref = "%s.%s" % (_identifier(schema), _identifier(table))
    for role, privileges in (options.get("grants") or {}).items():
        postgres_checked(
            context, node, _env(context, "user", "postgres"), database,
            "GRANT %s ON %s TO %s" % (privileges, table_ref, _identifier(role)))


def _audit_rules(context, definition, options):
    """Remove named audit rules created by a case, including partial runs."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    for rule in options.get("rules", []):
        owner = rule["owner"]
        name = rule["name"]
        kind = rule["kind"]

        def cleanup(rule_owner=owner, rule_name=name, rule_kind=kind):
            source = ("fdb_audit.audit_rule" if rule_owner == "sao"
                      else "fdb_audit.audit_rules")
            count = postgres_scalar(
                context, node, database,
                "SELECT count(*) FROM %s WHERE rule_name = %s" %
                (source, _literal(rule_name)), user=rule_owner)
            if count != "0":
                function = ("cancel_audit_stmt" if rule_kind == "stmt"
                            else "cancel_audit_object")
                postgres_checked(
                    context, node, rule_owner, database,
                    "SELECT fdb_audit.%s(%s)" % (function, _literal(rule_name)))
        defer(context, "删除审计规则 %s" % name, cleanup)


def _mac_policy(context, definition, options):
    """Manage cleanup for a disposable fbase_mac policy and table."""
    node = context.resolve_node(options.get("node", "primary"))
    database = options.get("database", "postgres")
    schema = options.get("schema", "public")
    table = options.get("table", "fbase_regress_mac_guard")
    policy = options.get("policy", "fbase_regress_mac_policy")
    column = options.get("column", "fbase_regress_mac_label")
    setup = options.get("setup", True)
    create_table = options.get("create_table", True)
    apply_policy = options.get("apply", True)
    levels = options.get("levels", [("L1", 10)])
    compartments = options.get("compartments", [("C1", 10)])
    labels = options.get("labels", [("L1:C1", 11)])
    table_columns = options.get("table_columns",
                                "id integer PRIMARY KEY, payload text")
    table_ref = "%s.%s" % (_identifier(schema), _identifier(table))

    def drop_table():
        postgres_checked(
            context, node, _env(context, "user", "postgres"), database,
            "DROP TABLE IF EXISTS %s" % table_ref)

    def drop_policy():
        count = postgres_scalar(
            context, node, database,
            "SELECT count(*) FROM fdb_mac.policy WHERE policy_name = %s" %
            _literal(policy), user="sso")
        if count != "0":
            postgres_checked(
                context, node, "sso", database,
                "SELECT fdb_mac.drop_policy(%s, true)" % _literal(policy))

    def remove_table_policy():
        count = postgres_scalar(
            context, node, database,
            "SELECT count(*) FROM fdb_mac.table_policy t "
            "JOIN pg_class c ON c.oid = t.relid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = %s" % (
                _literal(schema), _literal(table)), user="sso")
        if count != "0":
            postgres_checked(
                context, node, "sso", database,
                "SELECT fdb_mac.remove_table_policy(%s, %s, %s, true)" % (
                    _literal(policy), _literal(schema), _literal(table)))

    defer(context, "删除 MAC 测试表 %s" % table, drop_table)
    defer(context, "删除 MAC 测试策略 %s" % policy, drop_policy)
    defer(context, "移除 MAC 测试表策略 %s" % policy, remove_table_policy)

    if not setup:
        return
    if create_table:
        postgres_checked(
            context, node, _env(context, "user", "postgres"), database,
            "CREATE TABLE %s (%s)" % (table_ref, table_columns))
    postgres_checked(
        context, node, "sso", database,
        "SELECT fdb_mac.create_policy(%s, %s)" % (_literal(policy), _literal(column)))
    for name, identifier in levels:
        postgres_checked(
            context, node, "sso", database,
            "SELECT fdb_mac.create_level(%s, %s, %s)" %
            (_literal(policy), _literal(name), int(identifier)))
    for name, identifier in compartments:
        postgres_checked(
            context, node, "sso", database,
            "SELECT fdb_mac.create_compartment(%s, %s, %s)" %
            (_literal(policy), _literal(name), int(identifier)))
    for label, identifier in labels:
        postgres_checked(
            context, node, "sso", database,
            "SELECT fdb_mac.create_label(%s, %s, %s)" %
            (_literal(policy), _literal(label), int(identifier)))
    if apply_policy:
        if not create_table:
            raise ConfigError("mac_policy apply=true 时必须创建测试表")
        postgres_checked(
            context, node, "sso", database,
            "SELECT fdb_mac.apply_table_policy(%s, %s, %s, false)" % (
                _literal(policy), _literal(schema), _literal(table)))


def _settings(context, definition, options):
    selectors = options.get("nodes") or [options.get("node", "primary")]
    values = options.get("values") or {}
    action = options.get("apply", "reload")
    user = options.get("user") or _env(context, "user", "postgres")
    setup = options.get("setup", True)
    restore_runtime_value = options.get("restore_runtime_value", False)
    if action not in ("reload", "restart"):
        raise ConfigError("settings fixture apply 只支持 reload 或 restart")
    saved = []

    def cleanup():
        for node, name, old, auto_value in saved:
            sql = ("ALTER SYSTEM RESET %s" % name if auto_value is None else
                   "ALTER SYSTEM SET %s = %s" % (name, _literal(auto_value)))
            postgres_checked(context, node, user, "postgres", sql)
        if saved:
            cluster_apply(context, action)

    cleanup_registered = False
    for selector in selectors:
        node = context.resolve_node(selector)
        for name, value in values.items():
            if not _GUC_NAME.match(name):
                raise ConfigError("非法 PostgreSQL 配置名: %s" % name)
            old = postgres_scalar(
                context, node, "postgres",
                "SELECT CASE WHEN current_setting(%s) = '' THEN "
                "'__FBASE_REGRESS_EMPTY_SETTING__' ELSE current_setting(%s) END" %
                (_literal(name), _literal(name)), user=user)
            if old == "__FBASE_REGRESS_EMPTY_SETTING__":
                old = ""
            auto_result = postgres_execute(
                context, node, _env(context, "user", "postgres"), "postgres",
                "SELECT setting FROM pg_file_settings "
                "WHERE name = %s AND sourcefile LIKE '%%/postgresql.auto.conf' "
                "ORDER BY seqno DESC LIMIT 1" % _literal(name), structured=True)
            if auto_result.returncode != 0:
                raise OperationError(auto_result.output.strip() or
                                     "无法读取 ALTER SYSTEM 原配置")
            auto_value = auto_result.rows[0][0] if auto_result.rows else None
            if restore_runtime_value:
                auto_value = old
            saved.append((node, name, old, auto_value))
            if not cleanup_registered:
                defer(context, "恢复 PostgreSQL 配置", cleanup, priority=100)
                cleanup_registered = True
            if setup:
                postgres_checked(
                    context, node, user, "postgres",
                    "ALTER SYSTEM SET %s = %s" % (name, _literal(value)))
    if saved and setup:
        cluster_apply(context, action)
        details = context.values.setdefault("postgresql_settings", [])
        purpose = options.get("purpose", "用例临时 PostgreSQL 配置")
        for node, name, old, _ in saved:
            if setup:
                detail = check_setting(context, node, {
                    "source": "fixture", "name": name,
                    "equals": str(values[name]), "purpose": purpose,
                })
                detail["previous"] = old
                details.append(detail)
                if not detail["matched"]:
                    raise OperationError(
                        "PostgreSQL 参数 %s 未生效（实际=%s，要求=%s）" %
                        (name, detail["actual"], detail["requirement"]))


def _isolated_fixture(context, definition, name, options):
    isolated.setup_fixture(context, definition, name, options)


FIXTURES = {
    "cluster": _cluster,
    "node_running_guard": _node_running_guard,
    "faketime_node_guard": _faketime_node_guard,
    "mmr_node_source_guard": _mmr_node_source_guard,
    "mmr_node_state_guard": _mmr_node_state_guard,
    "mmr_check_node_conf_empty": _mmr_check_node_conf_empty,
    "mmr_node_failover_guard": _mmr_node_failover_guard,
    "mmr_global_failover_guard": _mmr_global_failover_guard,
    "mmr_streaming_parallel_guard": _mmr_streaming_parallel_guard,
    "mmr_streaming_mode": _mmr_streaming_mode,
    "mmr_subscriptions_enabled_guard": _mmr_subscriptions_enabled_guard,
    "mmr_replication_sets_empty": _mmr_replication_sets_empty,
    "mmr_replication_set_table_bindings": _mmr_replication_set_table_bindings,
    "mmr_sub_repsets_guard": _mmr_sub_repsets_guard,
    "mmr_two_phase_disabled": _mmr_two_phase_disabled,
    "mmr_async_set_mode_recovery": _mmr_async_set_mode_recovery,
    "mmr_remote_sql_tables": _mmr_remote_sql_tables,
    "mmr_global_sequence_probe": _mmr_global_sequence_probe,
    "mmr_global_sequences_empty": _mmr_global_sequences_empty,
    "mmr_schemas_empty": _mmr_schemas_empty,
    "system_clock": _system_clock,
    "shared_mmr_conflict_topology": _shared_mmr_conflict_topology,
    "shared_mmr_conflict_reset": _shared_mmr_conflict_reset,
    "mmr_forwarding_objects": _mmr_forwarding_objects,
    "certificate_store_empty": _certificate_store_empty,
    "logical_slot": _logical_slot,
    "logical_replication_objects": _logical_replication_objects,
    "failover_delay_objects": _failover_delay_objects,
    "hba_password_auth": _hba_password_auth,
    "topology": _topology,
    "roles": _roles,
    "database": _database,
    "table": _table,
    "sequence": _sequence,
    "table_grants": _table_grants,
    "audit_rules": _audit_rules,
    "mac_policy": _mac_policy,
    "settings": _settings,
}

SUPPORTED_FIXTURES = frozenset(set(FIXTURES) | set(isolated.SUPPORTED_FIXTURES))


def setup_fixture(context, definition, spec):
    """Expand and apply one fixture spec exactly like the legacy registry."""
    expanded = context.expand(spec)
    options = {} if isinstance(expanded, str) else dict(expanded)
    name = expanded if isinstance(expanded, str) else options.pop("type", None)
    if name in isolated.SUPPORTED_FIXTURES:
        isolated.setup_fixture(context, definition, name, options)
        return
    callback = FIXTURES.get(name)
    if not callback:
        raise ConfigError("未知 fixture: %s" % name)
    callback(context, definition, options)
