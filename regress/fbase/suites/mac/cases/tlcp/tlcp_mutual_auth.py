from framework.assertions import command_succeeds, output_contains
from framework.steps import command_step


ROOT = "/tmp/fbase_regress_tlcp_handshake_{run_id}"
DATA = ROOT + "/data"
CERTS = ROOT + "/tlcp"
PORT = "15556"
PGHOME = "/usr/local/fbase15.15"
PGCTL = PGHOME + "/bin/pg_ctl"
PSQL = PGHOME + "/bin/psql"


def shell(title, script, expected, assertion=command_succeeds(), timeout=90):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        timeout=timeout)


def psql(title, user, sql, expected, assertion=command_succeeds()):
    return shell(
        title,
        "%s -X -v ON_ERROR_STOP=1 -At -h 127.0.0.1 -p %s -U %s -d postgres -c %r" %
        (PSQL, PORT, user, sql), expected, assertion)


def tlcp_client(title, expected, assertion=command_succeeds()):
    return shell(
        title,
        "%s -X -At \"host=127.0.0.1 port=%s dbname=postgres user=postgres "
        "sslmode=verify-ca sslrootcert=%s/root.crt sslcert=%s/postgresql.crt "
        "sslkey=%s/postgresql.key sslenccert=%s/encpostgresql.crt "
        "sslenckey=%s/encpostgresql.key\" -c \"SELECT ssl,version,cipher "
        "FROM pg_stat_ssl WHERE pid=pg_backend_pid()\"" %
        (PSQL, PORT, CERTS, CERTS, CERTS, CERTS, CERTS), expected, assertion)


CASE = {
    "id": "mac.tlcp.tlcp_mutual_auth",
    "name": "TLCP 双向认证",
    "document": "商密和TLCP认证转测.md",
    "section": "2.4.1",
    "group": "tlcp",
    "fixtures": [
        "cluster",
        {"type": "isolated_tlcp_handshake", "data_dir": ROOT},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary"},
    "prerequisites": [
        "隔离实例使用当前 FBase 的 OpenSSL/TLCP 构建；证书和私钥均由本用例生成并在清理阶段删除",
    ],
    "steps": [
        shell(
            "初始化并启动 TLCP 隔离集群",
            "rm -rf %s; mkdir -p %s; %s/bin/initdb -D %s -U postgres --auth=trust; "
            "cp /home/postgres/license/license.dat %s/license.dat; "
            "printf \"\\nshared_preload_libraries = 'fbase_mac'\\nlisten_addresses = '127.0.0.1'\\nport = %s\\n\" >> %s/postgresql.conf; "
            "%s -D %s -l %s/server.log -w start" %
            (ROOT, CERTS, PGHOME, DATA, DATA, PORT, DATA, PGCTL, DATA, DATA),
            "返回 Success 和 server started，空证书库的隔离 fbase_mac 实例启动"),
        psql("安装 fbase_mac 扩展", "postgres", "CREATE EXTENSION fbase_mac",
             "返回 CREATE EXTENSION"),
        psql(
            "生成 TLCP CA、服务端和客户端双证书",
            "postgres",
            "SELECT fdb_mac.generate_ca_certificate('tlcp','/C=AA/ST=BB/O=Fbase/OU=Regress/CN=tlcp-ca',2); "
            "SELECT fdb_mac.generate_server_certificate('tlcp','/C=AA/ST=BB/O=Fbase/OU=Regress/CN=127.0.0.1 sign','/C=AA/ST=BB/O=Fbase/OU=Regress/CN=127.0.0.1 enc',2); "
            "SELECT fdb_mac.generate_client_certificate('tlcp','/C=AA/ST=BB/O=Fbase/OU=Regress/CN=postgres',2)",
            "三次返回 SUCCESS", output_contains("SUCCESS")),
        psql(
            "导出 TLCP CA、服务端和客户端双证书",
            "postgres",
            "SELECT fdb_mac.export_certificate('all','tlcp','%s','postgres')" % CERTS,
            "返回 EXPORT SUCCESS", output_contains("EXPORT SUCCESS")),
        shell(
            "确认导出证书及私钥文件和权限",
            "for file in root.crt root.key server.crt server.key encserver.crt encserver.key postgresql.crt postgresql.key encpostgresql.crt encpostgresql.key; do "
            "test -f %s/$file; test \"$(stat -c %%a %s/$file)\" = 600; done" %
            (CERTS, CERTS),
            "十个证书和私钥文件均存在且权限为 0600"),
        shell(
            "配置服务端 TLCP 双证书和 cert 双向认证 HBA",
            "mkdir -p %s/tlcp; cp %s/root.crt %s/server.crt %s/server.key %s/encserver.crt %s/encserver.key %s/tlcp/; "
            "chmod 600 %s/tlcp/server.key %s/tlcp/encserver.key; "
            "printf \"\\nssl = on\\nenable_tlcp = on\\nssl_ca_file = 'tlcp/root.crt'\\nssl_cert_file = 'tlcp/server.crt'\\nssl_key_file = 'tlcp/server.key'\\nssl_enccert_file = 'tlcp/encserver.crt'\\nssl_enckey_file = 'tlcp/encserver.key'\\n\" >> %s/postgresql.conf; "
            "printf 'local all all trust\\nhostssl all all 127.0.0.1/32 cert\\n' > %s/pg_hba.conf; "
            "%s -D %s -w -m fast stop; %s -D %s -l %s/tlcp.log -w start" %
            (DATA, CERTS, CERTS, CERTS, CERTS, CERTS, DATA, DATA, DATA, DATA,
             DATA, PGCTL, DATA, PGCTL, DATA, DATA),
            "返回 server stopped 和 server started，TLCP 服务端成功加载签名和加密证书"),
        tlcp_client(
            "使用 TLCP 客户端双证书建立 cert 双向认证连接",
            "返回 t|TLCP|ECC-SM4-CBC-SM3",
            output_contains("t|TLCP|ECC-SM4-CBC-SM3")),
    ],
    "teardown": "isolated_tlcp_handshake fixture 停止并删除隔离服务器、TLCP 证书、私钥和日志。",
}
