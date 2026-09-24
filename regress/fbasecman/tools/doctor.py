import os
from pathlib import Path

from framework.execution.shell import LoggedShellRunner
from framework.configuration import ConfigurationError, validate_profile_isolation


class DoctorResult:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.lines = []

    def ok(self, message):
        self.lines.append("[OK] " + message)

    def warn(self, message):
        self.warnings.append(message)
        self.lines.append("[WARN] " + message)

    def error(self, message):
        self.errors.append(message)
        self.lines.append("[ERROR] " + message)

    def exit_code(self):
        return 1 if self.errors else 0


def _check_file(result, label, path):
    path = Path(path)
    if path.exists():
        result.ok("%s exists: %s" % (label, path))
    else:
        result.error("%s missing: %s" % (label, path))


def _check_dir(result, label, path):
    path = Path(path)
    if path.is_dir():
        result.ok("%s exists: %s" % (label, path))
    else:
        result.error("%s missing: %s" % (label, path))


def _check_local_pg_tool(result, postgres_dir, tool):
    _check_file(result, "local %s" % tool, Path(postgres_dir) / "bin" / tool)


def _check_remote_pg_tools(result, runner, user, host, postgres_dir, label):
    script = """
    test -x "{0}/bin/initdb" &&
    test -x "{0}/bin/pg_ctl" &&
    test -x "{0}/bin/psql" &&
    test -x "{0}/bin/pg_basebackup"
    """.format(postgres_dir).strip()
    rc = runner.run_remote(user, host, script, log_name="doctor_%s_pg_tools.log" % label, check=False)
    if rc.returncode == 0:
        result.ok("%s remote PostgreSQL tools exist on %s" % (label, host))
    else:
        detail = (rc.stderr or rc.stdout or "").strip()
        if detail:
            result.error("%s remote PostgreSQL tools check failed on %s: %s; see %s" %
                         (label, host, detail, runner.log_dir / ("doctor_%s_pg_tools.log" % label)))
        else:
            result.error("%s remote PostgreSQL tools missing on %s; see %s" %
                         (label, host, runner.log_dir / ("doctor_%s_pg_tools.log" % label)))


def run_doctor(env):
    result = DoctorResult()
    cfg = env.config
    runner = LoggedShellRunner(env.env_logs_dir)

    for source in getattr(env, "sources", ()):
        result.ok("configuration source: %s" % source)

    try:
        validate_profile_isolation(env)
        result.ok("stable/regression ports, PGDATA roots, and environment output are isolated")
    except ConfigurationError as exc:
        result.error(str(exc))

    _check_file(result, "fbasecman_bin", cfg["fbasecman"]["fbasecman_bin"])
    _check_dir(result, "license_dir", cfg["fbasecman"]["license_dir"])
    _check_dir(result, "local postgres_dir", cfg["local"]["postgres_dir"])
    for tool in ("psql", "pg_ctl", "initdb", "pg_basebackup"):
        _check_local_pg_tool(result, cfg["local"]["postgres_dir"], tool)

    jdbc_dir = (env.root_dir / cfg["local"]["jdbc_lib_dir"]).resolve()
    if jdbc_dir.exists():
        result.ok("jdbc_lib_dir exists: %s" % jdbc_dir)
    else:
        result.warn("jdbc_lib_dir does not exist yet, env setup will create it: %s" % jdbc_dir)

    _check_remote_pg_tools(
        result,
        runner,
        cfg["database"]["mmr_pg_user"],
        cfg["database"]["mmr_host"],
        cfg["database"]["mmr_postgres_dir"],
        "mmr",
    )
    if "rep_pg_user" in cfg["database"]:
        _check_remote_pg_tools(
            result,
            runner,
            cfg["database"]["rep_pg_user"],
            cfg["database"]["rep_host"],
            cfg["database"]["rep_postgres_dir"],
            "rep",
        )

    return result
