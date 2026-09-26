from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


POLICY = "fbase_regress_lifecycle_policy"
TABLE = "fbase_regress_mac_lifecycle"
COLUMN = "fbase_regress_lifecycle_label"
U1 = "fbase_regress_lifecycle_u1"
U2 = "fbase_regress_lifecycle_u2"


CASE = {
    "id": "mac.mac.policy_lifecycle",
    "name": "MAC 策略禁用、恢复、移除及删除",
    "document": "安可强制访问控制功能转测(张娟).md",
    "section": "2.2.12-2.2.15",
    "group": "mac",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sso", "values": {"fdb.enable_mac": "on"},
         "apply": "reload", "purpose": "启用 MAC 以验证策略生命周期"},
        {"type": "roles", "create": [
            {"name": U1, "attributes": "", "login": True},
            {"name": U2, "attributes": "", "login": True},
        ], "cleanup_priority": -10},
        {"type": "mac_policy", "table": TABLE, "policy": POLICY, "column": COLUMN,
         "table_columns": "a integer PRIMARY KEY, name varchar",
         "levels": [("L1", 10), ("L2", 20)], "compartments": [("C1", 10)],
         "labels": [("L1:C1", 11), ("L2:C1", 21)]},
        {"type": "table_grants", "table": TABLE,
         "grants": {U1: "SELECT", U2: "SELECT, INSERT"}},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary",
                     "roles": ["sso"]},
    "prerequisites": [
        "策略已应用到测试表；U1 为普通标签用户，U2 为 FULL 特权用户",
    ],
    "steps": [
        sql_step("SSO 为 U1 设置 L1:C1 会话标签", "sso",
                 "SELECT fdb_mac.set_user_labels('%s', '%s', 'L1:C1', 'L1:C1', 'L1:C1', 'L1:C1', 'L1:C1')" %
                 (POLICY, U1), "U1 标签设置成功", command_succeeds()),
        sql_step("SSO 为 U2 设置等级并授予 FULL 特权", "sso",
                 "SELECT fdb_mac.set_user_levels('%s', '%s', 'L1')" % (POLICY, U2),
                 "U2 等级设置成功", command_succeeds()),
        sql_step("SSO 为 U2 授予 FULL 特权", "sso",
                 "SELECT fdb_mac.set_user_privs('%s', '%s', 'FULL')" % (POLICY, U2),
                 "FULL 特权授予成功", command_succeeds()),
        sql_step("FULL 用户写入不同标签及无效标签数据", U2,
                 "INSERT INTO %s VALUES (1, 'l1', 11), (2, 'l2', 21), (3, 'invalid', 999)" % TABLE,
                 "三行数据插入成功", command_succeeds()),
        sql_step("策略启用时 U1 只能读取匹配标签的数据", U1,
                 "SELECT a::text, name FROM %s ORDER BY a" % TABLE,
                 "仅返回 L1:C1 的一行", rows_equal([["1", "l1"]])),
        sql_step("SSO 禁用策略", "sso", "SELECT fdb_mac.disable_policy('%s')" % POLICY,
                 "策略禁用成功", command_succeeds()),
        sql_step("策略禁用后 U1 可读取全部数据", U1,
                 "SELECT a::text, name, %s::text FROM %s ORDER BY a" % (COLUMN, TABLE),
                 "返回全部三行，包括无效标签数据", rows_equal([["1", "l1", "11"], ["2", "l2", "21"], ["3", "invalid", "999"]])),
        sql_step("SSO 重新启用策略", "sso", "SELECT fdb_mac.enable_policy('%s')" % POLICY,
                 "策略启用成功", command_succeeds()),
        sql_step("策略恢复后 U1 再次仅能读取匹配标签数据", U1,
                 "SELECT a::text, name FROM %s ORDER BY a" % TABLE,
                 "仅返回 L1:C1 的一行", rows_equal([["1", "l1"]])),
        sql_step("SSO 移除表策略并保留策略列", "sso",
                 "SELECT fdb_mac.remove_table_policy('%s', 'public', '%s', false)" % (POLICY, TABLE),
                 "策略移除成功，策略列保留", command_succeeds()),
        sql_step("移除策略后 U1 可读取全部数据", U1,
                 "SELECT a::text, name FROM %s ORDER BY a" % TABLE,
                 "返回全部三行", rows_equal([["1", "l1"], ["2", "l2"], ["3", "invalid"]])),
        sql_step("确认保留的策略列仍存在", "sso",
                 "SELECT attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                 "WHERE c.relname = '%s' AND a.attname = '%s' AND a.attnum > 0 AND NOT a.attisdropped" % (TABLE, COLUMN),
                 "返回策略列名", rows_equal([[COLUMN]])),
        sql_step("重新应用策略以验证删除策略列路径", "sso",
                 "SELECT fdb_mac.apply_table_policy('%s', 'public', '%s', true)" % (POLICY, TABLE),
                 "使用保留列重新应用策略成功", command_succeeds()),
        sql_step("SSO 移除表策略并删除策略列", "sso",
                 "SELECT fdb_mac.remove_table_policy('%s', 'public', '%s', true)" % (POLICY, TABLE),
                 "策略和策略列移除成功", command_succeeds()),
        sql_step("确认策略列已删除", "sso",
                 "SELECT count(*)::text FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                 "WHERE c.relname = '%s' AND a.attname = '%s' AND a.attnum > 0 AND NOT a.attisdropped" % (TABLE, COLUMN),
                 "返回 0", rows_equal([["0"]])),
        sql_step("SSO 删除策略及其元数据", "sso", "SELECT fdb_mac.drop_policy('%s', true)" % POLICY,
                 "策略删除成功", command_succeeds()),
        sql_step("确认策略元数据和表绑定已删除", "sso",
                 "SELECT (SELECT count(*) FROM fdb_mac.policy WHERE policy_name = '%s')::text, "
                 "(SELECT count(*) FROM fdb_mac.table_policy t JOIN pg_class c ON c.oid=t.relid WHERE c.relname='%s')::text" %
                 (POLICY, TABLE), "返回 0|0", rows_equal([["0", "0"]])),
        sql_step("U1 会话不再显示已删除策略的标签", U1,
                 "SELECT * FROM fdb_mac.show_user_labels()", "返回空集", rows_equal([])),
        sql_step("U2 会话不再显示已删除策略的访问权限", U2,
                 "SELECT * FROM fdb_mac.show_user_access()", "返回空集", rows_equal([])),
    ],
    "teardown": "mac_policy fixture 允许策略已被业务步骤删除；roles fixture 删除 U1、U2；settings fixture 恢复 MAC 开关。",
}
