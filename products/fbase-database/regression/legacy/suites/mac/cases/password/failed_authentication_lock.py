from framework.assertions import (command_fails, command_succeeds, rows_equal,
                                  rows_with_output_contains)
from framework.steps import sql_step


USER = "fbase_regress_password_lock"
PASSWORD = "Lock-123"


def login_step(title, password, expected, assertion):
    return sql_step(title, USER, "SELECT current_user", expected, assertion,
                    password=password, command_timeout=15)


CASE = {
    "id": "mac.password.failed_authentication_lock",
    "name": "登录失败锁定、手工解锁及成功登录回显",
    "document": "安可密码和验证失效需求(陈群友).md",
    "section": "三、验证失败处理,四、登录成功回显",
    "group": "password",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sso", "setup": False,
         "restore_runtime_value": True,
         "values": {"fdb.failed_user_auth_times": "3",
                    "fdb.account_auto_thaw_time": "0",
                    "fdb.password_change_interval": "15"}, "apply": "reload",
         "purpose": "恢复账户锁定和密码有效期配置"},
        {"type": "roles", "create": [{"name": USER, "attributes": "", "login": True}]},
        {"type": "hba_password_auth", "role": USER},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True,
                     "node": "primary", "roles": ["sso"]},
    "prerequisites": ["测试账户专用 HBA 规则使用 password；其余连接仍保持原环境认证方式"],
    "steps": [
        sql_step("SSO 设置登录失败次数限制为 3", "sso",
                 "ALTER SYSTEM SET fdb.failed_user_auth_times = 3",
                 "返回 ALTER SYSTEM", command_succeeds()),
        sql_step("SSO 关闭自动解锁以稳定验证锁定状态", "sso",
                 "ALTER SYSTEM SET fdb.account_auto_thaw_time = 0",
                 "返回 ALTER SYSTEM", command_succeeds()),
        sql_step("SSO 开启密码有效期提示", "sso",
                 "ALTER SYSTEM SET fdb.password_change_interval = 15",
                 "返回 ALTER SYSTEM", command_succeeds()),
        sql_step("重载认证失败和密码有效期配置", "postgres", "SELECT pg_reload_conf()",
                 "返回 true", rows_equal([["t"]])),
        sql_step("确认登录失败次数限制", "postgres", "SHOW fdb.failed_user_auth_times",
                 "返回 3", rows_equal([["3"]])),
        sql_step("确认自动解锁已关闭", "postgres", "SHOW fdb.account_auto_thaw_time",
                 "返回 0", rows_equal([["0"]])),
        sql_step("为测试账户设置正确密码", "postgres",
                 "ALTER USER %s PASSWORD '%s'" % (USER, PASSWORD),
                 "返回 ALTER ROLE", command_succeeds()),
        login_step("第一次使用错误密码登录", "wrong-password",
                   "连接失败并提示 password authentication failed",
                   command_fails("password authentication failed")),
        login_step("第二次使用错误密码登录", "wrong-password",
                   "连接失败并提示 password authentication failed",
                   command_fails("password authentication failed")),
        sql_step("确认两次错误登录已计数但尚未锁定", "postgres",
                 "SELECT failedcount::text, idslocked::text FROM pg_catalog.fdb_mac_auth WHERE username = '%s'" % USER,
                 "返回 2|0", rows_equal([["2", "0"]])),
        login_step("第三次使用错误密码登录触发锁定", "wrong-password",
                   "连接失败并提示账户被锁定", command_fails("is blocked login")),
        sql_step("确认账户已锁定且失败次数为 3", "postgres",
                 "SELECT failedcount::text, idslocked::text FROM pg_catalog.fdb_mac_auth WHERE username = '%s'" % USER,
                 "返回 3|1", rows_equal([["3", "1"]])),
        login_step("锁定后使用正确密码仍不能登录", PASSWORD,
                   "连接失败并提示账户被锁定", command_fails("is blocked login")),
        sql_step("SSO 调用 enableuser 手工解锁账户", "sso",
                 "CALL fdb_mac.enableuser('%s')" % USER,
                 "返回 CALL 并提示解锁成功", command_succeeds()),
        sql_step("确认手工解锁清零失败计数", "postgres",
                 "SELECT failedcount::text, idslocked::text FROM pg_catalog.fdb_mac_auth WHERE username = '%s'" % USER,
                 "返回 0|0", rows_equal([["0", "0"]])),
        login_step("解锁后使用正确密码登录", PASSWORD,
                   "返回当前测试账户，且服务端回显 Password will expire at",
                   rows_with_output_contains([[USER]], "Password will expire at")),
        sql_step("确认成功登录后失败计数保持为 0", "postgres",
                 "SELECT failedcount::text, idslocked::text FROM pg_catalog.fdb_mac_auth WHERE username = '%s'" % USER,
                 "返回 0|0", rows_equal([["0", "0"]])),
    ],
    "teardown": "hba_password_auth fixture 恢复原 pg_hba.conf；roles fixture 删除测试账户；settings fixture 恢复三个配置并 reload。",
}
