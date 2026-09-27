from framework.assertions import command_succeeds, output_contains
from framework.steps import command_step


DATA = "/tmp/fbase_regress_license_{run_id}"
COPIED = DATA + "/copied_license"
OPTION = DATA + "/option_license"
PGHOME = "/usr/local/fbase15.15"
PGCTL = PGHOME + "/bin/pg_ctl"
PSQL = PGHOME + "/bin/psql"
LICENSE = "/home/postgres/license/license.dat"
COPIED_PORT = "15549"
OPTION_PORT = "15550"


def shell(title, script, expected, assertion=command_succeeds(), timeout=60):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        timeout=timeout)


def psql(title, sql, expected, assertion=command_succeeds()):
    return shell(
        title,
        "%s -X -v ON_ERROR_STOP=1 -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
        (PSQL, COPIED_PORT, sql), expected, assertion)


def init_cluster(path, port):
    return shell(
        "初始化隔离 license 数据库 %s" % path.rsplit("/", 1)[-1],
        "%s/bin/initdb -D %s -U postgres --auth=trust; "
        "printf \"\\nshared_preload_libraries = 'fbase_mac'\\nlisten_addresses = '127.0.0.1'\\nport = %s\\n\" >> %s/postgresql.conf" %
        (PGHOME, path, port, path),
        "返回 Success，配置 fbase_mac 预加载和隔离端口")


CASE = {
    "id": "mac.gm.license_lifecycle",
    "name": "License 启动、查询和重载",
    "document": "商密和TLCP认证转测.md",
    "section": "1.1.1",
    "group": "gm",
    "fixtures": [
        "cluster",
        {"type": "isolated_license", "data_dir": DATA},
    ],
    "requirements": {
        "plugins": ["fbase_mac", "fb_license"], "writable_node": True,
        "node": "primary",
    },
    "prerequisites": [
        "当前安装目录应已安装 fbase_mac 和 fb_license；使用 /home/postgres/license/license.dat 启动隔离实例",
    ],
    "steps": [
        shell("清理并创建 license 隔离目录", "rm -rf %s; mkdir -p %s" % (DATA, DATA),
              "返回 0，隔离目录为空"),
        init_cluster(COPIED, COPIED_PORT),
        shell("按文档复制 license.dat 后默认启动",
              "cp %s %s/license.dat; %s -D %s -l %s/server.log -w start" %
              (LICENSE, COPIED, PGCTL, COPIED, COPIED),
              "返回 server started，启动阶段校验数据目录内 license.dat"),
        psql("创建 license 查询和重载扩展", "CREATE EXTENSION fb_license",
             "返回 CREATE EXTENSION"),
        psql("查询当前加载的 license 信息",
             "SELECT product, status, days_remaining >= 0 FROM get_license_info() ORDER BY product",
             "输出 fbase、OK 和 true", output_contains("fbase|OK|t")),
        psql("使用指定 license 文件重载",
             "SELECT sys_reload_license('%s')" % LICENSE,
             "返回 true", output_contains("t")),
        psql("使用数据目录中的 license.dat 重载", "SELECT sys_reload_license()",
             "返回 true", output_contains("t")),
        init_cluster(OPTION, OPTION_PORT),
        shell("按文档使用 pg_ctl -L 指定 license 启动",
              "%s -D %s -L %s -l %s/server.log -w start; test -f %s/license.dat" %
              (PGCTL, OPTION, LICENSE, OPTION, OPTION),
              "返回 server started，-L 指定的 license.dat 自动复制到数据目录"),
        shell("停止后不再指定 -L 直接重启",
              "%s -D %s -w -m fast stop; %s -D %s -l %s/restart.log -w start" %
              (PGCTL, OPTION, PGCTL, OPTION, OPTION),
              "返回 server stopped 和 server started，使用已自动复制的 license.dat 启动"),
    ],
    "teardown": "isolated_license fixture 停止并删除两个隔离实例及其 license 副本。",
}
