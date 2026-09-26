from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


POLICY = "fbase_regress_user_policy"
U1 = "fbase_regress_mac_u1"
U2 = "fbase_regress_mac_u2"

LEVELS = [("L1", 10), ("L2", 20), ("L3", 30),
          ("L4", 40), ("L5", 50), ("L6", 60)]
COMPARTMENTS = [("C1", 10), ("C2", 20), ("C3", 30),
                ("C4", 40), ("C5", 50), ("C6", 60)]


def _call(title, sql, expected):
    return sql_step(title, "sso", sql, expected, command_succeeds())


CASE = {
    "id": "mac.mac.user_creation_access_and_session_labels",
    "name": "MAC 用户创建、授权及会话标签查看",
    "document": "安可强制访问控制功能转测(张娟).md",
    "section": "2.2.6-2.2.8",
    "group": "mac",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sso", "setup": False,
         "values": {"fdb.enable_mac": "on"}, "apply": "reload",
         "purpose": "恢复 MAC 开关的用例前状态"},
        {"type": "mac_policy", "policy": POLICY, "column": "fbase_regress_user_label",
         "create_table": False, "apply": False,
         "levels": LEVELS, "compartments": COMPARTMENTS,
         "labels": [("L5:C1,C2,C3,C4,C5", 41)]},
        {"type": "roles", "setup": False, "cleanup_priority": -10,
         "create": [U1, U2]},
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
        "SSO 有权启用 MAC、设置用户标签和特权",
        "策略定义用例已覆盖本章节所依赖的策略、等级和范围定义",
    ],
    "steps": [
        _call("SSO 开启 MAC 功能", "ALTER SYSTEM SET fdb.enable_mac = on",
              "ALTER SYSTEM 执行成功"),
        {"type": "cluster_action", "title": "重载配置使 MAC 开关生效",
         "action": "reload", "expected": "reload 执行成功",
         "assertion": command_succeeds()},
        sql_step("确认 MAC 功能已开启", "sso", "SHOW fdb.enable_mac", "返回 on",
                 rows_equal([["on"]])),
        sql_step("管理员创建用户 U1", "postgres", "CREATE USER %s" % U1,
                 "返回 CREATE ROLE", command_succeeds()),
        sql_step("管理员为 U1 设置密码", "postgres",
                 "ALTER USER %s PASSWORD 'Lp12345678'" % U1,
                 "返回 ALTER ROLE", command_succeeds()),
        sql_step("管理员创建用户 U2", "postgres", "CREATE USER %s" % U2,
                 "返回 CREATE ROLE", command_succeeds()),
        sql_step("管理员为 U2 设置密码", "postgres",
                 "ALTER USER %s PASSWORD 'Lp12345678'" % U2,
                 "返回 ALTER ROLE", command_succeeds()),
        sql_step("确认 U1 和 U2 均可登录", "postgres",
                 "SELECT rolname, rolcanlogin FROM pg_roles "
                 "WHERE rolname IN ('%s', '%s') ORDER BY rolname" % (U1, U2),
                 "依次返回 U1、U2，且 rolcanlogin 为 true",
                 rows_equal([[U1, "t"], [U2, "t"]])),
        _call("直接为 U1 设置五类用户标签",
              "SELECT fdb_mac.set_user_labels('%s', '%s', "
              "'L5:C1,C2,C3,C4,C5', 'L5:C1,C2,C3,C4', 'L2:C1,C2', "
              "'L3:C1,C2', 'L2:C1,C2')" % (POLICY, U1), "U1 标签设置成功"),
        sql_step("确认直接授权的 U1 等级范围", "sso",
                 "SELECT max_level::text, min_level::text, insert_level::text, def_level::text "
                 "FROM fdb_mac.user u JOIN fdb_mac.policy p ON p.polid = u.polid "
                 "WHERE p.policy_name = '%s' AND u.user_name = '%s'" % (POLICY, U1),
                 "返回最大读 L5、最小写 L2、插入 L3、默认读 L2 对应的等级 ID",
                 rows_equal([["50", "20", "30", "20"]])),
        _call("按等级为 U1 重设五类标签权限",
              "SELECT fdb_mac.set_user_levels('%s', '%s', 'L5', 'L2', 'L3', 'L2')" %
              (POLICY, U1), "U1 等级授权成功"),
        _call("按范围为 U1 重设五类标签权限",
              "SELECT fdb_mac.set_user_compartments('%s', '%s', "
              "'C1,C2,C3,C4,C5', 'C1,C2,C3,C4', 'C1,C2', 'C1,C2', 'C1,C2')" %
              (POLICY, U1), "U1 范围授权成功"),
        sql_step("确认分步授权后的 U1 等级仍正确", "sso",
                 "SELECT max_level::text, min_level::text, insert_level::text, def_level::text "
                 "FROM fdb_mac.user u JOIN fdb_mac.policy p ON p.polid = u.polid "
                 "WHERE p.policy_name = '%s' AND u.user_name = '%s'" % (POLICY, U1),
                 "返回 50、20、30、20", rows_equal([["50", "20", "30", "20"]])),
        _call("为 U2 设置 L1 用户等级", "SELECT fdb_mac.set_user_levels('%s', '%s', 'L1')" %
              (POLICY, U2), "U2 等级授权成功"),
        _call("为 U2 授予 FULL 特权", "SELECT fdb_mac.set_user_privs('%s', '%s', 'FULL')" %
              (POLICY, U2), "FULL 特权授予成功"),
        sql_step("确认 U2 已拥有 FULL 特权", "sso",
                 "SELECT privs::text FROM fdb_mac.user u JOIN fdb_mac.policy p ON p.polid = u.polid "
                 "WHERE p.policy_name = '%s' AND u.user_name = '%s'" % (POLICY, U2),
                 "返回 FULL 对应的特权值 2", rows_equal([["2"]])),
        _call("撤销 U2 的特权", "SELECT fdb_mac.set_user_privs('%s', '%s', '')" %
              (POLICY, U2), "特权撤销成功"),
        sql_step("确认 U2 特权已撤销", "sso",
                 "SELECT privs::text FROM fdb_mac.user u JOIN fdb_mac.policy p ON p.polid = u.polid "
                 "WHERE p.policy_name = '%s' AND u.user_name = '%s'" % (POLICY, U2),
                 "返回 0", rows_equal([["0"]])),
        sql_step("U1 在用户会话查看自身标签", U1,
                 "SELECT policy, user_name, max_read_label, max_write_label, min_write_label, "
                 "def_read_label, def_write_label, def_insert_label FROM fdb_mac.show_user_labels()",
                 "返回 U1 的策略和五类标签",
                 rows_equal([[POLICY, U1, "L5:C1,C2,C3,C4,C5", "L5:C1,C2,C3,C4",
                              "L2:C1,C2", "L2:C1,C2", "L5:C1,C2,C3,C4", "L3:C1,C2"]])),
        sql_step("U2 在用户会话查看自身标签", U2,
                 "SELECT policy, user_name, privs FROM fdb_mac.show_user_labels()",
                 "返回 U2 的策略、用户和已撤销特权状态",
                 rows_equal([[POLICY, U2, None]])),
        _call("撤销 U1 的全部 MAC 权限", "SELECT fdb_mac.drop_user_access('%s', '%s')" %
              (POLICY, U1), "U1 全部 MAC 权限撤销成功"),
        sql_step("确认 U1 的 MAC 权限记录已删除", "sso",
                 "SELECT count(*)::text FROM fdb_mac.user u JOIN fdb_mac.policy p ON p.polid = u.polid "
                 "WHERE p.policy_name = '%s' AND u.user_name = '%s'" % (POLICY, U1),
                 "返回 0", rows_equal([["0"]])),
    ],
    "teardown": "mac_policy fixture 删除策略及其用户访问记录；roles fixture 删除 U1、U2；settings fixture 恢复 MAC 开关。",
}
