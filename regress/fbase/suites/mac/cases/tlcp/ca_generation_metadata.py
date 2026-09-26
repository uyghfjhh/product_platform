from framework.assertions import (output_contains, output_contains_text,
                                  rows_equal, sql_fails)
from framework.steps import sql_step
from suites.mac.tlcp_session_support import CONNECTION, session_spec


CASE = {
    "id": "mac.tlcp.ca_generation_metadata",
    "name": "TLCP CA 生成、密码规则与证书元数据",
    "document": "商密和TLCP认证转测.md",
    "section": "2.2.1,2.2.2,2.3.1",
    "group": "tlcp",
    "fixtures": ["cluster"],
    "session": session_spec(), "connection": CONNECTION,
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary"},
    "prerequisites": ["fbase_mac 已安装；测试在事务内生成 TLCP CA，结束时回滚，不保留证书或私钥元数据"],
    "steps": [
        sql_step("非空 CA 私钥密码必须符合复杂度规则", "postgres",
                 "SELECT fdb_mac.generate_ca_certificate('tlcp', '/C=AA/ST=BB/O=Fbase/OU=Regress/CN=bad-ca', 2, '11')",
                 "执行失败，短密码不符合证书私钥密码规则",
                 sql_fails("passwd to save ca cert is not null, but does not compliant with rules")),
        sql_step("事务内生成带密码的 TLCP CA", "postgres",
                 "BEGIN; SELECT fdb_mac.generate_ca_certificate('tlcp', '/C=AA/ST=BB/O=Fbase/OU=Regress/CN=fbase-regress-ca', 2, '!Aa242260'); ROLLBACK",
                 "依次返回 BEGIN、SUCCESS、ROLLBACK", output_contains("BEGIN", "SUCCESS", "ROLLBACK")),
        sql_step("事务内确认 CA 元数据字段", "postgres",
                 "BEGIN; SELECT fdb_mac.generate_ca_certificate('tlcp', '/C=AA/ST=BB/O=Fbase/OU=Regress/CN=fbase-regress-ca', 2, '!Aa242260'); SELECT kind, useage, type, status, source, (length > 0)::text, (ca_id = 0)::text FROM fdb_mac.certs_info WHERE useage = 'ca'; ROLLBACK",
                 "返回 sm2|ca|tlcp|created|generate|true|true，且证书长度大于零、CA 无上级 ca_id",
                 output_contains_text("sm2", "ca", "tlcp", "created", "generate", "true")),
        sql_step("事务内确认 CA 证书和私钥元数据关联", "postgres",
                 "BEGIN; SELECT fdb_mac.generate_ca_certificate('tlcp', '/C=AA/ST=BB/O=Fbase/OU=Regress/CN=fbase-regress-ca', 2, '!Aa242260'); SELECT count(*)::text, bool_and(type IN ('cert','key'))::text, bool_and(length > 0)::text FROM fdb_mac.key_meta_data; ROLLBACK",
                 "返回 2|true|true，保存一份证书和一份私钥，二者均有内容",
                 output_contains_text("2", "true")),
        sql_step("确认事务回滚后没有遗留 CA 元数据", "postgres",
                 "SELECT count(*)::text FROM fdb_mac.certs_info WHERE owner = current_user AND source = 'generate'",
                 "返回 0", rows_equal([["0"]])),
    ],
    "teardown": "所有生成证书和私钥的步骤均以 BEGIN/ROLLBACK 结束；最终步骤确认没有遗留生成元数据。",
}
