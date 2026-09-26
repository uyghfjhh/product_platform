from framework.assertions import command_succeeds, rows_equal, sql_fails
from framework.steps import sql_step


TABLE = "fbase_regress_audit_cancel_object"
SSO_STMT = "fbase_regress_cancel_sso_stmt"
SAO_STMT = "fbase_regress_cancel_sao_stmt"
SAO_OBJECT = "fbase_regress_cancel_sao_object"


CASE = {
    "id": "mac.audit.rule_cancellation",
    "name": "审计规则取消权限与规则类型校验",
    "document": "审计功能转测（邹雪、陈群友）.md",
    "section": "5.3",
    "group": "audit",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sao", "values": {"fdb.enable_audit": "1"},
         "apply": "reload", "restore_runtime_value": True,
         "purpose": "启用审计规则取消所需的审计开关"},
        {"type": "table", "name": TABLE, "cleanup_priority": 10},
        {"type": "audit_rules", "rules": [
            {"owner": "sso", "name": SSO_STMT, "kind": "stmt"},
            {"owner": "sao", "name": SAO_STMT, "kind": "stmt"},
            {"owner": "sao", "name": SAO_OBJECT, "kind": "object"},
        ]},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary",
                     "roles": ["sso", "sao"]},
    "prerequisites": ["审计功能已开启，审计规则 UDF 和元数据表存在"],
    "steps": [
        sql_step("SSO 创建自身语句级审计规则", "sso",
                 "SELECT fdb_audit.set_audit_stmt('%s', 'SELECT', 'sao', 'FAIL')" % SSO_STMT,
                 "规则创建成功", command_succeeds()),
        sql_step("SAO 创建自身语句级审计规则", "sao",
                 "SELECT fdb_audit.set_audit_stmt('%s', 'SELECT', 'sso', 'SUCCESS')" % SAO_STMT,
                 "规则创建成功", command_succeeds()),
        sql_step("SAO 创建自身对象级审计规则", "sao",
                 "SELECT fdb_audit.set_audit_object('%s', 'SELECT', 'sso', 'SUCCESS', 'TABLE', 'public', '%s')" %
                 (SAO_OBJECT, TABLE), "规则创建成功", command_succeeds()),
        sql_step("SAO 不能取消 SSO 创建的规则", "sao",
                 "SELECT fdb_audit.cancel_audit_stmt('%s')" % SSO_STMT,
                 "执行失败，只能取消自身设置的规则", sql_fails("does not exist, please check")),
        sql_step("SSO 不能取消 SAO 创建的规则", "sso",
                 "SELECT fdb_audit.cancel_audit_stmt('%s')" % SAO_STMT,
                 "执行失败，只能取消自身设置的规则", sql_fails("does not exist, please check")),
        sql_step("SAO 不能用语句级取消函数取消对象级规则", "sao",
                 "SELECT fdb_audit.cancel_audit_stmt('%s')" % SAO_OBJECT,
                 "执行失败，对象级规则必须使用 cancel_audit_object", sql_fails("it is not stmt level")),
        sql_step("SAO 不能用对象级取消函数取消语句级规则", "sao",
                 "SELECT fdb_audit.cancel_audit_object('%s')" % SAO_STMT,
                 "执行失败，语句级规则必须使用 cancel_audit_stmt", sql_fails("it is not object level")),
        sql_step("SAO 正确取消自身对象级规则", "sao",
                 "SELECT fdb_audit.cancel_audit_object('%s')" % SAO_OBJECT,
                 "规则取消成功", command_succeeds()),
        sql_step("SAO 正确取消自身语句级规则", "sao",
                 "SELECT fdb_audit.cancel_audit_stmt('%s')" % SAO_STMT,
                 "规则取消成功", command_succeeds()),
        sql_step("SSO 正确取消自身语句级规则", "sso",
                 "SELECT fdb_audit.cancel_audit_stmt('%s')" % SSO_STMT,
                 "规则取消成功", command_succeeds()),
        sql_step("确认三条测试规则均已删除", "sao",
                 "SELECT count(*)::text FROM fdb_audit.audit_rule WHERE rule_name IN ('%s', '%s', '%s')" %
                 (SSO_STMT, SAO_STMT, SAO_OBJECT), "返回 0", rows_equal([["0"]])),
    ],
    "teardown": "audit_rules fixture 清理任何中途遗留的规则；table fixture 删除对象级规则使用的测试表；settings fixture 恢复审计开关。",
}
