from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


TABLE = "fbase_regress_audit_modify_object"
USER = "fbase_regress_audit_modify_user"
STMT = "fbase_regress_modify_stmt"
OBJECT = "fbase_regress_modify_object"


CASE = {
    "id": "mac.audit.rule_modification",
    "name": "审计语句级和对象级规则修改",
    "document": "审计功能转测（邹雪、陈群友）.md",
    "section": "5.4",
    "group": "audit",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sao", "values": {"fdb.enable_audit": "1"},
         "apply": "reload", "restore_runtime_value": True,
         "purpose": "启用审计规则修改所需的审计开关"},
        {"type": "roles", "create": [{"name": USER, "attributes": "", "login": True}],
         "cleanup_priority": -10},
        {"type": "table", "name": TABLE, "cleanup_priority": 10},
        {"type": "audit_rules", "rules": [
            {"owner": "sao", "name": STMT, "kind": "stmt"},
            {"owner": "sao", "name": OBJECT, "kind": "object"},
        ]},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary",
                     "roles": ["sao"]},
    "prerequisites": ["审计功能已开启，SAO 可设置和修改审计规则"],
    "steps": [
        sql_step("SAO 创建语句级审计规则", "sao",
                 "SELECT fdb_audit.set_audit_stmt('%s', 'UPDATE', '%s', 'ALL')" % (STMT, USER),
                 "规则创建成功", command_succeeds()),
        sql_step("SAO 创建对象级审计规则", "sao",
                 "SELECT fdb_audit.set_audit_object('%s', 'ALL', 'sso', 'ALL', 'TABLE', 'public', '%s')" %
                 (OBJECT, TABLE), "规则创建成功", command_succeeds()),
        sql_step("SAO 修改语句级规则的审计类型和时机", "sao",
                 "SELECT fdb_audit.modify_audit_stmt('%s', 'SELECT', '%s', 'SUCCESS')" % (STMT, USER),
                 "规则修改成功", command_succeeds()),
        sql_step("确认语句级规则修改结果", "sao",
                 "SELECT rule_name, rule_type, audit_type, user_name, audit_when FROM fdb_audit.audit_rules WHERE rule_name = '%s'" % STMT,
                 "返回 STMT、SELECT、测试用户、SUCCESS", rows_equal([[STMT, "STMT", "SELECT", USER, "SUCCESS"]])),
        sql_step("SAO 修改对象级规则的审计类型、用户和时机", "sao",
                 "SELECT fdb_audit.modify_audit_object('%s', 'UPDATE', '%s', 'SUCCESS', 'TABLE', 'public', '%s')" %
                 (OBJECT, USER, TABLE), "规则修改成功", command_succeeds()),
        sql_step("确认对象级规则修改结果", "sao",
                 "SELECT rule_name, rule_type, audit_type, user_name, audit_when, obj_type, obj_schema, obj_name "
                 "FROM fdb_audit.audit_rules WHERE rule_name = '%s'" % OBJECT,
                 "返回 OBJECT、UPDATE、测试用户、SUCCESS 和目标表", 
                 rows_equal([[OBJECT, "OBJECT", "UPDATE", USER, "SUCCESS", "TABLE", "public", TABLE]])),
    ],
    "teardown": "audit_rules fixture 删除两条规则；table 和普通用户 fixture 删除测试对象；settings fixture 恢复审计开关。",
}
