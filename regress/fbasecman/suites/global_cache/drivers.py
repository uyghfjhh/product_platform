"""Driver assets and execution for global-cache scenarios."""

import os
import re
import shutil

from framework.execution.phased_process import PhasedProcess
from framework.execution.shell import quote_arguments
from framework.evidence.jdbc import (
    jdbc_api_calls, jdbc_prepared_operations, without_phase_markers,
)
from framework.reporting import render_psql_table_from_pipe_text
from framework.execution.phased_process import PhaseAction
from suites.global_cache.paths import asset_path


class DriverError(RuntimeError):
    pass


# These drivers keep a real JDBC connection alive at the point where the
# cache/backend state is meaningful.  The Python side observes that state and
# only then releases the driver through stdin.
PHASED_JDBC_ACTIONS = {
    "basic_reuse": (
        PhaseAction(
            "after_first", "PHASE=AFTER_FIRST", title="首次 PreparedStatement 后观察 global cache",
            expected="首次查询完成且 JDBC 连接保持；console 能看到目标 global cache entry。",
        ),
    ),
    "cross_client_reuse": (
        PhaseAction(
            "after_client1", "PHASE=AFTER_CLIENT1", title="第一个 JDBC 客户端释放后观察共享 entry",
            expected="第一个客户端已关闭，第二个客户端尚未建立；共享 entry 状态可被 console 观察。",
        ),
    ),
    "parse_invalid_error_recovery_same_connection": (
        PhaseAction(
            "after_first_failure", "PHASE=AFTER_FIRST_FAILURE", title="首次 Parse 失败后观察连接和缓存",
            expected="首次 PreparedStatement 因表不存在失败，但同一 JDBC 连接仍保持。",
        ),
        PhaseAction(
            "after_create", "PHASE=AFTER_CREATE", title="补建表后、恢复执行前观察缓存",
            expected="补建表并授权完成，同一 PreparedStatement 尚未再次执行。",
        ),
        PhaseAction(
            "after_recovery", "PHASE=AFTER_RECOVERY", title="同一 PreparedStatement 恢复成功后观察缓存",
            expected="同一连接再次执行成功，恢复后的 SQL 已进入 global cache。",
        ),
    ),
    "heartbeat_reload_reclassifies_existing_normal_entry": (
        PhaseAction(
            "after_initial_execute", "PHASE=AFTER_EXECUTE",
            title="初始 prepared SQL 执行后观察 NORMAL entry",
            expected="初始 heartbeat 配置尚不匹配 SELECT 124；连接保持且 console 中条目为 NORMAL。",
        ),
    ),
}


def _phase_key_fbasecman_lines(text, sql_hint, limit=32):
    keywords = (
        "global_ps_cache", "prepared statement cache", "parse deploy",
        "rewrite", "server_ps", "heartbeat", "bypass", "guc)",
        "discard all", "errorresponse", "error ",
    )
    sql_fragment = (sql_hint or "").split("/*", 1)[0].strip().lower()
    selected = []
    for line in text.splitlines():
        lower = line.lower()
        if any(keyword in lower for keyword in keywords) or (
            sql_fragment and sql_fragment in lower
        ):
            selected.append(line)
    if not selected:
        return "<无与本阶段 SQL/cache 直接相关的 fbasecman 日志；完整日志保存在 logs/>"
    if len(selected) > limit:
        selected = selected[:limit] + ["... 其余关键日志保存在 fbasecman.log"]
    return "\n".join(selected)


def _phase_key_pg_lines(text, sql_hint, limit=20):
    sql_fragment = (sql_hint or "").split("/*", 1)[0].strip().lower()
    selected = []
    for line in text.splitlines():
        lower = line.lower()
        if "===== log:" in lower:
            continue
        if "error:" in lower or (
            "statement:" in lower and (not sql_fragment or sql_fragment in lower)
        ):
            selected.append(line)
    if not selected:
        return "<无与本阶段 SQL 直接相关的 PostgreSQL 新增日志；完整日志保存在 logs/>"
    if len(selected) > limit:
        selected = selected[:limit] + ["... 其余相关 PostgreSQL 日志保存在 logs/>"]
    return "\n".join(selected)


LIBPQ_ASSETS = {
    "unnamed_statement_overwrite_unref": "GC_unnamed_statement_overwrite_unref.c",
    "named_conflict_after_global_hit_keeps_old_entry": "GC_named_conflict_after_global_hit_keeps_old_entry.c",
    "prepare_before_bind_deploy": "GC_prepare_before_bind_deploy.c",
    "close_and_disconnect_unref": "GC_close_and_disconnect_unref.c",
    "bypass_prepare_protocol_sequence": "GC_bypass_prepare_protocol_sequence.c",
    "backend_global_split_eviction": "GC_backend_global_split_eviction.c",
    "shared_global_entry_disconnect_one_client_other_client_reuse_still_ok": "GC_shared_global_entry_disconnect_one_client_other_client_reuse_still_ok.c",
}


def driver_api_calls(source):
    """Extract actual client API calls from an external Java or libpq driver."""
    text = source.read_text(encoding="utf-8", errors="replace")
    if source.suffix == ".c":
        calls = re.findall(r"\b(PQ[A-Za-z0-9_]+)\s*\(", text)
    elif source.suffix == ".java":
        calls = [call[:-2] for call in jdbc_api_calls(source)]
    else:
        calls = []
    ordered = []
    for call in calls:
        rendered = call + "()"
        if rendered not in ordered:
            ordered.append(rendered)
    return ordered


def libpq_prepared_operations(source, phase=None):
    """Extract concrete SQL/parameter operations from the checked-in C driver."""
    if source.suffix != ".c":
        return []
    text = source.read_text(encoding="utf-8", errors="replace")
    operations = []
    pattern = re.compile(
        r'prepare_and_exec\s*\(\s*conn\s*,\s*"[^"]+"\s*,\s*"([^"]+)"'
        r'\s*,\s*(-?\d+)\s*,\s*"([^"]+)"\s*\)'
    )
    for sql, value, tag in pattern.findall(text):
        if phase and not tag.startswith(phase + "_"):
            continue
        operations.append({"sql": sql, "parameters": ["$1=%s" % value], "tag": tag})
    return operations


def record_driver_api_calls(rt, source):
    calls = driver_api_calls(source)
    # execute_jdbc() already records source-backed APIs, SQL, parameters and
    # real output in the step journal.  Do not attach them to the preceding
    # compilation record merely because it is the last legacy step.
    if getattr(rt, "step_journal", None) is not None and rt.step_journal.steps:
        return calls
    if calls and rt.step_records:
        rt.step_records[-1]["note"] = "driver 核心调用: %s" % " -> ".join(calls)
        if source.suffix == ".java":
            operations = jdbc_prepared_operations(source)
            if operations:
                rt.step_records[-1]["sql_operations"] = operations
    return calls


def libpq_source(root, case):
    try:
        filename = LIBPQ_ASSETS[case.name]
    except KeyError:
        raise DriverError("no libpq asset mapped for %s" % case.name)
    source = asset_path(root, "libpq", filename)
    if not source.exists():
        raise DriverError("missing libpq source: %s" % source)
    return source


def stage_libpq_source(source, target_dir):
    text = source.read_text(encoding="utf-8")
    target = target_dir / source.name
    target.write_text(text, encoding="utf-8")
    for include_name in re.findall(r'^\s*#include\s+"([^"]+)"', text, flags=re.MULTILINE):
        include_source = source.parent / include_name
        if not include_source.exists():
            raise DriverError("missing libpq include source: %s" % include_source)
        include_target = target_dir / include_name
        if not include_target.exists():
            stage_libpq_source(include_source, target_dir)
    return target


def build_libpq_asset(rt, logfile=None):
    source = libpq_source(rt.root, rt.case)
    target = stage_libpq_source(source, rt.driver_dir)
    binary = rt.build_dir / source.stem
    postgres_dir = rt.env.config["local"]["postgres_dir"]
    rt.run_command(
        ["gcc", "-std=c99", "-o", str(binary), str(target),
         "-I%s/include" % postgres_dir, "-L%s/lib" % postgres_dir, "-lpq"],
        logfile or (rt.logs_dir / "gcc.log"), cwd=rt.driver_dir,
        step_title="编译 libpq driver",
    )
    return binary


def libpq_run_spec(rt, binary, extra_args=None, include_rw_method=True):
    postgres_dir = rt.env.config["local"]["postgres_dir"]
    run_env = os.environ.copy()
    run_env["LD_LIBRARY_PATH"] = "%s/lib:%s" % (
        postgres_dir, run_env.get("LD_LIBRARY_PATH", "")
    )
    conninfo = "host=127.0.0.1 port=%s user=postgres dbname=postgres sslmode=disable" % rt.listen_port
    command = [str(binary), conninfo]
    if include_rw_method:
        command.append("mmr_hint")
    command.extend(extra_args or [])
    return command, run_env


def start_phased_libpq(rt, log_stem, extra_args=None, include_rw_method=True,
                       compile_log=None, binary=None):
    binary = binary or build_libpq_asset(rt, compile_log)
    command, run_env = libpq_run_spec(
        rt, binary, extra_args=extra_args, include_rw_method=include_rw_method
    )
    logfile = rt.logs_dir / ("%s.log" % log_stem)
    return PhasedProcess(command, logfile, cwd=rt.build_dir, env=run_env), command, logfile


def run_libpq_asset(rt, log_stem, extra_args=None, include_rw_method=True,
                    compile_log=None, binary=None, step_title="执行外置 libpq driver"):
    source = libpq_source(rt.root, rt.case)
    binary = binary or build_libpq_asset(rt, compile_log)
    command, run_env = libpq_run_spec(
        rt, binary, extra_args=extra_args, include_rw_method=include_rw_method
    )
    logfile = rt.logs_dir / ("%s.log" % log_stem)
    rc, output = rt.run_command(
        command, logfile, cwd=rt.build_dir, env=run_env, step_title=step_title
    )
    existing = rt.libpq_log.read_text(encoding="utf-8", errors="replace") if rt.libpq_log.exists() else ""
    if output:
        rt.libpq_log.write_text(existing + output, encoding="utf-8")
    record_driver_api_calls(rt, source)
    return rc, output


def normalize_prepared_sequence(operations):
    """Validate and normalize operations for the shared JDBC sequence driver."""
    normalized = []
    for operation in operations:
        if len(operation) != 3:
            raise DriverError("prepared sequence operation must have output key, mode and SQL")
        output_key, mode, sql = operation
        if not output_key or not isinstance(output_key, str):
            raise DriverError("prepared sequence output key must be a non-empty string")
        if mode not in ("statement", "execute", "query") and not (
            isinstance(mode, str) and (
                mode.startswith("query_int:")
                or mode.startswith("execute_int:")
                or mode.startswith("query_columns:")
            )
        ):
            raise DriverError("unsupported prepared sequence mode: %s" % mode)
        if isinstance(mode, str) and mode.startswith("query_columns:"):
            column_keys = [key.strip() for key in mode.split(":", 1)[1].split(",") if key.strip()]
            if not column_keys:
                raise DriverError("query_columns mode requires at least one output key")
        if not sql or not isinstance(sql, str):
            raise DriverError("prepared sequence SQL must be a non-empty string")
        normalized.append({"output_key": output_key, "mode": mode, "sql": sql})
    return normalized


def normalize_phased_prepared_operations(operations):
    normalized = []
    for operation in operations:
        if len(operation) != 3:
            raise DriverError("phased prepared operation must have output key, SQL and integer value")
        output_key, sql, value = operation
        if not output_key or not isinstance(output_key, str):
            raise DriverError("phased prepared output key must be a non-empty string")
        if not sql or not isinstance(sql, str):
            raise DriverError("phased prepared SQL must be a non-empty string")
        try:
            bind_value = int(value)
        except (TypeError, ValueError):
            raise DriverError("phased prepared bind value must be an integer: %r" % (value,))
        normalized.append({"output_key": output_key, "sql": sql, "bind_value": bind_value})
    if not normalized:
        raise DriverError("phased prepared driver requires at least one operation")
    return normalized


def jdbc_jar(rt, version=None):
    jar = rt.root / rt.env.config["local"]["jdbc_lib_dir"] / (
        "postgresql-%s.jar" % (version or rt.case.jdbc.get("version", "42.7.7"))
    )
    if not jar.exists():
        raise DriverError("missing jdbc jar: %s" % jar)
    return jar


def jdbc_url(rt, options=None):
    options = options or {}
    params = []
    for key, value in options.items():
        if value is not None:
            params.append("%s=%s" % (key, value))
    return "jdbc:postgresql://localhost:%s/postgres?%s" % (rt.listen_port, "&".join(params))


def jdbc_source_file(root, case, safe_name):
    path = asset_path(root, "jdbc", "GC_%s.java" % safe_name(case.name))
    if not path.exists():
        raise DriverError("missing jdbc driver source: %s" % path)
    return path


def compile_java(rt, source, logfile, step_title):
    jar = jdbc_jar(rt)
    rt.run_command(
        ["javac", "-cp", str(jar), str(source)],
        logfile,
        cwd=rt.driver_dir,
        step_title=step_title,
    )
    return jar


def run_java(rt, class_name, jar, logfile, user="postgres", password="", options=None,
             allow_failure=False, step_title="执行 JDBC driver", source=None):
    url = jdbc_url(rt, options)
    command = [
        "java", "-cp", "%s:%s" % (rt.driver_dir, jar), class_name,
        url, user, password,
    ]
    if source is not None:
        rc, output = rt.execute_jdbc(
            command, source, logfile, step_title, url, cwd=rt.driver_dir,
            check=not allow_failure,
        )
    else:
        rc, output = rt.run_command(
            command, logfile, cwd=rt.driver_dir, check=not allow_failure, step_title=step_title
        )
    if allow_failure and rc == 0:
        raise DriverError("%s expects JDBC execution to fail, but rc=0" % rt.case.name)
    return rc, output


def run_jdbc_asset(rt, source_name, class_name, arguments=None, user="postgres",
                   password="", options=None, allow_failure=False,
                   step_title="执行外置 JDBC driver"):
    source = asset_path(rt.root, "jdbc", source_name)
    if not source.exists():
        raise DriverError("missing jdbc driver source: %s" % source)
    target = rt.driver_dir / source.name
    shutil.copyfile(str(source), str(target))
    jar = compile_java(
        rt, target, rt.logs_dir / ("%s.javac.log" % class_name),
        "编译外置 JDBC driver",
    )
    command = [
        "java", "-cp", "%s:%s" % (rt.driver_dir, jar), class_name,
        jdbc_url(rt, options or {"prepareThreshold": 1, "preferQueryMode": "extended"}),
        user, password,
    ] + list(arguments or [])
    url = command[3]
    dynamic_operations = [
        {"sql": argument, "parameters": []}
        for argument in (arguments or [])
        if isinstance(argument, str) and argument.strip().lower().startswith(
            ("select ", "insert ", "update ", "delete ", "set ", "discard ")
        )
    ]
    rc, output = rt.execute_jdbc(
        command, source, rt.logs_dir / ("%s.java.log" % class_name), step_title, url,
        cwd=rt.driver_dir, check=not allow_failure, sql_operations=dynamic_operations or None,
    )
    record_driver_api_calls(rt, source)
    if allow_failure and rc == 0:
        raise DriverError("%s expects JDBC execution to fail, but rc=0" % rt.case.name)
    return rc, output


PHASE_LOG_EVIDENCE = {
    "parse_invalid_error_recovery_same_connection": {
        "after_first_failure": "proxy",
    },
}


def run_jdbc_asset_phased(rt, source_name, class_name, arguments=None, user="postgres",
                          password="", options=None, actions=None,
                          sql_operations=None, step_title="执行阶段 JDBC driver"):
    """Run an external JDBC asset and inspect it while its connection is alive."""
    source = asset_path(rt.root, "jdbc", source_name)
    if not source.exists():
        raise DriverError("missing jdbc driver source: %s" % source)
    target = rt.driver_dir / source.name
    shutil.copyfile(str(source), str(target))
    jar = compile_java(
        rt, target, rt.logs_dir / ("%s.javac.log" % class_name),
        "编译阶段 JDBC driver",
    )
    url = jdbc_url(rt, options or {"prepareThreshold": 1, "preferQueryMode": "extended"})
    command = [
        "java", "-cp", "%s:%s" % (rt.driver_dir, jar), class_name,
        url, user, password,
    ] + list(arguments or [])
    process = rt.start_jdbc_phase_process(
        command, target, url, rt.logs_dir / ("%s.java.log" % class_name),
        step_title, cwd=rt.driver_dir, sql_operations=sql_operations,
    )
    result = rt.observe_jdbc_phases(
        process, target, url, actions or [],
        lambda phase, marker: _phase_console_observation(
            rt, phase,
        ),
        timeout=30, finish_timeout=30,
        collect_logs=PHASE_LOG_EVIDENCE.get(rt.case.name, False),
    )
    if result[1] != 0:
        raise DriverError("JDBC command failed (%s): %s" % (result[1], " ".join(command)))
    return result[1], result[2]


def _phase_console_observation(rt, phase):
    """Collect complete business-visible console evidence at a JDBC pause."""
    chunks = []
    for index, sql in enumerate((
        "SHOW GLOBAL_PREPARED_STATEMENTS;",
        "SHOW GLOBAL_PREPARED_STATEMENTS_STATS;",
        "SHOW SERVER_PREP_STMTS;",
    ), 1):
        raw = rt.psql(
            "console", sql,
            rt.logs_dir / ("jdbc_%s_console_%s.raw.log" % (phase, index)),
            record=False,
        )
        chunks.append(
            "$ %s\n%s" % (
                quote_arguments(rt.psql_command("console", sql)),
                render_psql_table_from_pipe_text(raw),
            )
        )

    return {
        "command": "阶段 %s: console 查询 global/server cache" % phase,
        "output": "\n\n".join(chunks),
    }


def _run_phased_case_jdbc(rt, source, target, jar, command, url, title):
    actions = PHASED_JDBC_ACTIONS[rt.case.name]
    sql_hint = rt.case.sql.get("statement", "") if isinstance(rt.case.sql, dict) else ""
    sql_operations = jdbc_prepared_operations(source)
    if rt.case.name == "parse_invalid_error_recovery_same_connection":
        sql_operations = [
            {"sql": "DROP TABLE IF EXISTS test_parse_error", "parameters": []},
            {
                "sql": "SELECT inet_server_addr() AS server_ip, inet_server_port() AS server_port",
                "parameters": [],
            },
            {
                "sql": "SELECT * FROM test_parse_error WHERE id = ?",
                "parameters": ["$1=1 (预期失败)", "$1=2 (恢复执行)"],
            },
            {
                "sql": "CREATE TABLE test_parse_error (id INT PRIMARY KEY, data char(10))",
                "parameters": [],
            },
            {
                "sql": "INSERT INTO test_parse_error(id,data) VALUES (1,'test1'),(2,'test2')",
                "parameters": [],
            },
            {"sql": "GRANT SELECT ON test_parse_error TO postgres", "parameters": []},
        ]
    process = rt.start_jdbc_phase_process(
        command, target, url, rt.jdbc_log, title, cwd=rt.driver_dir,
        sql_operations=sql_operations,
    )
    return rt.observe_jdbc_phases(
        process, source, url, actions,
        lambda phase, marker: _phase_console_observation(rt, phase),
        timeout=30, finish_timeout=30,
        collect_logs=PHASE_LOG_EVIDENCE.get(rt.case.name, False),
    )


def run_case_jdbc(rt, safe_name, noise_patterns):
    case = rt.case
    source = jdbc_source_file(rt.root, case, safe_name)
    target = rt.driver_dir / source.name
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    compile_title = "编译 JDBC driver"
    run_title = "执行 JDBC driver"
    if case.name == "heartbeat_reload_reclassifies_existing_normal_entry":
        compile_title = "编译 JDBC driver: prepared SELECT 124"
        run_title = "发送 prepared statement: SELECT 124"
    elif case.name == "heartbeat_reload_old_entry_demoted_or_not_bypassed":
        compile_title = "编译 JDBC driver: prepared SELECT 123"
        run_title = "发送 prepared statement: SELECT 123"
    jar = compile_java(rt, target, rt.logs_dir / "javac.log", compile_title)
    options = {
        "prepareThreshold": case.jdbc.get("prepare_threshold", 1),
        "preferQueryMode": case.jdbc.get("prefer_query_mode", "extended"),
        "binaryTransfer": case.jdbc.get("binaryTransfer"),
        "connectTimeout": case.jdbc.get("connectTimeout"),
        "socketTimeout": case.jdbc.get("socketTimeout"),
    }
    url = jdbc_url(rt, options)
    command = [
        "java", "-cp", "%s:%s" % (rt.driver_dir, jar),
        "GC_" + safe_name(case.name), url, "postgres", "",
    ]
    if case.name in PHASED_JDBC_ACTIONS:
        _, rc, output = _run_phased_case_jdbc(
            rt, source, target, jar, command, url, run_title,
        )
        if rc != 0:
            raise DriverError("JDBC command failed (%s): %s" % (rc, " ".join(command)))
    else:
        _, output = run_java(
            rt, "GC_" + safe_name(case.name), jar, rt.jdbc_log,
            options=options,
            allow_failure=case.name == "discard_all_prepared_lowercase_invalidates_server_cache",
            step_title=run_title, source=source,
        )
    record_driver_api_calls(rt, source)
    rt.summary["jdbc_output"] = output
    filtered = [
        line for line in output.splitlines()
        if not any(pattern in line for pattern in noise_patterns)
    ]
    (rt.logs_dir / "jdbc.filtered.log").write_text(
        "\n".join(filtered) + ("\n" if filtered else ""), encoding="utf-8"
    )
    return filtered


def run_prepared_sequence(rt, operations, log_stem="GC_prepared_sql_sequence", allow_failure=False):
    normalized = normalize_prepared_sequence(operations)
    rt.summary["jdbc_sequence"] = normalized
    source = asset_path(rt.root, "jdbc", "GC_prepared_sql_sequence.java")
    target = rt.driver_dir / source.name
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    jar = compile_java(rt, target, rt.logs_dir / "GC_prepared_sql_sequence.javac.log", "编译外置通用 JDBC driver")
    command = [
        "java", "-cp", "%s:%s" % (rt.driver_dir, jar), "GC_prepared_sql_sequence",
        jdbc_url(rt, {"prepareThreshold": 1, "preferQueryMode": "extended"}), "postgres", "",
    ]
    for operation in normalized:
        command.extend([operation["output_key"], operation["mode"], operation["sql"]])
    report_operations = [
        {"sql": operation["sql"], "parameters": ["mode=%s" % operation["mode"]]}
        for operation in normalized
    ]
    actions = [
        PhaseAction(
            "after_%s" % operation["output_key"],
            "PHASE=AFTER_%s" % operation["output_key"],
            title="JDBC 完成: %s 后观察缓存" % operation["output_key"],
            expected="该条 JDBC SQL 已完成，连接保持且 console/log 可观察到本阶段状态。",
        )
        for operation in normalized
    ]
    process = rt.start_jdbc_phase_process(
        command, target, command[4], rt.logs_dir / ("%s.java.log" % log_stem),
        "执行外置通用 JDBC driver", cwd=rt.driver_dir,
        sql_operations=report_operations,
    )
    observations, rc, output = rt.observe_jdbc_phases(
        process, target, command[4], actions,
        lambda phase, marker: _phase_console_observation(rt, phase),
        timeout=30, finish_timeout=30,
        collect_logs=PHASE_LOG_EVIDENCE.get(rt.case.name, False),
    )
    result = (rc, output)
    if allow_failure and rc == 0:
        raise DriverError("%s expects JDBC execution to fail, but rc=0" % rt.case.name)
    if not allow_failure and rc != 0:
        raise DriverError("JDBC command failed (%s): %s" % (rc, " ".join(command)))
    record_driver_api_calls(rt, source)
    rt.summary["jdbc_sequence_output"] = without_phase_markers(output)
    return result


def start_phased_prepared(rt, operations, log_stem="GC_phased_prepared"):
    normalized = normalize_phased_prepared_operations(operations)
    rt.summary["phased_prepared_operations"] = normalized
    source = asset_path(rt.root, "jdbc", "GC_phased_prepared.java")
    target = rt.driver_dir / source.name
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    jar = compile_java(
        rt,
        target,
        rt.logs_dir / "GC_phased_prepared.javac.log",
        "编译外置阶段 JDBC driver",
    )
    command = [
        "java", "-cp", "%s:%s" % (rt.driver_dir, jar), "GC_phased_prepared",
        jdbc_url(rt, {"prepareThreshold": 1, "preferQueryMode": "extended"}),
        "postgres", "",
    ]
    for operation in normalized:
        command.extend([
            operation["output_key"], operation["sql"], str(operation["bind_value"]),
        ])
    logfile = rt.logs_dir / ("%s.java.log" % log_stem)
    report_operations = [
        {
            "sql": operation["sql"],
            "parameters": ["$1=%s" % operation["bind_value"]],
        }
        for operation in normalized
    ]
    return (
        rt.start_jdbc_phase_process(
            command,
            target,
            command[4],
            logfile,
            "执行外置阶段 JDBC driver",
            cwd=rt.driver_dir,
            sql_operations=report_operations,
        ),
        command,
        logfile,
    )
