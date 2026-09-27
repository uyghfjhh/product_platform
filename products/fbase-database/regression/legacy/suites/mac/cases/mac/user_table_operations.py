from framework.assertions import command_succeeds, rows_equal, sql_fails
from framework.steps import sql_step


POLICY = "fbase_regress_operations_policy"
TABLE = "fbase_regress_mac_operations"
COLUMN = "fbase_regress_operations_label"
U1 = "fbase_regress_operations_u1"
U2 = "fbase_regress_operations_u2"

LEVELS = [("L1", 10), ("L2", 20), ("L3", 30), ("L4", 40), ("L5", 50), ("L6", 60)]
COMPARTMENTS = [("C1", 10), ("C2", 20), ("C3", 30), ("C4", 40), ("C5", 50), ("C6", 60)]
LABELS = [("L1:C4", 11), ("L1:C2,C4", 12), ("L2:C2", 21), ("L3:", 22),
          ("L3:C2,C1", 23), ("L4:C2,C1", 30), ("L4:C2,C1,C5", 31),
          ("L5:C2,C1,C4", 32), ("L5:C2,C4", 33), ("L5:C2,C3", 34),
          ("L5:C2,C3,C4,C5", 40), ("L5:C1,C2,C3,C4,C5,C6", 41),
          ("L6:C2,C3,C4,C5", 42)]


def _insert(label_id):
    return sql_step("FULL 用户 U2 插入标签 ID=%s 的数据" % label_id, U2,
                    "INSERT INTO %s VALUES(%s, '%s', %s)" %
                    (TABLE, label_id, label_id, label_id), "插入成功",
                    command_succeeds())


STEPS = []
for unused_label, label_id in LABELS:
    STEPS.append(_insert(label_id))
STEPS.extend([
    sql_step("FULL 用户 U2 插入无效标签 ID=999", U2,
             "INSERT INTO %s VALUES(14, '14', 999)" % TABLE,
             "FULL 特权允许插入无效标签", command_succeeds()),
    sql_step("普通用户 U1 未指定标签插入数据", U1,
             "INSERT INTO %s(a, name) VALUES(100, '100')" % TABLE,
             "使用 U1 的默认插入标签插入成功", command_succeeds()),
    sql_step("普通用户 U1 插入无效标签 ID=999", U1,
             "INSERT INTO %s VALUES(101, '101', 999)" % TABLE,
             "执行失败，普通用户不能插入无效标签，违反 MAC 行级插入策略",
             sql_fails("row-level security policy")),
    sql_step("FULL 用户 U2 可读取 U1 插入的数据", U2,
             "SELECT a::text, name, %s::text FROM %s WHERE a = 100" % (COLUMN, TABLE),
             "返回 U1 插入的一行数据", rows_equal([["100", "100", "23"]])),
    sql_step("普通用户 U1 不能读取超过其会话读标签的数据", U1,
             "SELECT a::text, name, %s::text FROM %s WHERE a = 100" % (COLUMN, TABLE),
             "返回空集", rows_equal([])),
    sql_step("确认 U2 已插入所有有效及无效标签数据", U2,
             "SELECT a::text, %s::text FROM %s WHERE a IN (11,12,14,21,22,23,30,31,32,33,34,40,41,42) ORDER BY a" %
             (COLUMN, TABLE),
             "返回 13 个文档定义标签及无效标签 999",
             rows_equal([["11", "11"], ["12", "12"], ["14", "999"]] +
                        [[str(label_id), str(label_id)] for unused, label_id in LABELS[2:]])),
])


CASE = {
    "id": "mac.mac.user_table_operations",
    "name": "MAC 用户插入及查询策略表数据",
    "document": "安可强制访问控制功能转测(张娟).md",
    "section": "2.2.11",
    "group": "mac",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sso", "values": {"fdb.enable_mac": "on"},
         "apply": "reload", "purpose": "启用 MAC 以验证用户标签访问控制"},
        {"type": "roles", "create": [
            {"name": U1, "attributes": "", "login": True},
            {"name": U2, "attributes": "", "login": True},
        ], "cleanup_priority": -10},
        {"type": "mac_policy", "table": TABLE, "policy": POLICY, "column": COLUMN,
         "table_columns": "a integer PRIMARY KEY, name varchar",
         "levels": LEVELS, "compartments": COMPARTMENTS, "labels": LABELS},
        {"type": "table_grants", "table": TABLE,
         "grants": {U1: "SELECT, INSERT", U2: "SELECT, INSERT"}},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary",
                     "roles": ["sso"]},
    "prerequisites": [
        "策略、等级、范围、标签、用户和表授权按 2.2.1-2.2.10 的定义准备",
        "U2 持有 FULL 特权；U1 已配置文档定义的五类标签",
    ],
    "steps": [
        sql_step("SSO 为 U1 设置文档定义的五类标签", "sso",
                 "SELECT fdb_mac.set_user_labels('%s', '%s', 'L5:C1,C2,C3,C4,C5', "
                 "'L5:C1,C2,C3,C4', 'L2:C1,C2', 'L3:C1,C2', 'L2:C1,C2')" % (POLICY, U1),
                 "U1 标签设置成功", command_succeeds()),
        sql_step("SSO 为 U2 设置 L1 等级", "sso",
                 "SELECT fdb_mac.set_user_levels('%s', '%s', 'L1')" % (POLICY, U2),
                 "U2 等级设置成功", command_succeeds()),
        sql_step("SSO 为 U2 授予 FULL 特权", "sso",
                 "SELECT fdb_mac.set_user_privs('%s', '%s', 'FULL')" % (POLICY, U2),
                 "FULL 特权授予成功", command_succeeds()),
    ] + STEPS,
    "teardown": "settings fixture 恢复开关；mac_policy fixture 依次撤销绑定、删除策略和表；roles fixture 删除 U1、U2。",
}
