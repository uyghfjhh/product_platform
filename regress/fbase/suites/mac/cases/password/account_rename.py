from framework.assertions import output_contains_text, rows_equal
from framework.steps import sql_step


OLD = "fbase_regress_password_old"
NEW = "fbase_regress_password_new"


CASE = {
    "id": "mac.password.account_rename",
    "name": "账户重命名同步认证元数据",
    "document": "安可密码和验证失效需求(陈群友).md",
    "section": "七、其它测试项",
    "group": "password",
    "fixtures": ["cluster", {"type": "roles", "create": [{"name": OLD, "attributes": "", "login": True}]}],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary"},
    "prerequisites": ["普通登录测试账户存在；重命名在事务内执行，结束后回滚"],
    "steps": [
        sql_step("确认原账户已有认证元数据", "postgres",
                 "SELECT count(*)::text FROM pg_catalog.fdb_mac_auth WHERE username = '%s'" % OLD,
                 "返回 1", rows_equal([["1"]])),
        sql_step("重命名账户并检查认证元数据名称同步", "postgres",
                 "BEGIN; ALTER USER %s RENAME TO %s; SELECT username FROM pg_catalog.fdb_mac_auth WHERE username = '%s'; ROLLBACK" % (OLD, NEW, NEW),
                 "返回 ALTER ROLE，认证元数据用户名变为新名称，最后 ROLLBACK",
                 output_contains_text("ALTER ROLE", NEW, "ROLLBACK")),
        sql_step("确认回滚后原账户认证元数据仍存在", "postgres",
                 "SELECT count(*)::text FROM pg_catalog.fdb_mac_auth WHERE username = '%s'" % OLD,
                 "返回 1", rows_equal([["1"]])),
    ],
    "teardown": "roles fixture 删除原测试账户；业务重命名步骤在事务内回滚。",
}
