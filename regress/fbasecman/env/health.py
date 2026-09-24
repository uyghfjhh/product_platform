from framework.configuration import RegressionConfig

from framework.execution.shell import LoggedShellRunner, ShellCommandError


def _run_health_sql(runner, user, host, script, log_name):
    try:
        result = runner.run_remote(
            user,
            host,
            script,
            log_name=log_name,
        )
        value = result.stdout.strip()
        return value if value else "EMPTY"
    except ShellCommandError as exc:
        stderr = (exc.result.stderr or "").strip().splitlines()
        stdout = (exc.result.stdout or "").strip().splitlines()
        message = ""
        if stderr:
            message = stderr[-1]
        elif stdout:
            message = stdout[-1]
        else:
            message = "command failed rc=%s" % exc.result.returncode
        return "UNAVAILABLE: %s" % message


def check_environment(env, runner, verbose=False):
    cfg = env.config
    db = cfg["database"]
    ports = db["ports"]
    mmr_dir = db["mmr_postgres_dir"]
    mmr_host = db["mmr_host"]
    mmr_user = db["mmr_pg_user"]

    checks = {}

    mmr_non_active = _run_health_sql(
        runner,
        mmr_user,
        mmr_host,
        """{pg}/bin/psql -p {port} -d postgres -U {user} -t -c "select count(*) from fdd.mmr_node where node_state != 'ACTIVE';" """.format(
            pg=mmr_dir,
            port=ports["mmr1"],
            user=mmr_user,
        ),
        log_name="90_check_mmr_non_active.log",
    )
    checks["mmr_non_active"] = mmr_non_active

    testdb_node1 = _run_health_sql(
        runner,
        mmr_user,
        mmr_host,
        """{pg}/bin/psql -p {port} -d test_db -U {user} -t -c "select node_state from fdd.mmr_node where node_name = 'testdb_node1';" """.format(
            pg=mmr_dir,
            port=ports["mmr1"],
            user=mmr_user,
        ),
        log_name="91_check_testdb_node1.log",
    )
    checks["testdb_node1"] = testdb_node1

    testdb_node2 = _run_health_sql(
        runner,
        mmr_user,
        mmr_host,
        """{pg}/bin/psql -p {port} -d test_db -U {user} -t -c "select node_state from fdd.mmr_node where node_name = 'testdb_node2';" """.format(
            pg=mmr_dir,
            port=ports["mmr1"],
            user=mmr_user,
        ),
        log_name="92_check_testdb_node2.log",
    )
    checks["testdb_node2"] = testdb_node2

    mmr_streaming = _run_health_sql(
        runner,
        mmr_user,
        mmr_host,
        """{pg}/bin/psql -p {port} -d postgres -U {user} -t -c "select count(*) from pg_stat_replication where state = 'streaming' and application_name not like 'fmmr%';\" """.format(
            pg=mmr_dir,
            port=ports["mmr1"],
            user=mmr_user,
        ),
        log_name="93_check_mmr_streaming.log",
    )
    checks["mmr_streaming"] = mmr_streaming
    checks["rep_streaming"] = mmr_streaming
    return checks
