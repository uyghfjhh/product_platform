from framework.assertions import (command_fails, command_succeeds,
                                  output_contains)
from framework.steps import command_step


DATA = "/tmp/fbase_regress_password_expiry_{run_id}"
PORT = "15542"
PGHOME = "/usr/local/fbase15.15"
PSQL = PGHOME + "/bin/psql"
PGCTL = PGHOME + "/bin/pg_ctl"
USER = "fbase_regress_password_expiry"
INITIAL_PASSWORD = "Expiry-123"
NEW_PASSWORD = "Renew-456"


def shell(title, script, expected, assertion=command_succeeds(), timeout=60):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        timeout=timeout)


def postgres_psql(title, sql, expected, assertion=command_succeeds()):
    return shell(
        title,
        "%s -X -v ON_ERROR_STOP=1 -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
        (PSQL, PORT, sql), expected, assertion)


def user_psql(title, password, sql, expected, assertion):
    return shell(
        title,
        "env PGPASSWORD=%s %s -X -v ON_ERROR_STOP=1 -At -h 127.0.0.1 -p %s -U %s -d postgres -c %r" %
        (password, PSQL, PORT, USER, sql), expected, assertion)


CASE = {
    "id": "mac.password.change_interval",
    "name": "密码更换周期限制与改密恢复",
    "document": "安可密码和验证失效需求(陈群友).md",
    "section": "二、密码更换周期",
    "group": "password",
    "fixtures": [
        "cluster",
        "system_clock",
        {"type": "isolated_password_expiry", "data_dir": DATA},
    ],
    "requirements": {
        "plugins": ["fbase_mac"], "writable_node": True, "node": "primary",
        "system_time_control": True,
    },
    "prerequisites": [
        "当前测试用户必须具备 sudo -n true，以在用例结束时恢复主机时间；隔离实例使用本机 fbase_mac 和 license.dat",
    ],
    "steps": [
        shell("初始化密码周期隔离数据库",
              "rm -rf %s; %s/bin/initdb -D %s -U postgres --auth-local=trust --auth-host=trust" %
              (DATA, PGHOME, DATA),
              "返回 Success，隔离数据库初始化成功"),
        shell("配置预加载、密码周期和专用密码认证",
              "cp /home/postgres/license/license.dat %s/license.dat; "
              "printf \"\\nshared_preload_libraries = 'fbase_mac'\\npassword_encryption = 'sm3'\\nfdb.password_change_interval = 15\\nfdb.password_rule = 0\\nfdb.separate_user = off\\nlisten_addresses = '127.0.0.1'\\nport = %s\\nlogging_collector = on\\nlog_directory = 'log'\\n\" >> %s/postgresql.conf; "
              "printf \"local all all trust\\nhost all postgres 127.0.0.1/32 trust\\nhost all all 127.0.0.1/32 password\\n\" > %s/pg_hba.conf" %
              (DATA, PORT, DATA, DATA),
              "返回 0，配置 15 天密码周期和 password HBA"),
        shell("以当前系统时间启动隔离数据库",
              "%s -D %s -l %s/normal.log -w start" % (PGCTL, DATA, DATA),
              "返回 server started"),
        postgres_psql("安装 fbase_mac 插件", "CREATE EXTENSION fbase_mac",
                      "返回 CREATE EXTENSION"),
        postgres_psql("创建测试账户和文档中的查询表",
                      "CREATE USER %s; CREATE TABLE t1(id integer)" % USER,
                      "返回 CREATE ROLE 和 CREATE TABLE"),
        postgres_psql("向测试账户授予文档查询表的 SELECT 权限",
                      "GRANT SELECT ON t1 TO %s" % USER,
                      "返回 GRANT"),
        postgres_psql("设置测试账户最近一次密码",
                      "ALTER USER %s PASSWORD '%s'" % (USER, INITIAL_PASSWORD),
                      "返回 ALTER ROLE"),
        postgres_psql("确认密码更换周期配置为 15 天",
                      "SHOW fdb.password_change_interval", "输出 15",
                      output_contains("15")),
        postgres_psql("查看最近密码设置时间",
                      "SELECT username, pwdsettime IS NOT NULL FROM pg_catalog.fdb_mac_auth WHERE username = '%s'" % USER,
                      "输出测试账户和 true", output_contains(USER + "|t")),
        shell("停止隔离数据库后推进主机时间 16 天",
              "%s -D %s -w -m fast stop" % (PGCTL, DATA),
              "返回 server stopped"),
        {
            "type": "system_time_shift",
            "title": "将当前系统时间推进超过 15 天的密码更换周期",
            "seconds": 16 * 24 * 60 * 60,
            "expected": "系统时间推进 16 天，超过文档配置的 15 天周期",
            "assertion": command_succeeds(),
        },
        shell("以推进后的系统时间启动隔离数据库",
              "%s -D %s -l %s/expired.log -w start" % (PGCTL, DATA, DATA),
              "返回 server started"),
        user_psql("过期密码登录后回显必须改密提示", INITIAL_PASSWORD,
                  "SELECT current_user",
                  "返回当前账户并回显 Password has expired, please change it now.",
                  output_contains(USER, "Password has expired, please change it now.")),
        user_psql("密码过期后执行查询被拒绝", INITIAL_PASSWORD, "SELECT * FROM t1",
                  "执行失败并提示 The password has expired, you must change it.",
                  command_fails("The password has expired, you must change it.")),
        user_psql("密码过期后允许修改当前用户密码", INITIAL_PASSWORD,
                  "ALTER USER %s PASSWORD '%s'" % (USER, NEW_PASSWORD),
                  "返回 ALTER ROLE", command_succeeds()),
        user_psql("修改密码后最近密码设置时间已刷新", NEW_PASSWORD,
                  "SELECT username, pwdsettime > CURRENT_TIMESTAMP - INTERVAL '1 minute' FROM pg_catalog.fdb_mac_auth WHERE username = '%s'" % USER,
                  "输出测试账户和 true", output_contains(USER + "|t")),
        user_psql("修改密码后可以重新执行查询", NEW_PASSWORD, "SELECT * FROM t1",
                  "返回空结果且不再提示密码过期", command_succeeds()),
    ],
    "teardown": "isolated_password_expiry fixture 停止并删除隔离集群；system_clock fixture 恢复用例开始时的系统时间和 NTP 状态。",
}
