from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.cases.node_management.create_group import PGCTL, PGHOME, PSQL, psql, shell


ROOT = "/tmp/fbase_regress_mmr_license_lifecycle_{run_id}"
PORT = "15533"
LICENSE = "/home/postgres/license/license.dat"


CASE = {
    "id": "mmr.installation.license_lifecycle", "name": "pg_ctl license 启动、预警配置、重载和查询",
    "document": "多活功能测试文档.md", "section": "11.1,11.2,11.3.1,11.3.2", "group": "installation",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr", "fb_license"], "writable_node": True, "node": "mmr:mmr1"}, "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["使用临时 PGDATA 验证 pg_ctl 的 -L 参数；随后停止并以数据目录中的默认 license 再启动。", "所有 license 与 GUC 变更仅位于临时实例，清理时整个目录删除。"],
    "steps": [
        setup(shell("初始化临时实例并以 pg_ctl -L 指定 license 启动", "mkdir -p %s; %s/bin/initdb -D %s -U postgres --auth-local=trust --auth-host=trust; printf \"\\nshared_preload_libraries = 'fdd_mmr'\\nfdd.running_databases = 'postgres'\\nwal_level = logical\\ntrack_commit_timestamp = on\\nmax_worker_processes = 8\\nmax_logical_replication_workers = 4\\nmax_replication_slots = 10\\nmax_wal_senders = 10\\nlisten_addresses = '127.0.0.1'\\nport = %s\\n\" >> %s/postgresql.conf; %s -D %s -l %s/start.log -L %s -w start; %s; %s" % (ROOT, PGHOME, ROOT, PORT, ROOT, PGCTL, ROOT, ROOT, LICENSE, psql(PORT, "CREATE EXTENSION fdd_mmr"), psql(PORT, "CREATE EXTENSION fb_license")), "initdb、pg_ctl -L 启动并创建扩展成功", timeout=50)),
        query("展示 pg_ctl -L 启动后数据目录中的 license 文件", PORT, "SELECT (pg_stat_file('license.dat')).size > 0 AS license_copied", "返回 true", "t"), query("展示当前加载的 license 信息", PORT, "SELECT product,status,days_remaining FROM get_license_info() ORDER BY product", "返回 fbase、fmmr，状态均为 OK", "fbase", "fmmr", "OK"),
        setup(shell("停止临时实例并按文档使用默认 license 再启动", "%s -D %s stop -m immediate; %s -D %s -l %s/start.log -w start" % (PGCTL, ROOT, PGCTL, ROOT, ROOT), "默认 license 启动成功", timeout=40)),
        query("确认默认 license 再启动后服务可用", PORT, "SELECT pg_postmaster_start_time() IS NOT NULL AS started", "返回 true", "t"),
        sql("按文档设置 license_expiration_warning_days", psql(PORT, "ALTER SYSTEM SET license_expiration_warning_days=70000"), "返回 ALTER SYSTEM", "ALTER SYSTEM SET license_expiration_warning_days=70000;"),
        sql("按文档 reload 使 license 预警配置生效", psql(PORT, "SELECT pg_reload_conf()"), "返回 true", "SELECT pg_reload_conf();"),
        query("展示实际 license_expiration_warning_days", PORT, "SHOW license_expiration_warning_days", "返回 70000", "70000"),
        sql("按文档用 license 文件执行 sys_reload_license", psql(PORT, "SELECT sys_reload_license('%s')" % LICENSE), "返回 true", "SELECT sys_reload_license('%s')" % LICENSE), query("展示重载后的 license 信息", PORT, "SELECT product,status,days_remaining FROM get_license_info() ORDER BY product", "返回 fbase、fmmr，状态均为 OK", "fbase", "fmmr", "OK"),
    ],
    "teardown": "以 immediate 停止临时 license 实例；递归删除临时 PGDATA，连同 pg_ctl -L 复制的 license.dat、自动配置、扩展元数据和启动日志一并删除。",
}
