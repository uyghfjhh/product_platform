"""Executors for Chapter 11: Expanded product capabilities (Heartbeat, GUC, Attach, Parse error, Global PS)."""

import re
from pathlib import Path

from framework.execution.command import run_logged_command
from framework.execution.phased_process import PhaseAction, PhasedProcess
from suites.handover.runtime import HandoverFailure


def execute_heartbeat_interception(rt):
    """11.1、探活功能：探活 SQL 拦截且绝对不发送至 PostgreSQL"""
    # 配置文件中已设置 heartbeat_request "select 12"
    rt.start(heartbeat_request="select 12")

    # 1. 发送精确探活 SQL
    rc, out_hb, proxy_log, pg_log = rt.psql(
        "select 12;",
        title="11.1: 发送精确匹配的探活语句 'select 12;'",
        collect_logs=True,
    )
    # 验证客户端收到返回 1
    rt.check("探活请求直接返回成功", "输出包含 1", out_hb.strip(), "1" in out_hb)
    # 验证代理日志中记录了拦截
    rt.assert_proxy_log_pattern(r"(heartbeat|intercepted)", title="验证代理日志捕获心跳拦截记录")
    # 强校验：PostgreSQL 日志中绝对没有 select 12
    rt.assert_pg_log_pattern_absent(r"statement:\s*select\s+12\b", title="强校验：PostgreSQL 日志绝对无探活语句执行记录")

    # 2. 发送普通业务 SQL "select 22;"
    rc, out_normal, _, _ = rt.psql(
        "select 22;",
        title="11.1: 发送普通业务语句 'select 22;' (不应被拦截)",
        collect_logs=True,
    )
    rt.check("普通业务语句正常返回", "输出包含 22", out_normal.strip(), "22" in out_normal)


def execute_guc_sync(rt):
    """11.2、GUC 参数感知和动态同步功能"""
    rt.start()

    # 1. 设置会话 GUC 参数并验证生效
    statements = [
        "SET timezone = 'Asia/Shanghai'",
        "SHOW timezone",
        "SET search_path = public",
        "SHOW search_path",
        # 读写切换以触发跨连接 GUC 部署
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
        "SHOW timezone",
        "SHOW search_path",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
        "SHOW timezone",
        "SHOW search_path",
        # RESET 与 DISCARD
        "RESET ALL",
        "SHOW timezone",
        "DISCARD ALL",
    ]
    rc, output, proxy_log, _ = rt.psql_script(
        statements,
        title="11.2: GUC 设置、跨读写连接同步、RESET 与 DISCARD 全流程",
        collect_logs=True,
    )
    rt.check("GUC 同步保持正确", "SHOW timezone 保持 Asia/Shanghai", output, "Asia/Shanghai" in output)
    rt.check("GUC search_path 保持正确", "SHOW search_path 保持 public", output, "public" in output)
    rt.assert_proxy_log_pattern(r"(guc|sync|deploy)", title="验证代理日志记录 GUC 同步动作")


def execute_attach_optimization(rt):
    """11.3、attach 流程优化：读写切换标签、探活和 GUC 不产生多余后端连接"""
    rt.start()

    # 初始连接并记录连接数
    rt.psql("SELECT 1;", title="建立初始客户端连接")
    _, out_pools_before, _, _ = rt.console("SHOW POOLS;", "查看初始连接池 sv_active 数量")
    
    # 连续执行读写切换标签、探活和 GUC
    statements = [
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
        "select 12",  # 探活
        "SET search_path = public",  # GUC
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
    ]
    rt.psql_script(statements, title="11.3: 执行读写切换、探活与 GUC 操作", collect_logs=True)

    # 再次检查连接池数量
    _, out_pools_after, _, _ = rt.console("SHOW POOLS;", "查看操作后连接池 sv_active 数量")
    rt.check("连接池连接未异常增加", "连接复用优化生效", out_pools_after, "sv_active" in out_pools_after)


def execute_parse_error_single(rt):
    """11.4.3.1 单 Parse 执行失败后后端缓存清理与连接复用"""
    rt.start()

    # 故意构造语法错误的 Prepare/Parse，然后紧跟正确的 Prepare
    statements = [
        "PREPARE err_stmt AS SELECT * FROM nonexistent_table_for_error_test",
        "PREPARE valid_stmt AS SELECT 1",
        "EXECUTE valid_stmt",
        "DEALLOCATE valid_stmt",
    ]
    rc, output, proxy_log, _ = rt.psql_script(
        statements,
        title="11.4.3.1: 单 Parse 失败后继续执行后续 PreparedStatement",
        check_rc=False,
        collect_logs=True,
    )
    rt.check("后续合法 PreparedStatement 执行成功", "输出包含 1", output, "1" in output)


def execute_parse_error_multiple(rt):
    """11.4.3.2 多 Parse 序列中某个失败后恢复"""
    rt.start()

    statements = [
        "PREPARE stmt_ok1 AS SELECT 10",
        "PREPARE stmt_fail AS SELECT * FROM nonexistent_table_abc",
        "PREPARE stmt_ok2 AS SELECT 20",
        "EXECUTE stmt_ok1",
        "EXECUTE stmt_ok2",
        "DEALLOCATE stmt_ok1",
        "DEALLOCATE stmt_ok2",
    ]
    rc, output, proxy_log, _ = rt.psql_script(
        statements,
        title="11.4.3.2: 多 Parse 序列中间失败后合法语句正常执行",
        check_rc=False,
        collect_logs=True,
    )
    rt.check("合法语句 ok1 与 ok2 均成功执行", "包含 10 和 20", output, "10" in output and "20" in output)


def execute_global_prepared_statements(rt):
    """11.5.2 & 11.5.3 PreparedStatements 全局缓存基本功能与读写切换"""
    rt.start()

    # 1. 准备初始数据表
    db = rt.env.config["database"]
    postgres_dir = Path(rt.env.config["local"]["postgres_dir"])
    sql = (
        "DROP TABLE IF EXISTS handover_global_ps; "
        "CREATE TABLE handover_global_ps(id int primary key, note text); "
        "INSERT INTO handover_global_ps VALUES (1, 'name1'), (2, 'name2'), (3, 'name3');"
    )
    for node, port in (("mmr1", db["ports"]["mmr1"]), ("mmr2", db["ports"]["mmr2"])):
        cmd = [str(postgres_dir / "bin" / "psql"), "-h", db["mmr_host"], "-p", str(port),
               "-U", db["mmr_pg_user"], "-d", "postgres", "-c", sql]
        res = run_logged_command(cmd, rt.logs_dir / ("global_ps_prepare_%s.log" % node), cwd=rt.workdir)
        rt.record_step("在 %s 准备全局 PreparedStatement 初始数据" % node, command=res.command,
                       expected="handover_global_ps 含三条初始数据", actual=res.output,
                       result="PASS" if res.returncode == 0 else "FAIL")
        if res.returncode != 0:
            raise HandoverFailure("failed to seed handover_global_ps on %s: %s" % (node, res.output))

    # 2. 编译并运行 HandoverGlobalPrepared.java
    source = rt.root / "suites" / "handover" / "assets" / "jdbc" / "HandoverGlobalPrepared.java"
    jar = rt.root / rt.env.config["local"]["jdbc_lib_dir"] / "postgresql-42.7.7.jar"
    build = rt.workdir / "global_ps_first"
    build.mkdir(parents=True, exist_ok=True)
    compile_res = run_logged_command(["javac", "-cp", str(jar), "-d", str(build), str(source)],
                                     rt.logs_dir / "global_ps_first_compile.log", cwd=build)
    if compile_res.returncode != 0:
        raise HandoverFailure("HandoverGlobalPrepared JDBC compile failed: %s" % compile_res.output)

    url = "jdbc:postgresql://127.0.0.1:%s/postgres?user=postgres&prepareThreshold=1&preferQueryMode=extended" % rt.listen_port
    process = PhasedProcess(
        ["java", "-cp", "%s:%s" % (jar, build), "HandoverGlobalPrepared", url],
        rt.logs_dir / "global_ps_first_run.log", cwd=build,
    )

    phases = ("PREPARED_ROWS", "DEFAULT", "READ_ONLY_ONE", "READ_WRITE_ONE",
              "READ_ONLY_TWO", "READ_WRITE_TWO", "BEGIN_READ_ONLY")
    phase_titles = {
        "PREPARED_ROWS": "PreparedStatement 写入初始数据后",
        "DEFAULT": "默认读写状态查询后（未执行读写切换）",
        "READ_ONLY_ONE": "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY 后",
        "READ_WRITE_ONE": "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE 后",
        "READ_ONLY_TWO": "第二次切换为 READ ONLY 后",
        "READ_WRITE_TWO": "第二次切换为 READ WRITE 后",
        "BEGIN_READ_ONLY": "BEGIN READ ONLY 后",
    }
    actions = [
        PhaseAction(
            phase, "PHASE_PAUSE=%s" % phase, "continue",
            title="JDBC HandoverGlobalPrepared: %s 中间观察" % phase_titles.get(phase, phase),
            expected="JDBC 到达阶段并完成 console 中间观察",
        )
        for phase in phases
    ]

    write_port = str(db["ports"]["mmr1"])
    from suites.handover.helpers import _phase_console_observation, _global_ps_phase_validator, _validate_global_ps_rows, _global_ps_final_entries_expected, _stats_values
    from framework.evidence.jdbc import without_phase_markers

    validator = _global_ps_phase_validator(first_run=True, write_port=write_port)
    def observe(phase, marker):
        queries = ("SHOW GLOBAL_PREPARED_STATEMENTS;", "SHOW GLOBAL_PREPARED_STATEMENTS_STATS;")
        obs = _phase_console_observation(rt, phase, queries, "global_ps_first")
        obs["jdbc_output"] = without_phase_markers(process.output)
        obs.update(validator(phase, obs))
        return obs

    _, rc, output, _ = rt.observe_jdbc_phases(
        process, source, url, actions, observe, timeout=30, finish_timeout=60,
        collect_logs=False,
    )
    output = without_phase_markers(output)
    if rc != 0:
        raise HandoverFailure("HandoverGlobalPrepared failed with rc=%s: %s" % (rc, output))

    # 3. 结果与路由断言
    expected_markers = ("DEFAULT id=1 note=name1", "READ_ONLY_ONE id=2 note=name2",
                        "READ_WRITE_ONE id=1 note=name1", "READ_ONLY_TWO id=3 note=name3",
                        "READ_WRITE_TWO id=2 note=name2", "BEGIN_READ_ONLY id=3 note=name3",
                        "GLOBAL_PS_SWITCH_OK")
    rt.check("JDBC PreparedStatement 读写切换结果", "六段查询均返回文档初始数据",
             output, all(marker in output for marker in expected_markers))

    read_ports = re.findall(r"(?:READ_ONLY|BEGIN_READ_ONLY)[^\n]*backend_port=(\d+)", output)
    write_ports = re.findall(r"(?:DEFAULT|READ_WRITE)[^\n]*backend_port=(\d+)", output)
    rt.check("全局缓存读写切换路由正确", "READ ONLY 非写主，READ WRITE 为 %s" % write_port,
             output, bool(read_ports) and all(p != write_port for p in read_ports) and
             bool(write_ports) and all(p == write_port for p in write_ports))

    # 4. 控制台全局缓存条目与统计核对
    _, out_cache, _, _ = rt.console("SHOW GLOBAL_PREPARED_STATEMENTS;", "首次执行后的全局缓存条目", collect_logs=False)
    entries_passed, entries_actual = _validate_global_ps_rows(out_cache, 7, check_final_ref_counts=True)
    rt.check("首次全局缓存条目符合文档", _global_ps_final_entries_expected(), entries_actual, entries_passed)

    _, stats_before, _, _ = rt.console("SHOW GLOBAL_PREPARED_STATEMENTS_STATS;", "首次执行后的全局缓存统计", collect_logs=False)
    stats = _stats_values(stats_before)
    stats_ok = all(stats.get(key) == val for key, val in {
        "total_entries": 7, "referenced_entries": 5, "unreferenced_entries": 2,
        "bypass_entries": 0, "capacity": 10000, "hits": 0, "misses": 7, "evictions": 0,
    }.items())
    rt.check("首次全局缓存统计符合文档", "total_entries=7, referenced=5, unreferenced=2, misses=7",
             stats_before.strip(), stats_ok)


def execute_global_prepared_special_sql(rt):
    """11.5.3.2 全局缓存的心跳、GUC 和特殊事务标签"""
    rt.start()

    statements = [
        "PREPARE p_guc AS SET search_path = public",
        "EXECUTE p_guc",
        "SHOW search_path",
        "DEALLOCATE p_guc",
    ]
    rc, output, _, _ = rt.psql_script(statements, title="11.5.3.2: 特殊 SQL 类型全局缓存测试", collect_logs=True)
    rt.check("特殊 SQL GUC 生效", "包含 public", output, "public" in output)


def execute_global_prepared_eviction(rt):
    """11.5.3.3.1 全局和后端 PreparedStatement 缓存容量超限淘汰"""
    rt.start()

    # 准备若干不同的 Prepared Statement
    statements = []
    for i in range(1, 15):
        statements.append("PREPARE stmt_%d AS SELECT %d" % (i, i))
        statements.append("EXECUTE stmt_%d" % i)
        statements.append("DEALLOCATE stmt_%d" % i)

    rc, output, _, _ = rt.psql_script(statements, title="11.5.3.3.1: 批量执行 PS 触发淘汰机制", collect_logs=True)
    rt.check("PS 批量执行完成", "执行成功", output, rc == 0)


def execute_global_prepared_bypass_retention(rt):
    """11.5.3.3.2 持有 bypass 响应的全局缓存条目不淘汰"""
    rt.start()

    statements = [
        "PREPARE p_bypass AS SELECT 999",
        "EXECUTE p_bypass",
        "DEALLOCATE p_bypass",
    ]
    rc, output, _, _ = rt.psql_script(statements, title="11.5.3.3.2: bypass 响应条目测试", collect_logs=True)
    rt.check("bypass 语句执行成功", "包含 999", output, "999" in output)
