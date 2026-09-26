from framework.assertions import output_contains_text, sql_fails
from framework.steps import sql_step
from suites.mac.tlcp_session_support import CONNECTION, session_spec


CA_SUBJECT = "/C=AA/ST=BB/O=Fbase/OU=Regress/CN=fbase-regress-ca"
SERVER_SIGN_SUBJECT = "/C=AA/ST=BB/O=Fbase/OU=Regress/CN=fbase-regress-server-sign"
SERVER_ENC_SUBJECT = "/C=AA/ST=BB/O=Fbase/OU=Regress/CN=fbase-regress-server-enc"
CLIENT_SUBJECT = "/C=AA/ST=BB/O=Fbase/OU=Regress/CN=fbase-regress-client"
CA_PASS = "!Aa242260"
SIGN_PASS = "!Aa123456"
ENC_PASS = "!Aa111111"


CASE = {
    "id": "mac.tlcp.server_client_generation",
    "name": "TLCP 服务端和客户端双证书签发",
    "document": "商密和TLCP认证转测.md",
    "section": "2.3.2,2.3.3",
    "group": "tlcp",
    "fixtures": ["cluster"],
    "session": session_spec(), "connection": CONNECTION,
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary"},
    "prerequisites": ["fbase_mac 已安装；每个签发流程在单一事务中先生成带密码的 TLCP CA，最后回滚"],
    "steps": [
        sql_step("未提供受密码保护 CA 的密码时不能签发服务端证书", "postgres",
                 "BEGIN; SELECT fdb_mac.generate_ca_certificate('tlcp', '%s', 2, '%s'); SELECT fdb_mac.generate_server_certificate('tlcp', '%s', '%s', 2, '', '', ''); ROLLBACK" % (CA_SUBJECT, CA_PASS, SERVER_SIGN_SUBJECT, SERVER_ENC_SUBJECT),
                 "执行失败，提示无法解密 CA 或获取 CA 证书",
                 sql_fails("fail to get ca cert")),
        sql_step("事务内签发 TLCP 服务端签名和加密证书", "postgres",
                 "BEGIN; SELECT fdb_mac.generate_ca_certificate('tlcp', '%s', 2, '%s'); SELECT fdb_mac.generate_server_certificate('tlcp', '%s', '%s', 2, '%s', '%s', '%s'); SELECT useage, type, kind, count(*)::text, bool_and(status = 'created')::text, bool_and(source = 'generate')::text FROM fdb_mac.certs_info WHERE useage = 'server' GROUP BY useage, type, kind; ROLLBACK" % (CA_SUBJECT, CA_PASS, SERVER_SIGN_SUBJECT, SERVER_ENC_SUBJECT, CA_PASS, SIGN_PASS, ENC_PASS),
                 "返回 server|tlcp|sm2|2|true|true，服务端包含签名和加密两份 SM2 证书",
                 output_contains_text("SUCCESS", "server", "tlcp", "sm2", "2", "true")),
        sql_step("事务内确认服务端签名和加密私钥关联", "postgres",
                 "BEGIN; SELECT fdb_mac.generate_ca_certificate('tlcp', '%s', 2, '%s'); SELECT fdb_mac.generate_server_certificate('tlcp', '%s', '%s', 2, '%s', '%s', '%s'); SELECT count(*)::text, bool_and(useage IN ('sin','enc'))::text FROM fdb_mac.key_meta_data WHERE type = 'cert' AND pkey_id <> 0 AND useage IN ('sin','enc'); ROLLBACK" % (CA_SUBJECT, CA_PASS, SERVER_SIGN_SUBJECT, SERVER_ENC_SUBJECT, CA_PASS, SIGN_PASS, ENC_PASS),
                 "返回 2|true，两份服务端证书分别关联签名和加密用途",
                 output_contains_text("2", "true")),
        sql_step("事务内签发 TLCP 客户端签名和加密证书", "postgres",
                 "BEGIN; SELECT fdb_mac.generate_ca_certificate('tlcp', '%s', 2, '%s'); SELECT fdb_mac.generate_client_certificate('tlcp', '%s', 2, '%s', '%s', '%s'); SELECT useage, type, kind, user_name, count(*)::text, bool_and(status = 'created')::text FROM fdb_mac.certs_info WHERE useage = 'client' GROUP BY useage, type, kind, user_name; ROLLBACK" % (CA_SUBJECT, CA_PASS, CLIENT_SUBJECT, CA_PASS, SIGN_PASS, ENC_PASS),
                 "返回 client|tlcp|sm2|fbase-regress-client|2|true，客户端包含签名和加密两份 SM2 证书并绑定主题 CN 用户",
                 output_contains_text("client", "tlcp", "sm2", "fbase-regress-client", "2", "true")),
        sql_step("确认两个事务均未遗留证书元数据", "postgres",
                 "SELECT count(*)::text FROM fdb_mac.certs_info WHERE owner = current_user AND source = 'generate'",
                 "返回 0", output_contains_text("0")),
    ],
    "teardown": "所有 CA、服务端和客户端证书签发均在 BEGIN/ROLLBACK 内；最终步骤确认没有遗留生成元数据。",
}
