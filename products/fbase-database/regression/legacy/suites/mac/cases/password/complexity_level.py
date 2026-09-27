from framework.assertions import command_succeeds, rows_equal, sql_fails
from framework.steps import sql_step


USER = "fbase_regress_password_level"


CASE = {
    "id": "mac.password.complexity_level",
    "name": "等级模式密码复杂度校验",
    "document": "安可密码和验证失效需求(陈群友).md",
    "section": "一、密码复杂度",
    "group": "password",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sso", "setup": False, "restore_runtime_value": True,
         "values": {"fdb.password_mode": "level", "fdb.password_rule": "5"}, "apply": "reload",
         "purpose": "恢复密码复杂度模式和等级的用例前状态"},
        {"type": "roles", "create": [{"name": USER, "attributes": "", "login": True}]},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary", "roles": ["sso"]},
    "prerequisites": ["SSO 有权设置密码复杂度配置，测试用户存在"],
    "steps": [
        sql_step("SSO 设置密码复杂度模式为 level", "sso", "ALTER SYSTEM SET fdb.password_mode = 'level'",
                 "返回 ALTER SYSTEM", command_succeeds()),
        sql_step("SSO 设置密码复杂度等级为 5", "sso", "ALTER SYSTEM SET fdb.password_rule = 5",
                 "返回 ALTER SYSTEM", command_succeeds()),
        {"type": "cluster_action", "title": "重载密码复杂度配置", "action": "reload", "expected": "reload 执行成功", "assertion": command_succeeds()},
        sql_step("确认密码模式为 level", "sso", "SHOW fdb.password_mode", "返回 level", rows_equal([["level"]])),
        sql_step("确认密码复杂度等级为 5", "sso", "SHOW fdb.password_rule", "返回 5", rows_equal([["5"]])),
        sql_step("密码不能与用户名相同", "postgres", "ALTER USER %s PASSWORD '%s'" % (USER, USER),
                 "执行失败，密码不能与用户名相同", sql_fails("password must not same as user name")),
        sql_step("密码长度至少 8 字符", "postgres", "ALTER USER %s PASSWORD 'a'" % USER,
                 "执行失败，密码过短", sql_fails("password is too short, at least 8 characters")),
        sql_step("密码必须包含大写字母", "postgres", "ALTER USER %s PASSWORD '1234567890'" % USER,
                 "执行失败，缺少大写字母", sql_fails("password must contain upper letter")),
        sql_step("密码必须包含小写字母", "postgres", "ALTER USER %s PASSWORD 'M123456789'" % USER,
                 "执行失败，缺少小写字母", sql_fails("password must contain lower letter")),
        sql_step("密码必须包含数字", "postgres", "ALTER USER %s PASSWORD 'Mabcdefghij'" % USER,
                 "执行失败，缺少数字", sql_fails("password must contain digital letters")),
        sql_step("密码必须包含特殊字符", "postgres", "ALTER USER %s PASSWORD 'Mirror1234'" % USER,
                 "执行失败，缺少特殊字符", sql_fails("password must contain special letters")),
        sql_step("符合等级 5 的密码设置成功", "postgres", "ALTER USER %s PASSWORD 'Mirror-123'" % USER,
                 "返回 ALTER ROLE", command_succeeds()),
    ],
    "teardown": "roles fixture 删除测试用户；settings fixture 恢复密码复杂度配置并 reload。",
}
