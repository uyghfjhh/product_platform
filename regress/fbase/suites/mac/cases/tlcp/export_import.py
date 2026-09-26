from framework.assertions import command_succeeds, output_contains
from framework.steps import command_step


ROOT = "/tmp/fbase_regress_tlcp_transfer_{run_id}"
SOURCE = ROOT + "/source"
TARGET = ROOT + "/target"
EXPORT = ROOT + "/export"
PGHOME = "/usr/local/fbase15.15"
PGCTL = PGHOME + "/bin/pg_ctl"
PSQL = PGHOME + "/bin/psql"
SOURCE_PORT = "15552"
TARGET_PORT = "15553"
CA_PASS = "!Aa242260"
SIGN_PASS = "!Aa123456"
ENC_PASS = "!Aa111111"


def shell(title, script, expected, assertion=command_succeeds(), timeout=90):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        timeout=timeout)


def psql(title, port, user, sql, expected, assertion=command_succeeds()):
    return shell(
        title,
        "%s -X -v ON_ERROR_STOP=1 -At -h 127.0.0.1 -p %s -U %s -d postgres -c %r" %
        (PSQL, port, user, sql), expected, assertion)


def init_cluster(title, data_dir, port):
    return shell(
        title,
        "%s/bin/initdb -D %s -U postgres --auth=trust; "
        "cp /home/postgres/license/license.dat %s/license.dat; "
        "printf \"\\nshared_preload_libraries = 'fbase_mac'\\nlisten_addresses = '127.0.0.1'\\nport = %s\\n\" >> %s/postgresql.conf; "
        "%s -D %s -l %s/server.log -w start" %
        (PGHOME, data_dir, data_dir, port, data_dir, PGCTL, data_dir, data_dir),
        "返回 Success 和 server started，空证书库的隔离 fbase_mac 实例启动")


CASE = {
    "id": "mac.tlcp.export_import",
    "name": "TLCP 证书导出与 SSO 导入",
    "document": "商密和TLCP认证转测.md",
    "section": "2.3.4,2.3.10",
    "group": "tlcp",
    "fixtures": [
        "cluster",
        {"type": "isolated_tlcp_transfer", "data_dir": ROOT},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary"},
    "prerequisites": [
        "source 和 target 均为全新隔离 fbase_mac 集群；当前产品 load_certificate 要求 SSO 和 10 参数，详见 问题记录.md 的 D-002",
    ],
    "steps": [
        shell("清理并创建 TLCP 导出目录", "rm -rf %s; mkdir -p %s" % (ROOT, EXPORT),
              "返回 0，导出目录为空"),
        init_cluster("初始化并启动 TLCP 证书源集群", SOURCE, SOURCE_PORT),
        init_cluster("初始化并启动 TLCP 证书目标集群", TARGET, TARGET_PORT),
        psql("在源集群安装 fbase_mac 扩展", SOURCE_PORT, "postgres",
             "CREATE EXTENSION fbase_mac", "返回 CREATE EXTENSION"),
        psql("在目标集群安装 fbase_mac 扩展", TARGET_PORT, "postgres",
             "CREATE EXTENSION fbase_mac", "返回 CREATE EXTENSION"),
        psql("在源集群生成 TLCP CA 和服务端双证书", SOURCE_PORT, "postgres",
             "SELECT fdb_mac.generate_ca_certificate('tlcp','/C=AA/ST=BB/O=Fbase/OU=Regress/CN=transfer-ca',2,'%s'); "
             "SELECT fdb_mac.generate_server_certificate('tlcp','/C=AA/ST=BB/O=Fbase/OU=Regress/CN=transfer-server-sign','/C=AA/ST=BB/O=Fbase/OU=Regress/CN=transfer-server-enc',2,'%s','%s','%s')" %
             (CA_PASS, CA_PASS, SIGN_PASS, ENC_PASS),
             "两次返回 SUCCESS", output_contains("SUCCESS")),
        psql("按文档导出 CA 和服务端 TLCP 证书", SOURCE_PORT, "postgres",
             "SELECT fdb_mac.export_certificate('all','tlcp','%s','postgres','%s','%s','%s')" %
             (EXPORT, CA_PASS, SIGN_PASS, ENC_PASS),
             "无客户端证书时返回 SOME EXPORT SUCCESS，并导出 CA 和服务端双证书",
             output_contains("SOME EXPORT SUCCESS")),
        shell("确认导出的 CA 和服务端双证书文件及权限",
              "for file in root.crt root.key server.crt server.key encserver.crt encserver.key; do "
              "test -f %s/$file; test \"$(stat -c %%a %s/$file)\" = 600; done" %
              (EXPORT, EXPORT),
              "六个文件均存在且权限为 0600"),
        shell("SSO 按当前 10 参数接口导入 CA 和服务端双证书",
              "start=$(date '+%%Y-%%m-%%d %%H:%%M:%%S%%z'); "
              "end=$(date -d '+2 days' '+%%Y-%%m-%%d %%H:%%M:%%S%%z'); "
              "%s -X -v ON_ERROR_STOP=1 -At -h 127.0.0.1 -p %s -U sso -d postgres -c \""
              "SELECT fdb_mac.load_certificate('tlcp','ca','$start','$end','%s/root.key','%s/root.crt','postgres','','sin','%s'); "
              "SELECT fdb_mac.load_certificate('tlcp','server','$start','$end','%s/server.key','%s/server.crt','postgres','postgres','sin','%s'); "
              "SELECT fdb_mac.load_certificate('tlcp','server','$start','$end','%s/encserver.key','%s/encserver.crt','postgres','postgres','enc','%s');\"" %
              (PSQL, TARGET_PORT, EXPORT, EXPORT, CA_PASS, EXPORT, EXPORT,
               SIGN_PASS, EXPORT, EXPORT, ENC_PASS),
              "三次返回 SUCCESS，分别导入 CA、server 签名证书和 server 加密证书",
              output_contains("SUCCESS")),
        psql("确认目标集群导入证书元数据", TARGET_PORT, "postgres",
             "SELECT useage, type, source, count(*)::text FROM fdb_mac.certs_info "
             "GROUP BY useage,type,source ORDER BY useage",
             "返回 ca|tlcp|import|1 和 server|tlcp|import|2",
             output_contains("ca|tlcp|import|1", "server|tlcp|import|2")),
    ],
    "teardown": "isolated_tlcp_transfer fixture 停止并删除 source/target 集群和导出证书文件。",
}
