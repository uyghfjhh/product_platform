"""在 pgcluster 部署完成后准备 fbasecman 回归用例的业务夹具。"""

import argparse
import os
import sys
from pathlib import Path

import psycopg
import yaml
from psycopg import sql


def _scalar(connection, query: str, parameters=()):
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)
        row = cursor.fetchone()
        return row[0] if row else None


def _run(connection, statement: str, parameters=()) -> None:
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)


def _connect(host: str, port: int, database: str = "postgres", user: str = "postgres"):
    return psycopg.connect(
        host=host, port=port, dbname=database, user=user, connect_timeout=8,
        options="-c statement_timeout=30000", autocommit=True,
    )


def _ensure_roles(connection) -> None:
    roles = (
        ("u1", "12345", "scram-sha-256"),
        ("u11", "12345", "scram-sha-256"),
        ("u3", "12345", "scram-sha-256"),
        ("clear_text_user_1", "12345", "scram-sha-256"),
        ("clear_text_user_2", "12345", "scram-sha-256"),
        ("clear_text_user_3", "54321", "scram-sha-256"),
        ("u2", "123456", "md5"),
        ("u22", "123456", "md5"),
        ("md5_user_1", "md5_1", "md5"),
        ("md5_user_2", "md5_2", "md5"),
        ("scram256_user_1", "scram256_1", "scram-sha-256"),
        ("scram256_user_2", "scram256_2", "scram-sha-256"),
    )
    with connection.cursor() as cursor:
        for name, password, algorithm in roles:
            if _scalar(connection, "SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)):
                continue
            cursor.execute(sql.SQL("SET password_encryption TO {}").format(sql.Literal(algorithm)))
            cursor.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(name), sql.Literal(password)
                )
            )


def _ensure_main_database(host: str, port: int, user: str) -> None:
    with _connect(host, port, user=user) as connection:
        _ensure_roles(connection)
        for statement in (
            "CREATE TABLE IF NOT EXISTS test(id int primary key,name name)",
            "CREATE TABLE IF NOT EXISTS large_table(id SERIAL PRIMARY KEY,name TEXT)",
            "CREATE TABLE IF NOT EXISTS test_prepare_packet(id SERIAL PRIMARY KEY,data TEXT,data2 TEXT,insert_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS test_prepared_txn_errlog(id INT PRIMARY KEY,note VARCHAR(100))",
            "CREATE TABLE IF NOT EXISTS t_test1(id INT PRIMARY KEY,name TEXT)",
            "CREATE TABLE IF NOT EXISTS t_test2(key INT PRIMARY KEY,value TEXT)",
            "CREATE TABLE IF NOT EXISTS t_test3(code INT PRIMARY KEY,value TEXT)",
            "CREATE OR REPLACE VIEW public.mmr_groupinfo AS SELECT group_name::name,group_uuid::uuid FROM fdd.mmr_group",
            "CREATE OR REPLACE VIEW public.mmr_nodestate AS SELECT node_state::fdd.mmr_node_state FROM fdd.mmr_node WHERE node_id=(SELECT node_id FROM fdd.mmr_local_node)",
            "GRANT SELECT ON public.mmr_groupinfo,public.mmr_nodestate TO u2,u11,u22,u3",
        ):
            _run(connection, statement)
        if not _scalar(connection, "SELECT 1 FROM pg_database WHERE datname='test_db'"):
            _run(connection, "CREATE DATABASE test_db")


def _ensure_test_database(host: str, port: int, peer_port: int, user: str,
                          node_name: str, create_group: bool, use_citus: bool) -> None:
    with _connect(host, port, "test_db", user) as connection:
        _run(connection, "CREATE EXTENSION IF NOT EXISTS fdd_mmr")
        if use_citus:
            _run(connection, "CREATE EXTENSION IF NOT EXISTS citus")
        if create_group:
            _run(connection, "CREATE TABLE IF NOT EXISTS test(id INT PRIMARY KEY,name NAME)")
            _run(connection, "INSERT INTO test SELECT generate_series(1,10),'name' ON CONFLICT (id) DO NOTHING")
        if not _scalar(connection, "SELECT 1 FROM fdd.mmr_node WHERE node_name=%s", (node_name,)):
            dsn = f"host={host} port={port} user={user} dbname=test_db"
            _run(connection, "SELECT fdd.create_node(%s,%s)", (node_name, dsn))
        if create_group:
            if not _scalar(connection, "SELECT 1 FROM fdd.mmr_group WHERE group_name='testdb_g1'"):
                _run(connection, "SELECT fdd.create_group('testdb_g1')")
        elif not _scalar(connection, "SELECT 1 FROM fdd.mmr_group WHERE group_name='testdb_g1'"):
            peer = f"host={host} port={peer_port} user={user} dbname=test_db"
            _run(connection, "SELECT fdd.join_group('testdb_g1',%s,true,'data-only')", (peer,))


def _role_password(connection, role: str) -> str:
    return str(_scalar(connection, "SELECT rolpassword FROM pg_authid WHERE rolname=%s", (role,)) or "")


def prepare(profile: Path, override: Path) -> dict:
    deployment = yaml.safe_load(profile.read_text(encoding="utf-8"))
    regression = yaml.safe_load(override.read_text(encoding="utf-8"))
    db = regression["database"]
    host, user, ports = db["mmr_host"], db["mmr_pg_user"], db["ports"]
    mmr1, mmr2 = int(ports["mmr1"]), int(ports["mmr2"])
    use_citus = "citus" in deployment["mmr_clusters"]["fbasecman_regress"]["extensions"]

    print("[fixture] 检查 MMR 主节点与业务角色", flush=True)
    _ensure_main_database(host, mmr1, user)
    _ensure_main_database(host, mmr2, user)
    with _connect(host, mmr1, user=user) as connection:
        u3_hash = _role_password(connection, "u3")
        group_uuid = _scalar(connection, "SELECT group_uuid FROM fdd.mmr_group WHERE group_name='g1'")
        if not group_uuid:
            raise RuntimeError("pgcluster 尚未创建 MMR 组 g1")
    with _connect(host, mmr2, user=user) as connection:
        if u3_hash:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("ALTER ROLE u3 PASSWORD {}").format(sql.Literal(u3_hash)))

    print("[fixture] 准备 test_db 与多活关系", flush=True)
    _ensure_test_database(host, mmr1, mmr2, user, "testdb_node1", True, use_citus)
    _ensure_test_database(host, mmr2, mmr1, user, "testdb_node2", False, use_citus)

    print("[fixture] 采集测试上下文", flush=True)
    role_passwords = {}
    ciphertexts = {}
    system_ids = {}
    for cluster, port in (("mmr1", mmr1), ("mmr2", mmr2)):
        with _connect(host, port, user=user) as connection:
            for role in ("u3", "u22", "u11"):
                role_passwords[f"{cluster}_{role}"] = _role_password(connection, role)
            if cluster == "mmr1":
                for role in ("md5_user_1", "md5_user_2", "scram256_user_1", "scram256_user_2"):
                    ciphertexts[role] = _role_password(connection, role)
    for cluster, port in (("mmr1", mmr1), ("mmr2", mmr2)):
        members = [(cluster, port)] + [
            (f"{cluster}_standby{index}", standby_port)
            for index, standby_port in enumerate(ports[f"{cluster}_standbys"], 1)
        ]
        for name, member_port in members:
            with _connect(host, int(member_port), user=user) as connection:
                system_ids[name] = str(_scalar(connection, "SELECT system_identifier FROM pg_control_system()") or "")
    system_ids["rep_primary"] = system_ids["mmr1"]
    for index in range(1, len(ports["mmr1_standbys"]) + 1):
        system_ids[f"rep_standby{index}"] = system_ids[f"mmr1_standby{index}"]

    context = {
        "group_uuid": {"g1": str(group_uuid)},
        "role_passwords": role_passwords,
        "system_identifiers": system_ids,
        "ciphertexts": ciphertexts,
    }
    context_file = Path(regression["framework"]["environment_output_dir"]) / "test_context.yaml"
    context_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = context_file.with_suffix(".yaml.tmp")
    temporary.write_text(yaml.safe_dump(context, allow_unicode=True), encoding="utf-8")
    os.replace(temporary, context_file)
    print(f"[fixture] 测试上下文已写入 {context_file}", flush=True)
    return {"context_file": str(context_file), "nodes": len(system_ids) - len(ports["mmr1_standbys"]) - 1}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--override", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.profile, args.override)
    except Exception as exc:
        print(f"[fixture] 失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
