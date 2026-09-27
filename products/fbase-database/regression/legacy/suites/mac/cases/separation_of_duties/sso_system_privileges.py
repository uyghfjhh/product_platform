from framework.assertions import output_contains, rows_equal, sql_error
from framework.steps import sql_step


CASE = {
    "id": "mac.separation_of_duties.sso_system_privileges",
    "name": "SSO 系统权限限制",
    "document": "三权分立功能转测.md",
    "section": "5.1.1",
    "group": "separation_of_duties",
    "fixtures": ["cluster", "roles"],
    "requirements": {
        "plugins": ["fbase_mac"], "writable_node": True,
        "node": "primary", "roles": ["sso"],
        "settings": [
            {
                "name": "shared_preload_libraries", "contains": "fbase_mac",
                "purpose": "预加载等保插件，使三权分立权限钩子生效",
            },
            {
                "name": "fdb.separate_user", "equals": "on",
                "purpose": "启用三权分立机制",
            },
        ],
    },
    "prerequisites": [
        "mac 集群已由平台创建并处于运行状态",
        "fbase_mac 已创建并预加载",
        "数据库初始化角色 sso 存在",
    ],
    "steps": [
        sql_step(
            title="检查 SSO 的初始系统权限",
            node="primary",
            user="postgres",
            sql=(
                "SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,"
                "rolreplication,rolbypassrls,rolcanlogin "
                "FROM pg_roles WHERE rolname = 'sso'"
            ),
            expected=(
                "返回 sso|f|f|f|f|f|t：SSO 可登录，但不是超级用户，且不具备 "
                "CREATEDB、CREATEROLE、REPLICATION、BYPASSRLS 特权"
            ),
            assertion=rows_equal([["sso", "f", "f", "f", "f", "f", "t"]]),
        ),
        sql_step(
            title="SSO 不能修改自己的 LOGIN 属性",
            node="primary",
            user="sso",
            sql="ALTER USER sso LOGIN",
            expected="执行失败，SQLSTATE=42501，错误信息包含 permission denied",
            assertion=sql_error("42501", "permission denied"),
        ),
        sql_step(
            title="SSO 不能修改自己的 INHERIT 属性",
            node="primary",
            user="sso",
            sql="ALTER USER sso INHERIT",
            expected="执行失败，SQLSTATE=42501，错误信息包含 permission denied",
            assertion=sql_error("42501", "permission denied"),
        ),
        sql_step(
            title="SSO 可以修改自己的密码",
            node="primary",
            user="sso",
            sql="BEGIN; ALTER USER sso PASSWORD 'Sso12345'; ROLLBACK",
            expected="执行成功，依次返回 BEGIN、ALTER ROLE、ROLLBACK；事务回滚后不保留密码修改",
            assertion=output_contains("BEGIN", "ALTER ROLE", "ROLLBACK"),
        ),
    ],
    "teardown": "密码修改在业务步骤事务内回滚，无额外清理动作",
}
