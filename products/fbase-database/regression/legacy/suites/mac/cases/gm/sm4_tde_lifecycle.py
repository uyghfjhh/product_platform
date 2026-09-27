from framework.assertions import command_fails, command_succeeds, output_contains
from framework.steps import command_step


DATA = "/tmp/fbase_regress_tde_sm4_{run_id}"
KEY = "/tmp/fbase_regress_tde_sm4_{run_id}.txt"
PORT = "15551"
PGHOME = "/usr/local/fbase15.15"
PGCTL = PGHOME + "/bin/pg_ctl"
PSQL = PGHOME + "/bin/psql"


def shell(title, script, expected, assertion=command_succeeds(), timeout=60):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        timeout=timeout)


def psql(title, sql, expected, assertion=command_succeeds()):
    return shell(
        title,
        "%s -X -v ON_ERROR_STOP=1 -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
        (PSQL, PORT, sql), expected, assertion)


def manual_tde_start(title, password, log_name, expected,
                     assertion=command_succeeds()):
    """Drive the product's TDE terminal prompt through a private pseudo-terminal."""
    command = "%s -D %s -M sm4 -l %s/%s -w start" % (
        PGCTL, DATA, DATA, log_name)
    return shell(
        title,
        "(sleep 1; printf '%s\\n') | script -qefc '%s' /dev/null" %
        (password, command),
        expected, assertion)


CASE = {
    "id": "mac.gm.sm4_tde_lifecycle",
    "name": "SM4 透明加密初始化、启动和页存储",
    "document": "商密和TLCP认证转测.md",
    "section": "1.3",
    "group": "gm",
    "fixtures": [
        "cluster",
        {"type": "isolated_tde", "data_dir": DATA, "key_file": KEY},
    ],
    "requirements": {"plugins": ["fbase_mac"], "commands": ["script"],
                     "writable_node": True, "node": "primary"},
    "prerequisites": [
        "隔离实例使用当前 FBase 二进制和 /home/postgres/license/license.dat；txt 密钥文件由当前 PostgreSQL 用户拥有且权限为 0600",
    ],
    "steps": [
        shell("创建权限为 0600 的 SM4 txt 密钥文件",
              "rm -rf %s %s; umask 077; printf 'abcdef\\n' > %s; test \"$(stat -c %%a %s)\" = 600" %
              (DATA, KEY, KEY, KEY),
              "返回 0，密钥文件内容为六位且权限为 0600"),
        shell("使用 -M sm4 和 -K txt 密钥初始化隔离数据库",
              "%s/bin/initdb -D %s -U postgres -M sm4 -K %s --auth=trust" %
              (PGHOME, DATA, KEY),
              "返回 Success，SM4 初始化成功"),
        shell("确认初始化持久化 SM4 算法和密钥命令",
              "grep -F \"encryption_key_command = '%s'\" %s/postgresql.conf; "
              "grep -F \"tde_encrypt = 'sm4'\" %s/postgresql.conf" %
              (KEY, DATA, DATA),
              "输出 encryption_key_command 和 tde_encrypt = 'sm4'"),
        shell("复制 license 并配置隔离端口",
              "cp /home/postgres/license/license.dat %s/license.dat; "
              "printf \"\\nlisten_addresses = '127.0.0.1'\\nport = %s\\n\" >> %s/postgresql.conf" %
              (DATA, PORT, DATA),
              "返回 0，实例可使用 license 启动"),
        manual_tde_start("使用错误的手工 SM4 密钥启动失败", "wrongpw", "wrong.log",
              "返回 could not start server，错误密钥不能解密控制文件",
              command_fails("could not start server")),
        manual_tde_start("使用正确的手工 SM4 密钥启动", "abcdef", "manual.log",
              "返回 server started"),
        psql("确认手工密钥启动后的 SM4 配置",
             "SHOW tde_encrypt; SHOW encryption_key_command",
             "输出 sm4 和配置的密钥路径", output_contains("sm4", KEY)),
        shell("停止手工密钥启动的实例",
              "%s -D %s -w -m fast stop" % (PGCTL, DATA),
              "返回 server stopped"),
        shell("使用 -o -K 指定密钥文件启动",
              "%s -D %s -o '-K %s' -l %s/key_option.log -w start" %
              (PGCTL, DATA, KEY, DATA),
              "返回 server started"),
        shell("停止 -o -K 启动的实例",
              "%s -D %s -w -m fast stop" % (PGCTL, DATA),
              "返回 server stopped"),
        shell("使用 postgresql.conf 中的密钥命令直接启动",
              "%s -D %s -l %s/config.log -w start" % (PGCTL, DATA, DATA),
              "返回 server started"),
        psql("创建文档中的测试表、写入数据并 checkpoint",
             "CREATE TABLE t_test(id integer, str varchar(128)); "
             "INSERT INTO t_test VALUES (0, 'aaaabbbbcccc'); CHECKPOINT",
             "返回 CREATE TABLE、INSERT 0 1、CHECKPOINT"),
        psql("确认用户表主数据页第 12 字节的加密标志位",
             "SELECT (get_byte(pg_read_binary_file('base/' || "
             "(SELECT oid FROM pg_database WHERE datname=current_database()) || '/' || "
             "(SELECT relfilenode FROM pg_class WHERE relname='t_test'), 11, 1), 0) & 128 = 128)::text",
             "返回 true，页面标记为已加密", output_contains("true")),
        psql("确认 SM4 磁盘页中不存在文档写入的明文",
             "SELECT position(convert_to('aaaabbbbcccc', 'UTF8') in "
             "pg_read_binary_file('base/' || (SELECT oid FROM pg_database WHERE datname=current_database()) || '/' || "
             "(SELECT relfilenode FROM pg_class WHERE relname='t_test')))::text",
             "返回 0，磁盘页不含明文", output_contains("0")),
        psql("确认数据库可透明读取 SM4 加密数据",
             "SELECT id::text, str FROM t_test",
             "返回 0|aaaabbbbcccc", output_contains("0|aaaabbbbcccc")),
    ],
    "teardown": "isolated_tde fixture 停止并删除 SM4 隔离集群、日志和密钥文件。",
}
