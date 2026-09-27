from framework.assertions import command_fails, command_succeeds, output_contains
from framework.steps import command_step


ROOT = "/tmp/fbase_regress_ssl_{run_id}"
DATA = ROOT + "/data"
CERTS = ROOT + "/ssl"
PORT = "15555"
PGHOME = "/usr/local/fbase15.15"
PGCTL = PGHOME + "/bin/pg_ctl"
PSQL = PGHOME + "/bin/psql"
CA_PASS = "!Aa242260"
SERVER_PASS = "!Aa123456"
CLIENT_PASS = "!Aa654321"


def shell(title, script, expected, assertion=command_succeeds(), timeout=90):
    return command_step(title, ["sh", "-ec", script], expected, assertion, timeout=timeout)


def psql(title, user, sql, expected, assertion=command_succeeds()):
    return shell(title, "%s -X -v ON_ERROR_STOP=1 -At -h 127.0.0.1 -p %s -U %s -d postgres -c %r" %
                 (PSQL, PORT, user, sql), expected, assertion)


def ssl_client(title, expected, assertion=command_succeeds()):
    return shell(title, "%s -X -At \"host=127.0.0.1 port=%s dbname=postgres user=postgres sslmode=verify-ca sslrootcert=%s/root.crt sslcert=%s/postgresql.crt sslkey=%s/postgresql.key sslpassword=%s\" -c \"SELECT ssl,version,cipher FROM pg_stat_ssl WHERE pid=pg_backend_pid()\"" %
                 (PSQL, PORT, CERTS, CERTS, CERTS, CLIENT_PASS), expected, assertion)


CASE = {
    "id": "mac.tlcp.ssl_mutual_auth", "name": "SSL 双向认证与证书吊销", "document": "商密和TLCP认证转测.md", "section": "2.4.2", "group": "tlcp",
    "fixtures": ["cluster", {"type": "isolated_ssl", "data_dir": ROOT}],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary"},
    "prerequisites": ["隔离实例使用当前 FBase 的 OpenSSL/TLCP 构建；吊销按当前产品要求由 SSO 执行，详见 问题记录.md D-003"],
    "steps": [
        shell("初始化并启动 SSL 隔离集群", "rm -rf %s; mkdir -p %s; %s/bin/initdb -D %s -U postgres --auth=trust; cp /home/postgres/license/license.dat %s/license.dat; printf \"\\nshared_preload_libraries = 'fbase_mac'\\nlisten_addresses = '127.0.0.1'\\nport = %s\\n\" >> %s/postgresql.conf; %s -D %s -l %s/server.log -w start" % (ROOT, CERTS, PGHOME, DATA, DATA, PORT, DATA, PGCTL, DATA, DATA), "返回 Success 和 server started"),
        psql("安装 fbase_mac 扩展", "postgres", "CREATE EXTENSION fbase_mac", "返回 CREATE EXTENSION"),
        psql("生成 SSL CA、服务端和客户端证书", "postgres", "SELECT fdb_mac.generate_ca_certificate('ssl','/C=AA/ST=BB/O=CC/OU=DD/CN=root ca',10,'%s'); SELECT fdb_mac.generate_server_certificate('ssl','/C=AA/ST=BB/O=CC/OU=DD/CN=127.0.0.1','',10,'%s','%s'); SELECT fdb_mac.generate_client_certificate('ssl','/C=AA/ST=BB/O=CC/OU=DD/CN=postgres',10,'%s','%s')" % (CA_PASS, CA_PASS, SERVER_PASS, CA_PASS, CLIENT_PASS), "三次返回 SUCCESS", output_contains("SUCCESS")),
        psql("依次导出 SSL CA、服务端和客户端证书", "postgres", "SELECT fdb_mac.export_certificate('ca','ssl','%s','postgres','%s'); SELECT fdb_mac.export_certificate('server','ssl','%s','postgres','%s','%s'); SELECT fdb_mac.export_certificate('client','ssl','%s','postgres','%s','%s')" % (CERTS, CA_PASS, CERTS, CA_PASS, SERVER_PASS, CERTS, CA_PASS, CLIENT_PASS), "三次返回 EXPORT SUCCESS", output_contains("EXPORT SUCCESS")),
        shell("配置 SSL 服务端证书和双向认证 HBA", "cp %s/root.crt %s/root.crt; cp %s/server.crt %s/server.crt; cp %s/server.key %s/server.key; chmod 600 %s/server.key; printf \"\\nssl = on\\nssl_ca_file = 'root.crt'\\nssl_cert_file = 'server.crt'\\nssl_key_file = 'server.key'\\nssl_passphrase_command = 'printf %s'\\n\" >> %s/postgresql.conf; printf 'local all all trust\\nhostssl all all 127.0.0.1/32 cert\\n' > %s/pg_hba.conf; %s -D %s -w -m fast stop; %s -D %s -l %s/ssl.log -w start" % (CERTS, DATA, CERTS, DATA, CERTS, DATA, DATA, SERVER_PASS, DATA, DATA, PGCTL, DATA, PGCTL, DATA, DATA), "返回 server stopped 和 server started"),
        ssl_client("使用加密客户端私钥建立 SSL 双向认证连接", "返回 TLSv1.3 和 TLS_AES_256_GCM_SHA384", output_contains("t|TLSv1.3|TLS_AES_256_GCM_SHA384")),
        shell("SSO 通过本地 socket 吊销 SSL CA 及关联证书", "%s -X -v ON_ERROR_STOP=1 -At -p %s -U sso -d postgres -c %r" % (PSQL, PORT, "SELECT fdb_mac.abort_certificate((SELECT id FROM fdb_mac.certs_info WHERE useage='ca' AND type='ssl'),10,'%s')" % CA_PASS), "返回 SUCCESS", output_contains("SUCCESS")),
        shell("postgres 通过本地 socket 导出吊销证书列表", "%s -X -v ON_ERROR_STOP=1 -At -p %s -U postgres -d postgres -c %r" % (PSQL, PORT, "SELECT fdb_mac.export_certificate('crl','tlcp','%s','postgres','%s','%s','%s')" % (CERTS, CA_PASS, SERVER_PASS, CLIENT_PASS)), "返回 EXPORT SUCCESS", output_contains("EXPORT SUCCESS")),
        shell("配置 CRL 后重启 SSL 服务端", "test -f %s/root.crl; cp %s/root.crl %s/root.crl; printf \"\\nssl_crl_file = 'root.crl'\\n\" >> %s/postgresql.conf; %s -D %s -w -m fast stop; %s -D %s -l %s/crl.log -w start" % (CERTS, CERTS, DATA, DATA, PGCTL, DATA, PGCTL, DATA, DATA), "返回 server stopped 和 server started"),
        ssl_client("使用已吊销客户端证书连接失败", "执行失败，客户端证书已被吊销", command_fails("certificate revoked")),
    ],
    "teardown": "isolated_ssl fixture 停止并删除隔离服务器、证书、CRL 和日志。",
}
