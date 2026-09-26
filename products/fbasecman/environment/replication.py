from textwrap import dedent

from framework.configuration import RegressionConfig
from .topology import standby_ports

from framework.execution.shell import LoggedShellRunner


def build_replication_setup_script(env: RegressionConfig) -> str:
    cfg = env.config
    db = cfg["database"]
    ports = db["ports"]
    rep_standbys = standby_ports(db, "rep")
    rep_dir = db["rep_postgres_dir"]
    rep_data_root = db.get("rep_data_root", rep_dir)
    enable_citus = bool(db.get("enable_citus", False))
    preload_conf = "shared_preload_libraries = 'citus'\n" if enable_citus else ""
    citus_create_cmd = (
        '        $REP_POSTGRES_DIR/bin/psql -v ON_ERROR_STOP=1 -p $REP_PORT -d $REPDBNAME -U $REP_PG_USER -c "CREATE EXTENSION IF NOT EXISTS citus;" > /dev/null 2>&1\n'
        if enable_citus else ""
    )

    return dedent(
        f"""
        set -euo pipefail
        REP_HOST="{db['rep_host']}"
        REP_PG_USER="{db['rep_pg_user']}"
        REP_POSTGRES_DIR="{rep_dir}"
        REP_DATA_ROOT="{rep_data_root}"
        REPDBNAME="postgres"
        USER_U1="u1"
        USER_U11="u11"
        USER_U2="u2"
        USER_U22="u22"
        USER_U3="u3"
        REPLICATION_USER="replicator"

        REPPGDATA="$REP_DATA_ROOT/test_rep"
        REP_STANDBY1_PGDATA="$REP_DATA_ROOT/test_rep_s1"
        REP_STANDBY2_PGDATA="$REP_DATA_ROOT/test_rep_s2"
        REP_PORT="{ports['rep_primary']}"
        REP_STANDBY_PORTS="{' '.join(str(port) for port in rep_standbys)}"

        mkdir -p "$REP_DATA_ROOT"

        echo '========================================'
        echo '>>> [7/8] 正在初始化并启动 REP 主库...'
        echo '========================================'
        mkdir "$REPPGDATA"
        sleep 2
        $REP_POSTGRES_DIR/bin/initdb -D "$REPPGDATA" > /dev/null 2>&1
        mkdir "$REPPGDATA/pg_log"
        cat >> "$REPPGDATA/postgresql.conf" <<'EOF'
listen_addresses = '*'
port = {ports['rep_primary']}
wal_level = logical
hot_standby = on
{preload_conf}track_commit_timestamp = on
max_prepared_transactions = 200
logging_collector = on
log_directory = 'pg_log'
log_statement = 'all'
EOF
        $REP_POSTGRES_DIR/bin/pg_ctl -D "$REPPGDATA" -l "$REPPGDATA/logfile" start
        sleep 2
{citus_create_cmd}        $REP_POSTGRES_DIR/bin/psql -v ON_ERROR_STOP=1 -p $REP_PORT -d $REPDBNAME -U $REP_PG_USER -c "CREATE ROLE $REPLICATION_USER WITH REPLICATION LOGIN; CREATE TABLE test(id int primary key,name name); insert into test values(generate_series(1, 10),'name'); CREATE TABLE IF NOT EXISTS large_table (id SERIAL PRIMARY KEY, name TEXT); CREATE TABLE test_prepare_packet(id SERIAL PRIMARY KEY,data TEXT DEFAULT NULL,data2 TEXT DEFAULT NULL,insert_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP); CREATE TABLE IF NOT EXISTS test_prepared_txn_errlog(id INT PRIMARY KEY, note VARCHAR(100)); CREATE TABLE t_test1 (id INT PRIMARY KEY, name TEXT); INSERT INTO t_test1 VALUES (1, 'test1_row1'), (2, 'test1_row2'); CREATE TABLE t_test2 (key INT PRIMARY KEY, value TEXT); INSERT INTO t_test2 VALUES (10, 'test2_row1'), (20, 'test2_row2'); CREATE TABLE t_test3 (code INT PRIMARY KEY, value TEXT); INSERT INTO t_test3 VALUES (100, 'test3_row1'), (200, 'test3_row2'); create user clear_text_user_1 login password '12345'; create user clear_text_user_2 login password '12345'; create user clear_text_user_3 login password '54321'; create user $USER_U1 login password '12345'; create user $USER_U11 login password '12345'; create user $USER_U3 login password '12345';" > /dev/null 2>&1
        $REP_POSTGRES_DIR/bin/psql -v ON_ERROR_STOP=1 -p $REP_PORT -d $REPDBNAME -U $REP_PG_USER -c "alter system set password_encryption = 'md5';" > /dev/null 2>&1
        $REP_POSTGRES_DIR/bin/psql -v ON_ERROR_STOP=1 -p $REP_PORT -d $REPDBNAME -U $REP_PG_USER -c "select pg_reload_conf();" > /dev/null 2>&1
        $REP_POSTGRES_DIR/bin/psql -v ON_ERROR_STOP=1 -p $REP_PORT -d $REPDBNAME -U $REP_PG_USER -c "create user $USER_U2 login password '123456'; create user md5_user_1 login password 'md5_1'; create user md5_user_2 login password 'md5_2'; create user $USER_U22 login password '123456';" > /dev/null 2>&1
        $REP_POSTGRES_DIR/bin/psql -v ON_ERROR_STOP=1 -p $REP_PORT -d $REPDBNAME -U $REP_PG_USER -c "alter system set password_encryption = 'scram-sha-256';" > /dev/null 2>&1
        $REP_POSTGRES_DIR/bin/psql -v ON_ERROR_STOP=1 -p $REP_PORT -d $REPDBNAME -U $REP_PG_USER -c "select pg_reload_conf();" > /dev/null 2>&1
        $REP_POSTGRES_DIR/bin/psql -v ON_ERROR_STOP=1 -p $REP_PORT -d $REPDBNAME -U $REP_PG_USER -c "create user scram256_user_1 login password 'scram256_1'; create user scram256_user_2 login password 'scram256_2';" > /dev/null 2>&1
        sed -i '/host[[:space:]]\\+all[[:space:]]\\+all[[:space:]]\\+127.0.0.1\\/32[[:space:]]\\+trust/ s/^/#/' "$REPPGDATA/pg_hba.conf"
        cat >> "$REPPGDATA/pg_hba.conf" <<'EOF'
host    all             {db['rep_pg_user']}                   0.0.0.0/0               trust
host    all             u1                                    0.0.0.0/0               scram-sha-256
host    all             u2                                    0.0.0.0/0               trust
host    all             u11                                   0.0.0.0/0               scram-sha-256
host    all             u3                                    0.0.0.0/0               scram-sha-256
host    all             u22                                   0.0.0.0/0               md5
host    all             clear_text_user_1                     0.0.0.0/0               password
host    all             clear_text_user_2                     0.0.0.0/0               password
host    all             clear_text_user_3                     0.0.0.0/0               password
host    all             md5_user_1                            0.0.0.0/0               md5
host    all             md5_user_2                            0.0.0.0/0               md5
host    all             scram256_user_1                       0.0.0.0/0               scram-sha-256
host    all             scram256_user_2                       0.0.0.0/0               scram-sha-256
host    replication     replicator                            0.0.0.0/0               trust
EOF
        $REP_POSTGRES_DIR/bin/pg_ctl -D "$REPPGDATA" -l "$REPPGDATA/logfile" restart > /dev/null 2>&1

        echo '========================================'
        echo '>>> 正在创建并启动 REP 的流复制备库...'
        echo '========================================'
        index=1
        for standby_port in $REP_STANDBY_PORTS; do
            standby_data="$REP_DATA_ROOT/test_rep_s$index"
            mkdir "$standby_data"; chmod 700 "$standby_data"
            $REP_POSTGRES_DIR/bin/pg_basebackup -h $REP_HOST -U $REPLICATION_USER -p $REP_PORT -w -F p -P -X stream -R -D "$standby_data"
            sed -i "/^primary_conninfo = / s/'$/ application_name=pg_$((220 + index * 10))'/" "$standby_data/postgresql.auto.conf"
            echo "port = $standby_port" >> "$standby_data/postgresql.conf"
            $REP_POSTGRES_DIR/bin/pg_ctl -D "$standby_data" -l "$standby_data/logfile" start
            index=$((index + 1))
        done
        """
    ).strip()


def setup_replication_topology(env, runner):
    db = env.config["database"]
    ports = db["ports"]
    rep_standbys = standby_ports(db, "rep")
    script = build_replication_setup_script(env)
    runner.run_remote(
        db["rep_pg_user"], db["rep_host"], script,
        log_name="11_setup_replication.log", timeout=1200,
    )
    return {
        "rep_primary": {"host": db["rep_host"], "port": ports["rep_primary"], "role": "primary"},
        **{"rep_standby%d" % index: {"host": db["rep_host"], "port": port, "role": "standby"}
           for index, port in enumerate(rep_standbys, 1)},
    }
