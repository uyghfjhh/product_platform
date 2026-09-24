"""在 pgcluster 部署完成后准备 fbasecman 回归用例的业务夹具。

通过系统 psql 执行 SQL 与拓扑/密码上下文采集，无须依赖特定外部 Python 数据库驱动库，
确保在各种测试宿主环境与 Python 版本下均能稳定执行。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


def _find_psql_bin() -> str:
    """定位系统中可用的 psql 可执行程序。"""
    bin_path = shutil.which("psql")
    if bin_path:
        return bin_path
    for candidate in ("/usr/local/fbase15.15/bin/psql", "/usr/local/pgsql/bin/psql", "/usr/bin/psql"):
        if os.path.isfile(candidate):
            return candidate
    return "psql"


def _psql_exec(host: str, port: int, database: str, user: str, sql: str, tuples_only: bool = True) -> str:
    """通过 psql 执行 SQL 语句并返回标准输出。
    
    使用标准输入 (stdin) 传递 SQL 脚本，杜绝 Shell 引号转义引起的潜在语法错误。
    """
    cmd = [
        _find_psql_bin(),
        "-h", host,
        "-p", str(port),
        "-U", user,
        "-d", database,
        "-v", "ON_ERROR_STOP=1",
    ]
    if tuples_only:
        cmd.extend(["-t", "-A"])

    proc = subprocess.run(
        cmd,
        input=sql,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() if proc.stderr else f"退出码 {proc.returncode}"
        raise RuntimeError(f"psql 执行失败 (port={port}, db={database}): {err}")
    return proc.stdout.strip()


def _scalar(host: str, port: int, database: str, user: str, query: str) -> str:
    """执行单值查询，若查询无结果或异常则返回空字符串。"""
    try:
        out = _psql_exec(host, port, database, user, query, tuples_only=True)
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        return lines[0] if lines else ""
    except Exception:
        return ""


def _run(host: str, port: int, database: str, user: str, statement: str) -> None:
    """执行无返回值的 DDL 或 DML 语句。"""
    _psql_exec(host, port, database, user, statement, tuples_only=False)


def _ensure_roles(host: str, port: int, user: str) -> None:
    """确保集群主节点上所需的业务测试角色及不同加密认证方式均已创建。"""
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
    for name, password, algorithm in roles:
        exists = _scalar(host, port, "postgres", user, f"SELECT 1 FROM pg_roles WHERE rolname='{name}';")
        if exists:
            continue
        sql = f"SET password_encryption TO '{algorithm}'; CREATE ROLE \"{name}\" LOGIN PASSWORD '{password}';"
        _run(host, port, "postgres", user, sql)


def _ensure_main_database(host: str, port: int, user: str) -> None:
    """在 postgres 库中初始化测试表、视图与授权，并创建 test_db。"""
    _ensure_roles(host, port, user)
    statements = (
        "CREATE TABLE IF NOT EXISTS test(id int primary key,name name);",
        "CREATE TABLE IF NOT EXISTS large_table(id SERIAL PRIMARY KEY,name TEXT);",
        "CREATE TABLE IF NOT EXISTS test_prepare_packet(id SERIAL PRIMARY KEY,data TEXT,data2 TEXT,insert_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);",
        "CREATE TABLE IF NOT EXISTS test_prepared_txn_errlog(id INT PRIMARY KEY,note VARCHAR(100));",
        "CREATE TABLE IF NOT EXISTS t_test1(id INT PRIMARY KEY,name TEXT);",
        "CREATE TABLE IF NOT EXISTS t_test2(key INT PRIMARY KEY,value TEXT);",
        "CREATE TABLE IF NOT EXISTS t_test3(code INT PRIMARY KEY,value TEXT);",
        "CREATE OR REPLACE VIEW public.mmr_groupinfo AS SELECT group_name::name,group_uuid::uuid FROM fdd.mmr_group;",
        "CREATE OR REPLACE VIEW public.mmr_nodestate AS SELECT node_state::fdd.mmr_node_state FROM fdd.mmr_node WHERE node_id=(SELECT node_id FROM fdd.mmr_local_node);",
        "GRANT SELECT ON public.mmr_groupinfo,public.mmr_nodestate TO u2,u11,u22,u3;",
    )
    for stmt in statements:
        _run(host, port, "postgres", user, stmt)

    db_exists = _scalar(host, port, "postgres", user, "SELECT 1 FROM pg_database WHERE datname='test_db';")
    if not db_exists:
        _run(host, port, "postgres", user, "CREATE DATABASE test_db;")


def _ensure_test_database(
    host: str, port: int, peer_port: int, user: str,
    node_name: str, create_group: bool, use_citus: bool
) -> None:
    """在 test_db 中加载拓展插件并配置 MMR 节点拓扑。"""
    _run(host, port, "test_db", user, "CREATE EXTENSION IF NOT EXISTS fdd_mmr;")
    if use_citus:
        _run(host, port, "test_db", user, "CREATE EXTENSION IF NOT EXISTS citus;")
    if create_group:
        _run(host, port, "test_db", user, "CREATE TABLE IF NOT EXISTS test(id INT PRIMARY KEY,name NAME);")
        _run(host, port, "test_db", user, "INSERT INTO test SELECT generate_series(1,10),'name' ON CONFLICT (id) DO NOTHING;")

    node_exists = _scalar(host, port, "test_db", user, f"SELECT 1 FROM fdd.mmr_node WHERE node_name='{node_name}';")
    if not node_exists:
        dsn = f"host={host} port={port} user={user} dbname=test_db"
        _run(host, port, "test_db", user, f"SELECT fdd.create_node('{node_name}','{dsn}');")

    if create_group:
        grp_exists = _scalar(host, port, "test_db", user, "SELECT 1 FROM fdd.mmr_group WHERE group_name='testdb_g1';")
        if not grp_exists:
            _run(host, port, "test_db", user, "SELECT fdd.create_group('testdb_g1');")
    else:
        grp_exists = _scalar(host, port, "test_db", user, "SELECT 1 FROM fdd.mmr_group WHERE group_name='testdb_g1';")
        if not grp_exists:
            peer = f"host={host} port={peer_port} user={user} dbname=test_db"
            _run(host, port, "test_db", user, f"SELECT fdd.join_group('testdb_g1','{peer}',true,'data-only');")


def _role_password(host: str, port: int, user: str, role: str) -> str:
    """获取指定角色的密文密码。"""
    return _scalar(host, port, "postgres", user, f"SELECT rolpassword FROM pg_authid WHERE rolname='{role}';")


def prepare(profile: Path, override: Path) -> dict:
    """核心入口: 准备测试夹具并生成测试上下文 test_context.yaml。
    
    步骤 1: 检查 mmr1 与 mmr2 主节点角色与库表
    步骤 2: 同步 u3 角色密码与 MMR g1 组配置
    步骤 3: 初始化 test_db 及其 MMR 关系
    步骤 4: 采集各节点 system_identifier 及角色密码并持久化为 test_context.yaml
    """
    deployment = yaml.safe_load(profile.read_text(encoding="utf-8"))
    regression = yaml.safe_load(override.read_text(encoding="utf-8"))
    db = regression["database"]
    host, user, ports = db["mmr_host"], db["mmr_pg_user"], db["ports"]
    mmr1, mmr2 = int(ports["mmr1"]), int(ports["mmr2"])
    use_citus = "citus" in deployment.get("mmr_clusters", {}).get("fbasecman_regress", {}).get("extensions", [])

    # 1. 检查 MMR 主节点与业务角色
    print("[fixture] 1/4 检查 MMR 主节点与业务角色", flush=True)
    _ensure_main_database(host, mmr1, user)
    _ensure_main_database(host, mmr2, user)

    # 2. 获取并同步角色密码及 MMR 组 UUID
    u3_hash = _role_password(host, mmr1, user, "u3")
    group_uuid = _scalar(host, mmr1, "postgres", user, "SELECT group_uuid FROM fdd.mmr_group WHERE group_name='g1';")
    if not group_uuid:
        raise RuntimeError("pgcluster 尚未创建 MMR 组 g1")

    if u3_hash:
        _run(host, mmr2, "postgres", user, f"ALTER ROLE u3 PASSWORD '{u3_hash}';")

    # 3. 准备 test_db 业务库与多活关联
    print("[fixture] 2/4 准备 test_db 与多活关系", flush=True)
    _ensure_test_database(host, mmr1, mmr2, user, "testdb_node1", True, use_citus)
    _ensure_test_database(host, mmr2, mmr1, user, "testdb_node2", False, use_citus)

    # 4. 采集系统标识符与角色密码信息
    print("[fixture] 3/4 采集测试上下文元数据", flush=True)
    role_passwords = {}
    ciphertexts = {}
    system_ids = {}

    for cluster, port in (("mmr1", mmr1), ("mmr2", mmr2)):
        for role in ("u3", "u22", "u11"):
            role_passwords[f"{cluster}_{role}"] = _role_password(host, port, user, role)
        if cluster == "mmr1":
            for role in ("md5_user_1", "md5_user_2", "scram256_user_1", "scram256_user_2"):
                ciphertexts[role] = _role_password(host, port, user, role)

    for cluster, port in (("mmr1", mmr1), ("mmr2", mmr2)):
        members = [(cluster, port)] + [
            (f"{cluster}_standby{index}", standby_port)
            for index, standby_port in enumerate(ports[f"{cluster}_standbys"], 1)
        ]
        for name, member_port in members:
            system_ids[name] = _scalar(host, int(member_port), "postgres", user, "SELECT system_identifier FROM pg_control_system();")

    system_ids["rep_primary"] = system_ids.get("mmr1", "")
    for index in range(1, len(ports["mmr1_standbys"]) + 1):
        system_ids[f"rep_standby{index}"] = system_ids.get(f"mmr1_standby{index}", "")

    # 5. 持久化至 test_context.yaml
    print("[fixture] 4/4 生成并持久化 test_context.yaml", flush=True)
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
    print(f"[fixture] 测试上下文已成功写入: {context_file}", flush=True)

    return {"context_file": str(context_file), "nodes": len(system_ids) - len(ports["mmr1_standbys"]) - 1}


def main() -> int:
    """CLI 执行入口。"""
    parser = argparse.ArgumentParser(description="fbasecman 业务夹具与测试上下文准备工具")
    parser.add_argument("--profile", type=Path, required=True, help="pgcluster 部署配置文件路径")
    parser.add_argument("--override", type=Path, required=True, help="regress override 配置文件路径")
    args = parser.parse_args()
    try:
        prepare(args.profile, args.override)
    except Exception as exc:
        print(f"[fixture] 执行失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
