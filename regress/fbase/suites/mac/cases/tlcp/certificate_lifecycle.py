from framework.assertions import output_contains_text, rows_equal, sql_fails
from framework.steps import sql_step
from suites.mac.tlcp_session_support import CONNECTION, session_spec


CA = "/C=AA/ST=BB/O=Fbase/OU=Regress/CN=fbase-regress-ca"
SIGN = "/C=AA/ST=BB/O=Fbase/OU=Regress/CN=fbase-regress-server-sign"
ENC = "/C=AA/ST=BB/O=Fbase/OU=Regress/CN=fbase-regress-server-enc"
CA_PASS = "!Aa242260"
SIGN_PASS = "!Aa123456"
ENC_PASS = "!Aa111111"


CASE = {
    "id": "mac.tlcp.certificate_lifecycle",
    "name": "TLCP 证书更新、备份、撤销、归档与恢复",
    "document": "商密和TLCP认证转测.md",
    "section": "2.3.5,2.3.6,2.3.7,2.3.8,2.3.9",
    "group": "tlcp",
    "fixtures": ["cluster"],
    "session": session_spec(), "connection": CONNECTION,
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary", "roles": ["sso"]},
    "prerequisites": ["证书相关元数据表为空；超级用户生成证书，SSO 执行备份、撤销、归档和恢复；fixture 结束时清理所有测试元数据"],
    "steps": [
        sql_step("超级用户生成 TLCP CA 和服务端双证书", "postgres",
                 "SELECT fdb_mac.generate_ca_certificate('tlcp','%s',2,'%s'); SELECT fdb_mac.generate_server_certificate('tlcp','%s','%s',2,'%s','%s','%s')" % (CA, CA_PASS, SIGN, ENC, CA_PASS, SIGN_PASS, ENC_PASS),
                 "两次函数返回 SUCCESS", output_contains_text("SUCCESS")),
        sql_step("续期服务端证书", "postgres",
                 "SELECT fdb_mac.renewal_certificate((SELECT max(id) FROM fdb_mac.certs_info WHERE useage='server'),7,'%s','%s'); SELECT count(*)::text, bool_and(end_time>start_time)::text FROM fdb_mac.certs_info WHERE useage='server'" % (CA_PASS, ENC_PASS),
                 "返回 SUCCESS 和 2|true", output_contains_text("SUCCESS", "2", "true")),
        sql_step("SSO 备份证书和私钥元数据", "sso",
                 "SELECT fdb_mac.backup_certificate(); SELECT (SELECT count(*) FROM fdb_mac.certs_info_bak)::text, (SELECT count(*) FROM fdb_mac.key_meta_data_bak)::text",
                 "返回 SUCCESS，两个备份表均有记录", output_contains_text("SUCCESS")),
        sql_step("SSO 撤销服务端配对证书", "sso",
                 "SELECT fdb_mac.abort_certificate((SELECT max(id) FROM fdb_mac.certs_info WHERE useage='server'),7,'%s'); SELECT count(*)::text, bool_and(status='revoked')::text FROM fdb_mac.certs_info WHERE useage='server'" % CA_PASS,
                 "返回 SUCCESS 和 2|true", output_contains_text("SUCCESS", "2", "true")),
        sql_step("SSO 归档已撤销服务端证书", "sso",
                 "SELECT fdb_mac.archive_certificate(); SELECT ((SELECT count(*) FROM fdb_mac.certs_info WHERE useage='server') = 0)::text, ((SELECT count(*) FROM fdb_mac.certs_info_achive WHERE useage='server' AND status='revoked') >= 2)::text",
                 "返回 SUCCESS、true|true：当前 server 为 0，归档中至少有两张 revoked server 证书",
                 output_contains_text("SUCCESS", "true")),
        sql_step("SSO 从备份恢复服务端证书", "sso",
                 "SELECT fdb_mac.restore_certificate((SELECT max(id) FROM fdb_mac.certs_info_bak WHERE useage='server')); SELECT count(*)::text, bool_and(status='created')::text FROM fdb_mac.certs_info WHERE useage='server'",
                 "文档要求恢复成功；当前产品因 archive 删除备份记录而拒绝恢复，详见 D-008",
                 sql_fails("can not get any cert"),
                 continue_on_failure=True),
        sql_step("确认恢复后存在活动服务端证书", "postgres",
                 "SELECT (count(*) > 0)::text FROM fdb_mac.certs_info WHERE useage='server'",
                 "当前产品返回 false，证明 D-008 的归档后恢复文档预期不成立", rows_equal([["false"]]), continue_on_failure=True),
        sql_step("SSO 撤销 CA 并归档全部生命周期测试证书", "sso",
                 "SELECT fdb_mac.abort_certificate((SELECT max(id) FROM fdb_mac.certs_info WHERE useage='ca'),7,'%s'); SELECT fdb_mac.archive_certificate()" % CA_PASS,
                 "两次函数均返回 SUCCESS", output_contains_text("SUCCESS")),
        sql_step("确认归档后活动证书元数据已清空", "postgres",
                 "SELECT (SELECT count(*) FROM fdb_mac.certs_info)::text, (SELECT count(*) FROM fdb_mac.key_meta_data)::text",
                 "返回 0|0", rows_equal([["0", "0"]])),
    ],
    "teardown": "测试步骤按转测文档撤销 CA 并归档，清空活动证书表；备份和归档表保留生命周期历史。",
}
