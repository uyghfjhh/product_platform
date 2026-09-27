"""One disposable fbase_mac server for TLCP metadata-only cases."""

from framework.assertions import command_succeeds
from framework.steps import command_step


ROOT = "/tmp/fbase_regress_tlcp_session_{run_id}"
DATA = ROOT + "/data"
PORT = "15557"
CONNECTION = {"host": "127.0.0.1", "port": PORT}
PGHOME = "/usr/local/fbase15.15"
PGCTL = PGHOME + "/bin/pg_ctl"
PSQL = PGHOME + "/bin/psql"


def session_spec():
    init = (
        "mkdir -p %s; %s/bin/initdb -D %s -U postgres --auth=trust; "
        "cp /home/postgres/license/license.dat %s/license.dat; "
        "printf \"\\nshared_preload_libraries = 'fbase_mac'\\nlisten_addresses = '127.0.0.1'\\nport = %s\\n\" >> %s/postgresql.conf; "
        "%s -D %s -l %s/server.log -w start; "
        "%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres -c 'CREATE EXTENSION fbase_mac'"
        % (DATA, PGHOME, DATA, DATA, PORT, DATA, PGCTL, DATA, DATA, PSQL, PORT))
    return {
        "key": "mac_tlcp_metadata",
        "fixtures": [{"type": "isolated_tlcp_session", "data_dir": ROOT}],
        "steps": [command_step("初始化共享 TLCP 元数据隔离集群", ["sh", "-ec", init],
                                "初始化、启动并创建 fbase_mac 扩展成功",
                                command_succeeds(), report=False, timeout=90)],
    }
