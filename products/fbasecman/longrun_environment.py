"""Provision the upstream six-node longrun topology under one task directory."""

import os
import re
import shutil
import subprocess
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from zipfile import ZipFile

from platform_regress.environment.disposable import reserve_listener

NODES = {"mmr1": None, "mmr2": None, "mmr1_s1": "mmr1", "mmr1_s2": "mmr1",
         "mmr1_s3": "mmr1", "mmr2_s1": "mmr2"}
HOST = "127.0.0.1"


def settings_from_upstream(suite):
    script = (suite / "set_stabilize.sh").read_text(encoding="utf-8")
    parameters = {}
    excluded = re.search(r'(?m)^EXCLUDE_SCHEMA="([^"]+)"$', script)
    if not excluded:
        raise ValueError("上游长稳脚本缺少 EXCLUDE_SCHEMA")
    for name in ("mmr1", "mmr2"):
        group = name.upper()
        block = script.split(f"set_{name}()\n", 1)[1].split(">>", 1)[0]
        config = block.split('listen_addresses =', 1)[1].splitlines()[1:]
        values = []
        for line in config:
            line = line.strip().removesuffix('\\"').rstrip()
            if line.startswith("port ="):
                continue
            preload = re.search(rf'(?m)^{group}_PRELOAD="([^"]+)"$', script)
            if not preload:
                raise ValueError(f"上游长稳脚本缺少 {group}_PRELOAD")
            line = line.replace(f"${group}_PRELOAD", preload[1]).replace("$EXCLUDE_SCHEMA", excluded[1])
            if not re.fullmatch(r"[a-z][a-z0-9_.]*\s*=\s*(?:'[^'$]+'|[A-Za-z0-9]+)", line):
                raise ValueError(f"上游 {name} PostgreSQL 参数无法解析：{line}")
            values.append(line)
        parameters[name] = values
    return parameters


def sql_fixture_from_upstream(suite, node):
    script = (suite / "set_stabilize.sh").read_text(encoding="utf-8")
    next_section = {"mmr1": "set_mmr2", "mmr2": "init_sql_parse_stability_data"}[node]
    block = script.split(f"set_{node}()\n", 1)[1].split(f"{next_section}()\n", 1)[0]
    schema_lines = []
    grants = []
    for raw in block.splitlines():
        line = raw.strip()
        if "cron." in line or line.startswith("REVOKE ALL ON SCHEMA cron "):
            continue
        if line.startswith(("CREATE TABLE ", "CREATE USER ", "insert into test ", "SET password_encryption=",
                            "RETURNS integer", "LANGUAGE sql", "AS 'SELECT i + 1'", "CREATE OR REPLACE FUNCTION ",
                            "value integer", "id integer", ");")):
            schema_lines.append(line.partition(";")[0] + ";" if ";" in line else line)
        elif line.startswith(("GRANT ", "REVOKE ")):
            grants.append(line.partition(";")[0] + ";")
    if not any("CREATE USER sql_parse " in line for line in schema_lines):
        raise ValueError("无法找到上游常稳账号定义")
    return "\n".join(["CREATE ROLE replicator WITH REPLICATION LOGIN;",
                      "CREATE SCHEMA jdbc_parse_test;", "CREATE SCHEMA sql_parse_stability;",
                      *schema_lines, *grants])


def local_database_home():
    configured = os.environ.get("PRODUCT_PLATFORM_FBASECMAN_STABLE_PG_HOME")
    home = Path(configured or "/usr/local/pgsql").resolve()
    if not (home / "bin/pg_config").is_file():
        raise ValueError("缺少本机 FBase MMR 安装，请配置 PRODUCT_PLATFORM_FBASECMAN_STABLE_PG_HOME")
    return home


def command(argv, log, *, input_text=None, timeout=90):
    with log.open("a", encoding="utf-8") as handle:
        result = subprocess.run([str(arg) for arg in argv], input=input_text, text=True,
                                stdout=handle, stderr=subprocess.STDOUT, timeout=timeout, check=False)
    if result.returncode:
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
        raise RuntimeError(f"{Path(argv[0]).name} 失败，日志 {log}\n" + "\n".join(tail))


def prepare_installation(suite, root):
    home = local_database_home()
    pg_config = home / "bin/pg_config"
    lib = Path(subprocess.check_output([pg_config, "--pkglibdir"], text=True).strip())
    share = Path(subprocess.check_output([pg_config, "--sharedir"], text=True).strip())
    if any(not (lib / name).is_file() for name in ("fdd_mmr.so", "fddoutput.so")):
        raise ValueError(f"所选 FBase 安装缺少 fdd_mmr/fddoutput：{home}")
    if (lib / "pg_cron.so").is_file() and (share / "extension/pg_cron.control").is_file():
        return home

    # Build and install the same upstream pg_cron into this task's private
    # installation. No changes are made to the system PostgreSQL installation.
    staged = root / "fbase-home"
    shutil.copytree(home, staged, symlinks=True)
    staged_config = staged / "bin/pg_config"
    for option in ("--pkglibdir", "--sharedir"):
        destination = Path(subprocess.check_output([staged_config, option], text=True).strip()).resolve()
        if not destination.is_relative_to(staged):
            raise ValueError(f"FBase 安装不能安全重定位：{destination}")
    target = root / "contrib"
    with ZipFile(suite / "contrib/pg_cron-main.zip") as archive:
        archive.extractall(target)
    make_args = ["make", "-C", target / "pg_cron-main", f"PG_CONFIG={staged / 'bin/pg_config'}"]
    log = root / "pg_cron-build.log"
    command([*make_args, "-j2"], log)
    command([*make_args, "install"], log)
    staged_lib = staged / lib.relative_to(home)
    if not (staged_lib / "pg_cron.so").is_file():
        raise RuntimeError(f"pg_cron 未安装到任务自己的 FBase 目录，见 {log}")
    return staged


@contextmanager
def isolated_environment(suite, root, license_file=None):
    root.mkdir(mode=0o700)
    home = prepare_installation(suite, root)
    binaries = home / "bin"
    evidence = root / "postgres-evidence"
    evidence.mkdir()
    parameters = settings_from_upstream(suite)
    reserved = {}
    with ExitStack() as stack:
        for name in NODES:
            port, listener = reserve_listener(reserved)
            stack.callback(listener.close)
            reserved[port] = (name, listener)
        ports = {name: int(port) for port, (name, _) in reserved.items()}

        def pg(name, tool, *args, timeout=90, sql=None):
            command([binaries / tool, *args], root / f"{name}.setup.log",
                    input_text=sql, timeout=timeout)

        def psql(node, sql):
            pg(node, "psql", "-Xqw", "-v", "ON_ERROR_STOP=1", "-h", HOST,
               "-p", ports[node], "-U", "postgres", "-d", "postgres", sql=sql)

        def start(name, config):
            datadir = root / name
            log = root / f"{name}.postgres.log"
            with (datadir / "postgresql.conf").open("a", encoding="utf-8") as handle:
                handle.write(f"\nlisten_addresses = '{HOST}'\nport = {ports[name]}\n")
                handle.write("\n".join(config) + "\n")
            reserved[str(ports[name])][1].close()

            started = False

            def stop():
                if (datadir / "postmaster.pid").is_file():
                    pg(name, "pg_ctl", "-D", datadir, "stop", "-m", "fast", "-w", "-t", "15", timeout=18)
                elif not started:
                    return
                if (datadir / "postmaster.pid").exists():
                    raise RuntimeError(f"拒绝清理仍在运行的任务实例 {datadir}")
                retained = evidence / name
                retained.mkdir()
                for filename in ("postgresql.conf", "postgresql.auto.conf", "pg_hba.conf"):
                    shutil.copyfile(datadir / filename, retained / filename)
                if (datadir / "log").is_dir():
                    shutil.move(datadir / "log", retained / "log")
                shutil.rmtree(datadir)

            stack.callback(stop)
            pg(name, "pg_ctl", "-D", datadir, "-l", log, "start", "-w")
            started = True

        for name in ("mmr1", "mmr2"):
            datadir = root / name
            pg(name, "initdb", "-D", datadir, "-U", "postgres", "-A", "trust")
            if license_file:
                shutil.copyfile(license_file, datadir / "license.dat")
            start(name, parameters[name])
            psql(name, "CREATE EXTENSION fdd_mmr;")
            if name == "mmr1":
                psql(name, "CREATE EXTENSION pg_cron;")
            psql(name, sql_fixture_from_upstream(suite, name))
            psql(name, f"SELECT fdd.create_node('node{name[-1]}', "
                 f"'host={HOST} port={ports[name]} user=postgres dbname=postgres');")
            if name == "mmr1":
                psql(name, "SELECT fdd.create_group('g1');")
                psql(name, (suite / "create_procdure.sql").read_text(encoding="utf-8"))
            else:
                psql(name, """CREATE SCHEMA cron;
                    CREATE TABLE cron.job (jobid bigint PRIMARY KEY,schedule text NOT NULL,command text NOT NULL,
                        nodename text NOT NULL,nodeport integer NOT NULL,database text NOT NULL,username text NOT NULL,
                        active boolean NOT NULL,jobname text);
                    CREATE TABLE cron.job_run_details (jobid bigint,runid bigint PRIMARY KEY,job_pid integer,
                        database text,username text,command text,status text,return_message text,
                        start_time timestamptz,end_time timestamptz);
                    REVOKE ALL ON SCHEMA cron FROM PUBLIC;
                    REVOKE ALL ON TABLE cron.job,cron.job_run_details FROM PUBLIC;
                """)
                psql(name, f"SELECT fdd.join_group('g1', 'host={HOST} port={ports['mmr1']} "
                     "user=postgres dbname=postgres',true,'data-only');")
        psql("mmr1", """
            INSERT INTO sql_parse_stability.route_t(id,value) VALUES (1,10),(2,20);
            INSERT INTO sql_parse_stability.route_t(id,value) SELECT id,0 FROM generate_series(1001,11000) id;
        """)
        deadline = time.monotonic() + 60
        while True:
            result = subprocess.check_output([
                binaries / "psql", "-XwAt", "-h", HOST, "-p", str(ports["mmr2"]), "-U", "postgres",
                "-d", "postgres", "-c", "SELECT count(*) FROM sql_parse_stability.route_t",
            ], text=True, timeout=10).strip()
            if result == "10002":
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("上游 SQL Parse 夹具未在 60 秒内复制到 MMR2")
            time.sleep(0.5)
        psql("mmr1", "SELECT cron.schedule('test_bench','0 * * * *','CALL test_bench();');")
        for name, primary in NODES.items():
            if primary:
                pg(name, "pg_basebackup", "-d", f"host={HOST} port={ports[primary]} user=replicator "
                   f"application_name={name}", "-w", "-F", "p", "-X", "stream", "-R", "-D", root / name)
                # pg_basebackup carries the primary's PostgreSQL configuration and its license.
                start(name, ["max_connections = 1000", "hot_standby_feedback = on",
                             "max_standby_streaming_delay = 5min"])
        yield home, ports
    if home == root / "fbase-home":
        shutil.rmtree(home)
        shutil.rmtree(root / "contrib")
