from framework.assertions import command_fails, command_succeeds, output_contains
from framework.steps import command_step


DATA = "/tmp/fbase_regress_sm3_auth_{run_id}"
AUTH_SM3 = DATA + "/auth_sm3"
SPLIT_AUTH = DATA + "/split_auth"
DEFAULT_AUTH = DATA + "/default_auth"
PGHOME = "/usr/local/fbase15.15"
PSQL = PGHOME + "/bin/psql"
PGCTL = PGHOME + "/bin/pg_ctl"
SM3_PORT = "15546"
DEFAULT_PORT = "15548"
PASSWORD = "AlterSm3-123"
PWFILE = DATA + "/initdb_password"


def shell(title, script, expected, assertion=command_succeeds(), timeout=60):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        timeout=timeout)


def psql(title, port, sql, expected, assertion=command_succeeds(), password=None):
    prefix = "env PGPASSWORD=%s " % password if password else ""
    return shell(
        title,
        "%s%s -X -v ON_ERROR_STOP=1 -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
        (prefix, PSQL, port, sql), expected, assertion)


def initdb_with_password(data_dir, auth_options):
    return (
        "umask 077; printf '%s\\n' > %s; %s/bin/initdb -D %s -U postgres %s "
        "--pwfile=%s; rm -f %s" %
        (PASSWORD, PWFILE, PGHOME, data_dir, auth_options, PWFILE, PWFILE))


CASE = {
    "id": "mac.gm.sm3_authentication",
    "name": "SM3 初始化、密码存储与认证",
    "document": "商密和TLCP认证转测.md",
    "section": "1.2",
    "group": "gm",
    "fixtures": [
        "cluster",
        {"type": "isolated_sm3_auth", "data_dir": DATA},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary"},
    "prerequisites": [
        "隔离实例使用当前 FBase 二进制与 /home/postgres/license/license.dat；当前 FDD 构建禁用 psql \\password，依用户确认以 ALTER USER 验证其同一服务端密码存储结果",
    ],
    "steps": [
        shell("清理并创建 SM3 初始化测试根目录", "rm -rf %s; mkdir -p %s" % (DATA, DATA),
              "返回 0，隔离目录为空"),
        shell("使用 --auth=sm3 和初始密码初始化数据库",
              initdb_with_password(AUTH_SM3, "--auth=sm3"),
              "返回 Success，所有本地和 TCP HBA 规则使用 sm3"),
        shell("确认 --auth=sm3 写入 SM3 密码配置和 HBA 规则",
              "grep -F 'password_encryption = sm3' %s/postgresql.conf; "
              "test \"$(grep -Ec '^(local|host).*sm3$' %s/pg_hba.conf)\" -eq 6" %
              (AUTH_SM3, AUTH_SM3),
              "输出 password_encryption = sm3，6 条默认 HBA 规则均为 sm3"),
        shell("启动 --auth=sm3 隔离实例",
              "cp /home/postgres/license/license.dat %s/license.dat; "
              "printf \"\\nlisten_addresses = '127.0.0.1'\\nport = %s\\n\" >> %s/postgresql.conf; "
              "%s -D %s -l %s/server.log -w start" %
              (AUTH_SM3, SM3_PORT, AUTH_SM3, PGCTL, AUTH_SM3, AUTH_SM3),
              "返回 server started"),
        psql("以 SM3 密码认证连接 --auth=sm3 实例", SM3_PORT, "SELECT current_user",
             "返回 postgres", output_contains("postgres"), PASSWORD),
        psql("确认初始化用户保存为 SM3 64 位摘要", SM3_PORT,
             "SELECT rolpassword ~ '^SM3[0-9a-f]{64}$' FROM pg_authid WHERE rolname = 'postgres'",
             "返回 true", output_contains("t"), PASSWORD),
        shell("使用不同的 --auth-local 和 --auth-host 初始化数据库",
              initdb_with_password(
                  SPLIT_AUTH, "--auth-local=sm3 --auth-host=scram-sha-256"),
              "返回 Success，本地 HBA 使用 sm3，TCP HBA 使用 scram-sha-256"),
        shell("确认混合认证初始化仍使用 SM3 存储且保留两类 HBA 规则",
              "grep -F 'password_encryption = sm3' %s/postgresql.conf; "
              "test \"$(grep -Ec '^local.*sm3$' %s/pg_hba.conf)\" -eq 2; "
              "test \"$(grep -Ec '^host.*scram-sha-256$' %s/pg_hba.conf)\" -eq 4" %
              (SPLIT_AUTH, SPLIT_AUTH, SPLIT_AUTH),
              "输出 SM3 存储配置，local 为 sm3、host 为 scram-sha-256"),
        shell("不指定认证参数初始化默认数据库",
              "%s/bin/initdb -D %s -U postgres --auth=trust" % (PGHOME, DEFAULT_AUTH),
              "返回 Success，password_encryption 默认 sm3，HBA 默认 trust"),
        shell("确认默认初始化的 SM3 存储配置和 trust HBA 规则",
              "grep -F 'password_encryption = sm3' %s/postgresql.conf; "
              "test \"$(grep -Ec '^(local|host).*trust$' %s/pg_hba.conf)\" -eq 6" %
              (DEFAULT_AUTH, DEFAULT_AUTH),
              "输出 password_encryption = sm3，6 条默认 HBA 规则均为 trust"),
        shell("启动默认认证隔离实例",
              "cp /home/postgres/license/license.dat %s/license.dat; "
              "printf \"\\nlisten_addresses = '127.0.0.1'\\nport = %s\\n\" >> %s/postgresql.conf; "
              "%s -D %s -l %s/server.log -w start" %
              (DEFAULT_AUTH, DEFAULT_PORT, DEFAULT_AUTH, PGCTL, DEFAULT_AUTH, DEFAULT_AUTH),
              "返回 server started"),
        psql("确认未设置密码时 rolpassword 为空", DEFAULT_PORT,
             "SELECT rolpassword IS NULL FROM pg_authid WHERE rolname = 'postgres'",
             "返回 true", output_contains("t")),
        psql("使用 ALTER USER 设置密码", DEFAULT_PORT,
             "ALTER USER postgres PASSWORD '%s'" % PASSWORD,
             "返回 ALTER ROLE"),
        psql("确认 ALTER USER 后密码保存为 SM3 64 位摘要", DEFAULT_PORT,
             "SELECT rolpassword ~ '^SM3[0-9a-f]{64}$' FROM pg_authid WHERE rolname = 'postgres'",
             "返回 true", output_contains("t")),
        shell("将默认实例 TCP HBA 配置为文档中的 SM3 认证并重载",
              "printf 'host all all 127.0.0.1/32 sm3\\n' > %s/pg_hba.conf; %s -D %s reload" %
              (DEFAULT_AUTH, PGCTL, DEFAULT_AUTH),
              "返回 server signaled，127.0.0.1 连接使用 sm3"),
        psql("使用正确 SM3 密码认证成功", DEFAULT_PORT, "SELECT current_user",
             "返回 postgres", output_contains("postgres"), PASSWORD),
        psql("使用错误 SM3 密码认证失败", DEFAULT_PORT, "SELECT current_user",
             "执行失败并提示 password authentication failed",
             command_fails("password authentication failed"), "wrong-password"),
    ],
    "teardown": "isolated_sm3_auth fixture 停止并删除三个隔离实例及其日志。",
}
