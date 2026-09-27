from framework.assertions import command_fails, command_succeeds
from framework.steps import command_step


DATA = "/tmp/fbase_regress_password_log_{run_id}"
PORT = "15544"
PGHOME = "/usr/local/fbase15.15"
PSQL = PGHOME + "/bin/psql"
PGCTL = PGHOME + "/bin/pg_ctl"
USER = "fbase_regress_password_log"
CONSOLE_PASSWORD = "ConsoleSecret-123"
CONSOLE_DUPLICATE = "ConsoleDuplicate-456"
FILE_PASSWORD = "FileSecret-789"
FILE_DUPLICATE = "FileDuplicate-987"


def shell(title, script, expected, assertion=command_succeeds(), timeout=60):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        timeout=timeout)


def psql(title, user, sql, expected, assertion=command_succeeds()):
    return shell(
        title,
        "%s -X -v ON_ERROR_STOP=1 -At -h 127.0.0.1 -p %s -U %s -d postgres -c %r" %
        (PSQL, PORT, user, sql), expected, assertion)


CASE = {
    "id": "mac.password.log_password_masking",
    "name": "日志和错误语句中的密码隐藏",
    "document": "安可密码和验证失效需求(陈群友).md",
    "section": "六、日志密码隐藏",
    "group": "password",
    "fixtures": [
        "cluster",
        {"type": "isolated_password_log", "data_dir": DATA},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary"},
    "prerequisites": [
        "隔离实例预加载 fbase_mac；console_capture.log 是未启用 logging_collector 时的服务端 stderr 捕获，等价保存文档中的终端输出",
    ],
    "steps": [
        shell("初始化日志密码隐藏隔离数据库",
              "rm -rf %s; %s/bin/initdb -D %s -U postgres --auth=trust" %
              (DATA, PGHOME, DATA), "返回 Success"),
        shell("配置全语句日志和服务端 stderr 捕获",
              "cp /home/postgres/license/license.dat %s/license.dat; "
              "printf \"\\nshared_preload_libraries = 'fbase_mac'\\nlog_statement = 'all'\\nlog_min_messages = info\\nlog_min_error_statement = error\\nlogging_collector = off\\nlog_destination = 'stderr'\\nlisten_addresses = '127.0.0.1'\\nport = %s\\n\" >> %s/postgresql.conf" %
              (DATA, PORT, DATA),
              "返回 0，配置 log_statement=all、log_min_messages=info"),
        shell("不启用 logging_collector 启动并捕获服务端标准错误输出",
              "%s -D %s -l %s/console_capture.log -w start" % (PGCTL, DATA, DATA),
              "返回 server started；capture 文件保存文档中的终端日志"),
        psql("安装 fbase_mac 插件", "postgres", "CREATE EXTENSION fbase_mac",
             "返回 CREATE EXTENSION"),
        psql("SSO 开启密码隐藏开关并重载", "sso",
             "ALTER SYSTEM SET fdb.password_rule = 1; SELECT pg_reload_conf()",
             "返回 ALTER SYSTEM 和 true"),
        psql("执行含明文密码的成功角色语句", "postgres",
             "CREATE ROLE %s PASSWORD '%s'" % (USER, CONSOLE_PASSWORD),
             "返回 CREATE ROLE，并写入掩码语句日志"),
        psql("执行含明文密码的失败角色语句", "postgres",
             "CREATE ROLE %s PASSWORD '%s'" % (USER, CONSOLE_DUPLICATE),
             "返回 role already exists，触发 ERROR 和 STATEMENT 日志",
             command_fails("already exists")),
        shell("确认服务端 stderr 捕获隐藏成功和失败语句中的密码",
              "grep -F \"CREATE ROLE %s PASSWORD *\" %s/console_capture.log; "
              "grep -F \"STATEMENT:  CREATE ROLE %s PASSWORD *\" %s/console_capture.log; "
              "! grep -F %s %s/console_capture.log; ! grep -F %s %s/console_capture.log" %
              (USER, DATA, USER, DATA, CONSOLE_PASSWORD, DATA, CONSOLE_DUPLICATE, DATA),
              "捕获中存在 PASSWORD * 和掩码 STATEMENT，且不存在两段明文密码"),
        shell("按文档使用 pg_ctl -l 重启并写入日志文件",
              "%s -D %s -l %s/file_capture.log -w -m fast restart" % (PGCTL, DATA, DATA),
              "返回 server started"),
        psql("在日志文件路径执行成功角色语句", "postgres",
             "ALTER USER %s PASSWORD '%s'" % (USER, FILE_PASSWORD),
             "返回 ALTER ROLE，并写入掩码语句日志"),
        psql("在日志文件路径执行失败角色语句", "postgres",
             "CREATE ROLE %s PASSWORD '%s'" % (USER, FILE_DUPLICATE),
             "返回 role already exists，触发 ERROR 和 STATEMENT 日志",
             command_fails("already exists")),
        shell("确认日志文件隐藏所有密码并保留掩码语句",
              "grep -F \"ALTER USER %s PASSWORD *\" %s/file_capture.log; "
              "grep -F \"STATEMENT:  CREATE ROLE %s PASSWORD *\" %s/file_capture.log; "
              "! grep -F %s %s/file_capture.log; ! grep -F %s %s/file_capture.log" %
              (USER, DATA, USER, DATA, FILE_PASSWORD, DATA,
               FILE_DUPLICATE, DATA),
              "文件中存在 PASSWORD *，且不存在两段明文密码"),
    ],
    "teardown": "isolated_password_log fixture 停止并删除隔离实例，同时将 console_capture.log 和 file_capture.log 保存为 case 证据。",
}
