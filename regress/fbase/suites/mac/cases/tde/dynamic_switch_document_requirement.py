from framework.assertions import command_succeeds, output_contains
from framework.steps import command_step


DATA = "/tmp/fbase_regress_tde_dynamic_{run_id}"
KEY = "/tmp/fbase_regress_tde_dynamic_{run_id}.txt"
PORT = "15541"
PGHOME = "/usr/local/fbase15.15"


def shell(title, script, expected, assertion=command_succeeds()):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        timeout=60)


def psql(title, sql, expected, assertion=command_succeeds()):
    return shell(title, "%s/bin/psql -X -v ON_ERROR_STOP=1 -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
                 (PGHOME, PORT, sql), expected, assertion)


CASE = {
    "id": "mac.tde.dynamic_switch_document_requirement",
    "name": "TDE reload 动态切换文档符合性",
    "document": "透明加密功能转测（陈群友）.md",
    "section": "1.2",
    "known_issue": "D-007",
    "group": "tde",
    "fixtures": ["cluster", {"type": "isolated_tde", "data_dir": DATA, "key_file": KEY}],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary"},
    "prerequisites": ["隔离集群使用 rc4 和 txt 密钥启动；文档 1.2 声明 ALTER SYSTEM 后 reload 可动态禁用透明加密"],
    "steps": [
        shell("创建 0600 的 TDE 密钥文件", "rm -rf %s %s; umask 077; printf 'abcdef\\n' > %s" % (DATA, KEY, KEY),
              "返回 0"),
        shell("初始化 RC4 隔离集群", "%s/bin/initdb -D %s -M rc4 -K %s --auth=trust" % (PGHOME, DATA, KEY),
              "返回 Success"),
        shell("复制 license 并启动 RC4 隔离集群", "cp /home/postgres/license/license.dat %s/license.dat; %s/bin/pg_ctl -D %s -o '-p %s' -l %s/server.log start" % (DATA, PGHOME, DATA, PORT, DATA),
              "返回 server started"),
        psql("确认启动状态为 rc4", "SHOW tde_encrypt", "返回 rc4", output_contains("rc4")),
        psql("按转测文档设置 TDE 算法为 plain", "ALTER SYSTEM SET tde_encrypt = 'plain'", "返回 ALTER SYSTEM"),
        psql("按转测文档重载 TDE 配置", "SELECT pg_reload_conf()", "返回 true", output_contains("t")),
        psql("文档要求 reload 后 TDE 应动态切换为 plain", "SHOW tde_encrypt",
             "转测文档 1.2 要求：ALTER SYSTEM SET tde_encrypt=plain 后 reload，SHOW 必须返回 plain",
             output_contains("plain")),
    ],
    "teardown": "isolated_tde fixture 停止并删除隔离集群、数据目录和密钥文件。失败步骤用于保留文档 1.2 与产品实现不一致的证据。",
}
