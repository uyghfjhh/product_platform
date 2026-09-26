from framework.assertions import command_fails, command_succeeds, output_contains
from framework.steps import command_step


ROOT = "/tmp/fbase_regress_tlcp_audit_{run_id}"
DATA = ROOT + "/data"
PORT = "15554"
PGHOME = "/usr/local/fbase15.15"
PGCTL = PGHOME + "/bin/pg_ctl"
PSQL = PGHOME + "/bin/psql"
RULE = "fbase_regress_tlcp_audit"


def shell(title, script, expected, assertion=command_succeeds(), timeout=90):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        timeout=timeout)


def psql(title, user, sql, expected, assertion=command_succeeds()):
    return shell(title, "%s -X -v ON_ERROR_STOP=1 -At -h 127.0.0.1 -p %s -U %s -d postgres -c %r" %
                 (PSQL, PORT, user, sql), expected, assertion)


CASE = {
    "id": "mac.tlcp.udf_audit_logs", "name": "TLCP UDF 成功和失败审计记录",
    "document": "商密和TLCP认证转测.md", "section": "2.5", "group": "tlcp",
    "fixtures": ["cluster", {"type": "isolated_tlcp_audit", "data_dir": ROOT}],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary"},
    "prerequisites": ["全新隔离 fbase_mac 集群，避免共享证书元数据影响 TLCP UDF"],
    "steps": [
        shell("初始化并启动 TLCP 审计隔离集群",
              "rm -rf %s; %s/bin/initdb -D %s -U postgres --auth=trust; cp /home/postgres/license/license.dat %s/license.dat; printf \"\\nshared_preload_libraries = 'fbase_mac'\\nlisten_addresses = '127.0.0.1'\\nport = %s\\n\" >> %s/postgresql.conf; %s -D %s -l %s/server.log -w start" %
              (ROOT, PGHOME, DATA, DATA, PORT, DATA, PGCTL, DATA, DATA),
              "返回 Success 和 server started"),
        psql("安装 fbase_mac 扩展", "postgres", "CREATE EXTENSION fbase_mac", "返回 CREATE EXTENSION"),
        psql("SAO 开启审计", "sao", "ALTER SYSTEM SET fdb.enable_audit = 1", "返回 ALTER SYSTEM"),
        shell("重载审计配置", "%s -D %s reload" % (PGCTL, DATA), "返回 server signaled"),
        psql("SAO 设置 postgres 的 TLCP MAC 审计规则", "sao",
             "SELECT fdb_audit.set_audit_stmt('%s','MAC','postgres','ALL')" % RULE,
             "规则创建成功"),
        psql("postgres 在事务内成功生成 TLCP CA", "postgres",
             "BEGIN; SELECT fdb_mac.generate_ca_certificate('tlcp','/C=AA/ST=BB/O=Fbase/OU=Regress/CN=tlcp-audit-ca',2,'!Aa242260'); ROLLBACK",
             "返回 BEGIN、SUCCESS、ROLLBACK", output_contains("SUCCESS")),
        psql("postgres 导出到不存在目录失败", "postgres",
             "SELECT fdb_mac.export_certificate('client','tlcp','/tmp/fbase_regress_tlcp_audit_missing','postgres')",
             "执行失败并记录失败审计", command_fails("please input legal path")),
        psql("SAO 查到 TLCP 成功和失败 MAC/FUNCTION 审计", "sao",
             "SELECT objname, succ FROM fdb_audit.audit_records WHERE username='postgres' AND audittype='MAC' AND objtype='FUNCTION' AND objname IN ('generate_ca_certificate','export_certificate') ORDER BY serialno",
             "返回 generate_ca_certificate|SUCCESS 和 export_certificate|FAILED",
             output_contains("generate_ca_certificate|SUCCESS", "export_certificate|FAILED")),
    ],
    "teardown": "isolated_tlcp_audit fixture 停止并删除隔离集群、审计规则和日志。",
}
