"""Executors for Chapter 9: JDBC read/write splitting scenarios (42.2.7, 42.7.0, 42.7.7)."""

import re
from framework.clients.psql import build_psql_command
from framework.execution.command import run_logged_command
from framework.execution.phased_process import PhaseAction, PhasedProcess
from framework.execution.shell import quote_arguments
from suites.handover.runtime import HandoverFailure


def execute_jdbc(rt):
    """9.1 & 9.2 JDBC 读写分离时序矩阵测试"""
    rt.start()
    version = "42.2.7" if "4227" in rt.case.name else ("42.7.0" if "4270" in rt.case.name else "42.7.7")
    source = rt.root / "suites" / "handover" / "assets" / "jdbc" / "HandoverJdbcRouting.java"
    jar = rt.root / rt.env.config["local"]["jdbc_lib_dir"] / ("postgresql-%s.jar" % version)
    build = rt.workdir / "jdbc"
    build.mkdir(parents=True, exist_ok=True)
    compile_log = rt.logs_dir / "jdbc_compile.log"
    compile_result = run_logged_command(["javac", "-cp", str(jar), "-d", str(build), str(source)], compile_log, cwd=build)
    if compile_result.returncode != 0:
        raise HandoverFailure("JDBC compile failed: %s" % compile_result.output)

    url = "jdbc:postgresql://127.0.0.1:%s/postgres?user=postgres" % rt.listen_port
    run_log = rt.logs_dir / "jdbc_run.log"
    protocol_mode = "old" if version == "42.2.7" else "new"
    phase_groups = ("OLD_11", "OLD_12", "OLD_13", "OLD_14") if protocol_mode == "old" else (
        "NEW_11", "NEW_12", "NEW_13", "NEW_14",
    )

    def observe(phase, marker):
        command = build_psql_command(
            rt.env.config["local"]["postgres_dir"], "127.0.0.1", rt.listen_port,
            "admin", "console", "SHOW CLIENTS;",
        )
        logfile = rt.logs_dir / ("jdbc_%s_show_clients.log" % phase.lower())
        rc, console_output = rt.run_command(command, logfile, check=False, record=False)
        visible = console_output.rstrip() or "<empty>"
        passed = rc == 0 and "postgres" in console_output.lower()
        return {
            "command": "$ " + quote_arguments(command),
            "output": visible,
            "passed": passed,
            "actual": ("SHOW CLIENTS 中存在保持中的 postgres JDBC client。" if passed else visible),
        }

    process = PhasedProcess(
        ["java", "-cp", "%s:%s" % (jar, build), "HandoverJdbcRouting", url, protocol_mode],
        run_log, cwd=build,
    )
    actions = [
        PhaseAction(
            phase, "PHASE_PAUSE=%s" % phase, "continue",
            title="JDBC %s 文档分组 %s 完成后的中间观察" % (version, phase),
            expected="该分组 JDBC 时序完成且 SHOW CLIENTS 保留 postgres JDBC client。",
        )
        for phase in phase_groups
    ]
    observations, rc, output, _ = rt.observe_jdbc_phases(
        process, source, url, actions, observe, timeout=30, finish_timeout=60,
        collect_logs=False,
    )
    if rc != 0:
        raise HandoverFailure("JDBC %s routing driver failed with rc=%s: %s" % (version, rc, output))

    # 验证矩阵完成标识
    matrix_marker = "OLD_MATRIX_OK" if protocol_mode == "old" else "NEW_MATRIX_OK"
    rt.check("JDBC %s 时序矩阵执行完成" % version, "包含 %s" % matrix_marker, output, matrix_marker in output)
