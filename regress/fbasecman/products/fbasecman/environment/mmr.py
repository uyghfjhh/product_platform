from textwrap import dedent
from framework.configuration import RegressionConfig
from framework.execution.shell import LoggedShellRunner
from .topology import standby_ports

def build_mmr_setup_script(env: RegressionConfig) -> str:
    db = env.config["database"]; ports = db["ports"]
    pg = db["mmr_postgres_dir"]; root = db.get("mmr_data_root", pg)
    enable_citus = bool(db.get("enable_citus", False))
    preload_libs = "citus,fdd_mmr" if enable_citus else "fdd_mmr"
    citus_primary_cmd = (
        '    "$PG/bin/psql" -v ON_ERROR_STOP=1 -p "$p" -d postgres -U "$USER" -c "CREATE EXTENSION IF NOT EXISTS citus;"\n'
        if enable_citus else ""
    )
    p_mmr1 = ports["mmr1"]
    p_mmr2 = ports["mmr2"]
    citus_testdb_mmr1 = (
        f'    "$PG/bin/psql" -v ON_ERROR_STOP=1 -p {p_mmr1} -d test_db -U "$USER" -c "CREATE EXTENSION IF NOT EXISTS citus;" >/dev/null\n'
        if enable_citus else ""
    )
    citus_testdb_mmr2 = (
        f'    "$PG/bin/psql" -v ON_ERROR_STOP=1 -p {p_mmr2} -d test_db -U "$USER" -c "CREATE EXTENSION IF NOT EXISTS citus;" >/dev/null\n'
        if enable_citus else ""
    )
    mmr1_standbys = standby_ports(db, "mmr1")
    mmr2_standbys = standby_ports(db, "mmr2")
    mmr1_setup = "\n    ".join(
        "standby %s %s test_mmr1_s%d pg_%d" % (ports["mmr1"], port, index, 239 + index)
        for index, port in enumerate(mmr1_standbys, 1)
    )
    mmr2_setup = "\n    ".join(
        "standby %s %s test_mmr2_s%d pg_%d" % (ports["mmr2"], port, index, 249 + index)
        for index, port in enumerate(mmr2_standbys, 1)
    )
    return dedent(f"""
    set -euo pipefail
    PG="{pg}"; ROOT="{root}"; HOST="{db['mmr_host']}"; USER="{db['mmr_pg_user']}"; REPL=replicator
    mkdir -p "$ROOT"
    primary() {{ n="$1"; p="$2"; node="$3"; d="$ROOT/$n"; mkdir "$d"; "$PG/bin/initdb" -D "$d" >/dev/null 2>&1; mkdir "$d/pg_log"; cat >>"$d/postgresql.conf" <<EOF
listen_addresses='*'
port=$p
wal_level=logical
hot_standby=on
shared_preload_libraries='{preload_libs}'
track_commit_timestamp=on
fdd.running_databases='postgres,test_db'
fdd.exclude_schema='fdd,pg_catalog,information_schema'
max_prepared_transactions=200
logging_collector=on
log_directory='pg_log'
EOF
    "$PG/bin/pg_ctl" -D "$d" -l "$d/logfile" start; sleep 2
    sed -i '/127.0.0.1\\/32.*trust/ s/^/#/' "$d/pg_hba.conf"
    cat >>"$d/pg_hba.conf" <<'EOF_HBA'
host all all 127.0.0.1/32 trust
host all postgres 0.0.0.0/0 trust
host all u1 0.0.0.0/0 scram-sha-256
host all u2 0.0.0.0/0 trust
host all u11 0.0.0.0/0 scram-sha-256
host all u3 0.0.0.0/0 scram-sha-256
host all u22 0.0.0.0/0 md5
host all clear_text_user_1 0.0.0.0/0 password
host all clear_text_user_2 0.0.0.0/0 password
host all clear_text_user_3 0.0.0.0/0 password
host all md5_user_1 0.0.0.0/0 md5
host all md5_user_2 0.0.0.0/0 md5
host all scram256_user_1 0.0.0.0/0 scram-sha-256
host all scram256_user_2 0.0.0.0/0 scram-sha-256
host replication replicator 0.0.0.0/0 trust
EOF_HBA
    "$PG/bin/pg_ctl" -D "$d" -l "$d/logfile" restart >/dev/null 2>&1
    "$PG/bin/psql" -v ON_ERROR_STOP=1 -p "$p" -d postgres -U "$USER" -c "CREATE ROLE $REPL WITH REPLICATION LOGIN;"
    "$PG/bin/psql" -v ON_ERROR_STOP=1 -p "$p" -d postgres -U "$USER" -c "CREATE EXTENSION fdd_mmr;"
{citus_primary_cmd}    "$PG/bin/psql" -v ON_ERROR_STOP=1 -p "$p" -d postgres -U "$USER" -c "SELECT fdd.create_node('$node','host=$HOST port=$p user=$USER dbname=postgres');"
    }}
    primary test_mmr1 {ports['mmr1']} node1
    schema_sql="CREATE TABLE test(id int primary key,name name); CREATE TABLE large_table(id SERIAL PRIMARY KEY,name TEXT); CREATE TABLE test_prepare_packet(id SERIAL PRIMARY KEY,data TEXT,data2 TEXT,insert_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP); CREATE TABLE test_prepared_txn_errlog(id INT PRIMARY KEY,note VARCHAR(100)); CREATE TABLE t_test1(id INT PRIMARY KEY,name TEXT); CREATE TABLE t_test2(key INT PRIMARY KEY,value TEXT); CREATE TABLE t_test3(code INT PRIMARY KEY,value TEXT);"
    "$PG/bin/psql" -v ON_ERROR_STOP=1 -p {ports['mmr1']} -d postgres -U "$USER" -c "$schema_sql SELECT fdd.create_group('g1');" >/dev/null
    primary test_mmr2 {ports['mmr2']} node2
    "$PG/bin/psql" -v ON_ERROR_STOP=1 -p {ports['mmr2']} -d postgres -U "$USER" -c "$schema_sql" >/dev/null
    "$PG/bin/psql" -v ON_ERROR_STOP=1 -p {ports['mmr2']} -d postgres -U "$USER" -c "SELECT fdd.join_group('g1','host=$HOST port={ports['mmr1']} user=$USER dbname=postgres',true,'data-only');" >/dev/null
    setup_primary() {{ p="$1"
      "$PG/bin/psql" -v ON_ERROR_STOP=1 -p "$p" -d postgres -U "$USER" -c "CREATE USER u1 LOGIN PASSWORD '12345'; CREATE USER u11 LOGIN PASSWORD '12345'; CREATE USER u3 LOGIN PASSWORD '12345'; CREATE USER clear_text_user_1 LOGIN PASSWORD '12345'; CREATE USER clear_text_user_2 LOGIN PASSWORD '12345'; CREATE USER clear_text_user_3 LOGIN PASSWORD '54321';" >/dev/null
      "$PG/bin/psql" -v ON_ERROR_STOP=1 -p "$p" -d postgres -U "$USER" -c "ALTER SYSTEM SET password_encryption='md5';" >/dev/null
      "$PG/bin/psql" -v ON_ERROR_STOP=1 -p "$p" -d postgres -U "$USER" -c "SELECT pg_reload_conf();" >/dev/null
      "$PG/bin/psql" -v ON_ERROR_STOP=1 -p "$p" -d postgres -U "$USER" -c "CREATE USER u2 LOGIN PASSWORD '123456'; CREATE USER u22 LOGIN PASSWORD '123456'; CREATE USER md5_user_1 LOGIN PASSWORD 'md5_1'; CREATE USER md5_user_2 LOGIN PASSWORD 'md5_2';" >/dev/null
      "$PG/bin/psql" -v ON_ERROR_STOP=1 -p "$p" -d postgres -U "$USER" -c "ALTER SYSTEM SET password_encryption='scram-sha-256';" >/dev/null
      "$PG/bin/psql" -v ON_ERROR_STOP=1 -p "$p" -d postgres -U "$USER" -c "SELECT pg_reload_conf(); CREATE USER scram256_user_1 LOGIN PASSWORD 'scram256_1'; CREATE USER scram256_user_2 LOGIN PASSWORD 'scram256_2'; CREATE OR REPLACE VIEW public.mmr_groupinfo AS SELECT group_name::name,group_uuid::uuid FROM fdd.mmr_group; CREATE OR REPLACE VIEW public.mmr_nodestate AS SELECT node_state::fdd.mmr_node_state FROM fdd.mmr_node WHERE node_id=(SELECT node_id FROM fdd.mmr_local_node); GRANT SELECT ON public.mmr_groupinfo,public.mmr_nodestate TO u2,u11,u22,u3;" >/dev/null
    }}
    setup_primary {ports['mmr1']}
    setup_primary {ports['mmr2']}
    "$PG/bin/createdb" -p {ports['mmr1']} -U "$USER" test_db
    "$PG/bin/createdb" -p {ports['mmr2']} -U "$USER" test_db
    "$PG/bin/psql" -v ON_ERROR_STOP=1 -p {ports['mmr1']} -d test_db -U "$USER" -c "CREATE EXTENSION fdd_mmr;" >/dev/null
{citus_testdb_mmr1}    "$PG/bin/psql" -v ON_ERROR_STOP=1 -p {ports['mmr1']} -d test_db -U "$USER" -c "SELECT fdd.create_node('testdb_node1','host=$HOST port={ports['mmr1']} user=$USER dbname=test_db'); CREATE TABLE test(id INT PRIMARY KEY,name NAME); INSERT INTO test SELECT generate_series(1,10),'name'; SELECT fdd.create_group('testdb_g1');" >/dev/null
    "$PG/bin/psql" -v ON_ERROR_STOP=1 -p {ports['mmr2']} -d test_db -U "$USER" -c "CREATE EXTENSION fdd_mmr;" >/dev/null
{citus_testdb_mmr2}    "$PG/bin/psql" -p {ports['mmr2']} -d test_db -U "$USER" -c "SELECT fdd.create_node('testdb_node2','host=$HOST port={ports['mmr2']} user=$USER dbname=test_db');" >/dev/null 2>&1 || true
    "$PG/bin/psql" -p {ports['mmr2']} -d test_db -U "$USER" -c "SELECT fdd.join_group('testdb_g1','host=$HOST port={ports['mmr1']} user=$USER dbname=test_db',true,'data-only');" >/dev/null 2>&1 || true
    standby() {{ p="$1"; port="$2"; n="$3"; app="$4"; d="$ROOT/$n"; mkdir "$d"; chmod 700 "$d"; "$PG/bin/pg_basebackup" -h "$HOST" -U "$REPL" -p "$p" -w -F p -P -X stream -R -D "$d"; sed -i "/^primary_conninfo = / s/'$/ application_name=$app'/" "$d/postgresql.auto.conf"; echo "port=$port" >>"$d/postgresql.conf"; "$PG/bin/pg_ctl" -D "$d" -l "$d/logfile" start; }}
    {mmr1_setup}
    {mmr2_setup}
    """).strip()


def setup_mmr_topology(env: RegressionConfig, runner: LoggedShellRunner):
    db = env.config["database"]; ports = db["ports"]
    mmr1_standbys = standby_ports(db, "mmr1")
    mmr2_standbys = standby_ports(db, "mmr2")
    script = build_mmr_setup_script(env)
    runner.run_remote(db["mmr_pg_user"], db["mmr_host"], script, log_name="10_setup_mmr.log", timeout=1800)
    result = {"mmr1": {"host": db["mmr_host"], "port": ports["mmr1"], "role": "write_leader"}, "mmr2": {"host": db["mmr_host"], "port": ports["mmr2"], "role": "active"}}
    for cluster, configured_ports in (("mmr1", mmr1_standbys), ("mmr2", mmr2_standbys)):
        for index, port in enumerate(configured_ports, 1):
            result[f"{cluster}_standby{index}"] = {"host": db["mmr_host"], "port": port, "role": "standby"}
    return result
