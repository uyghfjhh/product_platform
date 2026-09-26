from framework.assertions import command_succeeds, output_contains
from framework.steps import command_step


DATA = "/tmp/fbase_regress_tde_rc4_{run_id}"
KEY = "/tmp/fbase_regress_tde_rc4_{run_id}.txt"
PORT = "15540"
PGHOME = "/usr/local/fbase15.15"
PSQL = PGHOME + "/bin/psql"
PGCTL = PGHOME + "/bin/pg_ctl"


def shell(title, script, expected, assertion=command_succeeds()):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        timeout=60)


def psql(title, sql, expected, assertion=command_succeeds()):
    return shell(title, "%s -X -v ON_ERROR_STOP=1 -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
                 (PSQL, PORT, sql), expected, assertion)


CASE = {
    "id": "mac.tde.rc4_storage_lifecycle",
    "name": "RC4 透明加密初始化、页存储与密钥约束",
    "document": "透明加密功能转测（陈群友）.md",
    "section": "1.1,1.3,1.4",
    "group": "tde",
    "fixtures": ["cluster", {"type": "isolated_tde", "data_dir": DATA, "key_file": KEY}],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary"},
    "prerequisites": ["本机可创建 /tmp/fbase_regress_tde_rc4；license.dat 位于 /home/postgres/license/license.dat"],
    "steps": [
        shell("创建权限为 0600 的 txt TDE 密钥文件", "rm -rf %s %s; umask 077; printf 'abcdef\\n' > %s; test \"$(stat -c %%a %s)\" = 600" % (DATA, KEY, KEY, KEY),
              "返回 0，密钥内容为六位且权限为 0600"),
        shell("使用 -M rc4 和 -K txt 密钥初始化隔离数据库", "%s -D %s -M rc4 -K %s --auth=trust" % (PGHOME + "/bin/initdb", DATA, KEY),
              "返回 Success，初始化成功"),
        shell("确认初始化将密钥路径和 RC4 写入配置", "grep -F \"encryption_key_command = '%s'\" %s; grep -F \"tde_encrypt = 'rc4'\" %s" % (KEY, DATA + "/postgresql.conf", DATA + "/postgresql.conf"),
              "输出 encryption_key_command 和 tde_encrypt = 'rc4'"),
        shell("复制 license 并启动隔离 RC4 集群", "cp /home/postgres/license/license.dat %s/license.dat; %s -D %s -o '-p %s' -l %s/server.log start" % (DATA, PGCTL, DATA, PORT, DATA),
              "返回 server started"),
        psql("确认启动后的 TDE 算法和密钥命令", "SHOW tde_encrypt; SHOW encryption_key_command", "输出 rc4 和已配置的密钥路径",
             output_contains("rc4", KEY)),
        psql("创建用户表、写入数据并执行 checkpoint", "CREATE TABLE tde_data(id integer, payload text); INSERT INTO tde_data VALUES (1, 'tde-visible-marker'); CHECKPOINT", "返回 CREATE TABLE、INSERT 0 1、CHECKPOINT"),
        psql("确认用户表主数据页第 12 字节的加密标志位", "SELECT (get_byte(pg_read_binary_file('base/' || (SELECT oid FROM pg_database WHERE datname=current_database()) || '/' || (SELECT relfilenode FROM pg_class WHERE relname='tde_data'), 11, 1), 0) & 128 = 128)::text", "返回 true，页面标记为已加密",
             output_contains("true")),
        psql("确认磁盘页中不存在明文 payload", "SELECT position(convert_to('tde-visible-marker', 'UTF8') in pg_read_binary_file('base/' || (SELECT oid FROM pg_database WHERE datname=current_database()) || '/' || (SELECT relfilenode FROM pg_class WHERE relname='tde_data')))::text", "返回 0，磁盘页不含明文标记",
             output_contains("0")),
        psql("确认数据库仍可透明读取加密数据", "SELECT id::text, payload FROM tde_data", "返回 1|tde-visible-marker", output_contains("1|tde-visible-marker")),
    ],
    "teardown": "isolated_tde fixture 停止并删除隔离集群、数据目录和密钥文件。",
}
