from textwrap import dedent

from framework.configuration import RegressionConfig

from framework.execution.shell import LoggedShellRunner
from .topology import topology_nodes


def collect_test_context(env, runner):
    cfg = env.config
    db = cfg["database"]
    local_pg = cfg["local"]["postgres_dir"]
    ports = db["ports"]
    configured = topology_nodes(db)

    remote_script = dedent(
        f"""
        MMR_POSTGRES_DIR="{db['mmr_postgres_dir']}"
        MMR1_PORT="{ports['mmr1']}"
        MMR2_PORT="{ports['mmr2']}"
        MMRDBNAME="postgres"
        MMR_PG_USER="{db['mmr_pg_user']}"

        MMR1_U3_PASSWORD=$($MMR_POSTGRES_DIR/bin/psql -p $MMR1_PORT -d $MMRDBNAME -U $MMR_PG_USER -t -c "SELECT rolpassword FROM pg_authid WHERE rolname = 'u3';" | tr -d '[:space:]')
        MMR1_U22_PASSWORD=$($MMR_POSTGRES_DIR/bin/psql -p $MMR1_PORT -d $MMRDBNAME -U $MMR_PG_USER -t -c "SELECT rolpassword FROM pg_authid WHERE rolname = 'u22';" | tr -d '[:space:]')
        MMR1_U11_PASSWORD=$($MMR_POSTGRES_DIR/bin/psql -p $MMR1_PORT -d $MMRDBNAME -U $MMR_PG_USER -t -c "SELECT rolpassword FROM pg_authid WHERE rolname = 'u11';" | tr -d '[:space:]')
        GROUP_UUID=$($MMR_POSTGRES_DIR/bin/psql -p $MMR1_PORT -d $MMRDBNAME -U $MMR_PG_USER -t -c "SELECT group_uuid FROM fdd.mmr_group where group_name = 'g1';" | tr -d '[:space:]')

        $MMR_POSTGRES_DIR/bin/psql -p $MMR2_PORT -d $MMRDBNAME -U $MMR_PG_USER -c "alter user u3 password '$MMR1_U3_PASSWORD';"

        MMR2_U3_PASSWORD=$($MMR_POSTGRES_DIR/bin/psql -p $MMR2_PORT -d $MMRDBNAME -U $MMR_PG_USER -t -c "SELECT rolpassword FROM pg_authid WHERE rolname = 'u3';" | tr -d '[:space:]')
        MMR2_U22_PASSWORD=$($MMR_POSTGRES_DIR/bin/psql -p $MMR2_PORT -d $MMRDBNAME -U $MMR_PG_USER -t -c "SELECT rolpassword FROM pg_authid WHERE rolname = 'u22';" | tr -d '[:space:]')
        MMR2_U11_PASSWORD=$($MMR_POSTGRES_DIR/bin/psql -p $MMR2_PORT -d $MMRDBNAME -U $MMR_PG_USER -t -c "SELECT rolpassword FROM pg_authid WHERE rolname = 'u11';" | tr -d '[:space:]')
        MMR1_SYSTEM_IDENTIFIER=$($MMR_POSTGRES_DIR/bin/psql -p $MMR1_PORT -d $MMRDBNAME -U $MMR_PG_USER -t -c "SELECT system_identifier FROM pg_control_system();" | tr -d '[:space:]')

        printf "%s\\n" "$MMR1_U3_PASSWORD" "$MMR1_U22_PASSWORD" "$MMR1_U11_PASSWORD" "$MMR2_U3_PASSWORD" "$MMR2_U22_PASSWORD" "$MMR2_U11_PASSWORD" "$GROUP_UUID" "$MMR1_SYSTEM_IDENTIFIER"
        """
    ).strip()

    remote = runner.run_remote(
        db["mmr_pg_user"],
        db["mmr_host"],
        remote_script,
        log_name="94_collect_remote_context.log",
    )
    lines = [line.strip() for line in remote.stdout.splitlines() if line.strip()]

    mmr1_u3 = runner.run(
        f"""{local_pg}/bin/psql -p {ports['mmr1']} -h {db['mmr_host']} -d postgres -U {db['mmr_pg_user']} -t -c "SELECT rolpassword FROM pg_authid WHERE rolname = 'u3';" """,
        log_name="95_collect_local_mmr1_u3.log",
    ).stdout.strip()
    def query_system_id(port: int, host: str, user: str, log_name: str) -> str:
        return runner.run(
            f"""{local_pg}/bin/psql -p {port} -h {host} -d postgres -U {user} -t -c "SELECT system_identifier FROM pg_control_system();" """,
            log_name=log_name,
        ).stdout.strip()

    def query_rolepwd(role: str, log_name: str) -> str:
        return runner.run(
            f"""{local_pg}/bin/psql -p {ports['mmr1']} -h {db['mmr_host']} -d postgres -U {db['mmr_pg_user']} -t -c "SELECT rolpassword FROM pg_authid WHERE rolname = '{role}';" """,
            log_name=log_name,
        ).stdout.strip()

    system_identifiers = {
        "mmr1": lines[7] if len(lines) > 7 else "",
        "mmr2": query_system_id(ports["mmr2"], db["mmr_host"], db["mmr_pg_user"], "97_sysid_mmr2.log"),
    }
    for topology in ("mmr1", "mmr2"):
        for index, (_, port) in enumerate(configured[topology][1:], 1):
            system_identifiers["%s_standby%d" % (topology, index)] = query_system_id(
                port, db["mmr_host"], db["mmr_pg_user"], "sysid_%s_s%d.log" % (topology, index))
    # Alias rep_* to mmr1 and mmr1_standby* for backwards compatibility
    system_identifiers["rep_primary"] = system_identifiers.get("mmr1", "")
    for index in range(1, 10):
        if "mmr1_standby%d" % index in system_identifiers:
            system_identifiers["rep_standby%d" % index] = system_identifiers["mmr1_standby%d" % index]
    return {
        "group_uuid": {"g1": lines[6] if len(lines) > 6 else ""},
        "role_passwords": {
            "mmr1_u3": lines[0] if len(lines) > 0 else "",
            "mmr1_u22": lines[1] if len(lines) > 1 else "",
            "mmr1_u11": lines[2] if len(lines) > 2 else "",
            "mmr2_u3": lines[3] if len(lines) > 3 else "",
            "mmr2_u22": lines[4] if len(lines) > 4 else "",
            "mmr2_u11": lines[5] if len(lines) > 5 else "",
        },
        "system_identifiers": system_identifiers,
        "ciphertexts": {
            "md5_user_1": query_rolepwd("md5_user_1", "104_md5_user_1.log"),
            "md5_user_2": query_rolepwd("md5_user_2", "105_md5_user_2.log"),
            "scram256_user_1": query_rolepwd("scram256_user_1", "106_scram_user_1.log"),
            "scram256_user_2": query_rolepwd("scram256_user_2", "107_scram_user_2.log"),
        },
    }
