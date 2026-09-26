from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


POLICY = "fbase_regress_definition_policy"
COLUMN = "fbase_regress_definition_label"
TABLE = "fbase_regress_mac_definition"

LEVELS = [("L1", 10), ("L2", 20), ("L3", 30),
          ("L4", 40), ("L5", 50), ("L6", 60)]
COMPARTMENTS = [("C1", 10), ("C2", 20), ("C3", 30),
                ("C4", 40), ("C5", 50), ("C6", 60)]
LABELS = [
    ("L1:C4", 11), ("L1:C2,C4", 12), ("L2:C2", 21), ("L3:", 22),
    ("L3:C2,C1", 23), ("L4:C2,C1", 30), ("L4:C2,C1,C5", 31),
    ("L5:C2,C1,C4", 32), ("L5:C2,C4", 33), ("L5:C2,C3", 34),
    ("L5:C2,C3,C4,C5", 40), ("L5:C1,C2,C3,C4,C5,C6", 41),
    ("L6:C2,C3,C4,C5", 42),
]
NORMALIZED_LABELS = [
    ("L1:C4", 11), ("L1:C2,C4", 12), ("L2:C2", 21), ("L3:", 22),
    ("L3:C1,C2", 23), ("L4:C1,C2", 30), ("L4:C1,C2,C5", 31),
    ("L5:C1,C2,C4", 32), ("L5:C2,C4", 33), ("L5:C2,C3", 34),
    ("L5:C2,C3,C4,C5", 40), ("L5:C1,C2,C3,C4,C5,C6", 41),
    ("L6:C2,C3,C4,C5", 42),
]


def _call(title, sql, expected):
    return sql_step(title=title, user="sso", sql=sql, expected=expected,
                    assertion=command_succeeds())


def _binding_check(title, expected):
    return sql_step(
        title=title, user="sso",
        sql=("SELECT p.policy_name, c.relname, a.attname "
             "FROM fdb_mac.table_policy t "
             "JOIN fdb_mac.policy p ON p.polid = t.polid "
             "JOIN pg_class c ON c.oid = t.relid "
             "JOIN pg_attribute a ON a.attrelid = c.oid "
             "WHERE p.policy_name = '%s' AND c.relname = '%s' "
             "AND a.attname = '%s' AND a.attnum > 0 AND NOT a.attisdropped") %
            (POLICY, TABLE, COLUMN),
        expected=expected,
        assertion=rows_equal([[POLICY, TABLE, COLUMN]]),
    )


STEPS = [
    _call("SSO 开启 MAC 功能", "ALTER SYSTEM SET fdb.enable_mac = on",
          "ALTER SYSTEM 执行成功"),
    {
        "type": "cluster_action", "title": "重载配置使 MAC 开关生效",
        "action": "reload", "expected": "reload 执行成功",
        "assertion": command_succeeds(),
    },
    sql_step("确认 MAC 功能已开启", "sso", "SHOW fdb.enable_mac", "返回 on",
             rows_equal([["on"]])),
    _call("创建 MAC 策略", "SELECT fdb_mac.create_policy('%s', '%s')" %
          (POLICY, COLUMN), "策略创建成功"),
    sql_step(
        "确认策略名称和策略列", "sso",
        "SELECT policy_name, policy_col_name, policy_col_hide, policy_enable "
        "FROM fdb_mac.policy WHERE policy_name = '%s'" % POLICY,
        "返回策略、策略列，且未隐藏并已启用",
        rows_equal([[POLICY, COLUMN, "f", "t"]]),
    ),
]

for name, identifier in LEVELS:
    STEPS.append(_call("创建等级 %s（ID=%s）" % (name, identifier),
                       "SELECT fdb_mac.create_level('%s', '%s', %s)" %
                       (POLICY, name, identifier), "等级创建成功"))
STEPS.append(sql_step(
    "确认 6 个等级均已创建", "sso",
    "SELECT level_name, levelid::text FROM fdb_mac.level l "
    "JOIN fdb_mac.policy p ON p.polid = l.polid "
    "WHERE p.policy_name = '%s' ORDER BY levelid" % POLICY,
    "依次返回 L1-L6 及 ID 10-60",
    rows_equal([[name, str(identifier)] for name, identifier in LEVELS]),
))

for name, identifier in COMPARTMENTS:
    STEPS.append(_call("创建范围 %s（ID=%s）" % (name, identifier),
                       "SELECT fdb_mac.create_compartment('%s', '%s', %s)" %
                       (POLICY, name, identifier), "范围创建成功"))
STEPS.append(sql_step(
    "确认 6 个范围均已创建", "sso",
    "SELECT comp_name, compid::text FROM fdb_mac.compartment c "
    "JOIN fdb_mac.policy p ON p.polid = c.polid "
    "WHERE p.policy_name = '%s' ORDER BY compid" % POLICY,
    "依次返回 C1-C6 及 ID 10-60",
    rows_equal([[name, str(identifier)] for name, identifier in COMPARTMENTS]),
))

for label, identifier in LABELS:
    STEPS.append(_call("创建标签 %s（ID=%s）" % (label, identifier),
                       "SELECT fdb_mac.create_label('%s', '%s', %s)" %
                       (POLICY, label, identifier), "标签创建成功"))
STEPS.append(sql_step(
    "确认 13 个标签均已创建", "sso",
    "SELECT label, labelid::text FROM fdb_mac.label b "
    "JOIN fdb_mac.policy p ON p.polid = b.polid "
    "WHERE p.policy_name = '%s' ORDER BY labelid" % POLICY,
    "返回 13 个标签及其 ID；范围名称按产品规则规范排序",
    rows_equal([[label, str(identifier)] for label, identifier in NORMALIZED_LABELS]),
))

STEPS.extend([
    sql_step("DBA 创建待应用策略的测试表", "postgres",
             "CREATE TABLE %s (a integer, name varchar)" % TABLE,
             "返回 CREATE TABLE", command_succeeds()),
    _call("首次应用策略，禁止复用同名列",
          "SELECT fdb_mac.apply_table_policy('%s', 'public', '%s', false)" %
          (POLICY, TABLE), "策略应用成功，并自动新增策略列"),
    _binding_check("确认首次应用已绑定策略并新增策略列",
                   "返回策略、测试表和自动新增的策略列"),
    _call("撤销策略但保留策略列以验证复用路径",
          "SELECT fdb_mac.remove_table_policy('%s', 'public', '%s', false)" %
          (POLICY, TABLE), "策略绑定撤销成功，策略列保留"),
    sql_step("确认策略绑定已撤销且策略列仍存在", "sso",
             "SELECT count(*)::text FROM fdb_mac.table_policy t "
             "JOIN fdb_mac.policy p ON p.polid = t.polid "
             "JOIN pg_class c ON c.oid = t.relid "
             "WHERE p.policy_name = '%s' AND c.relname = '%s'" % (POLICY, TABLE),
             "返回 0", rows_equal([["0"]])),
    sql_step("确认保留的策略列可供复用", "sso",
             "SELECT attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
             "WHERE c.relname = '%s' AND a.attname = '%s' "
             "AND a.attnum > 0 AND NOT a.attisdropped" % (TABLE, COLUMN),
             "返回策略列名", rows_equal([[COLUMN]])),
    _call("重新应用策略并复用已有策略列",
          "SELECT fdb_mac.apply_table_policy('%s', 'public', '%s', true)" %
          (POLICY, TABLE), "策略重新应用成功，复用已有策略列"),
    _binding_check("确认复用策略列后策略已重新绑定",
                   "返回策略、测试表和复用的策略列"),
])


CASE = {
    "id": "mac.mac.policy_definition_and_application",
    "name": "MAC 策略定义及表应用",
    "document": "安可强制访问控制功能转测(张娟).md",
    "section": "2.2.1-2.2.5,2.2.10",
    "group": "mac",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sso", "setup": False,
         "values": {"fdb.enable_mac": "on"}, "apply": "reload",
         "purpose": "恢复 MAC 开关的用例前状态"},
        {"type": "mac_policy", "setup": False, "table": TABLE,
         "policy": POLICY, "column": COLUMN},
    ],
    "requirements": {
        "plugins": ["fbase_mac"], "writable_node": True, "node": "primary",
        "roles": ["sso"],
        "settings": [{"name": "shared_preload_libraries", "contains": "fbase_mac",
                      "purpose": "预加载等保插件，使强制访问控制钩子生效"}],
    },
    "prerequisites": [
        "mac 集群已由平台创建并处于运行状态",
        "fbase_mac 已创建并预加载",
        "SSO 有权设置 fdb.enable_mac 并管理 MAC 策略",
    ],
    "steps": STEPS,
    "teardown": "settings fixture 恢复 MAC 开关；mac_policy fixture 撤销绑定、删除策略和测试表。",
}
