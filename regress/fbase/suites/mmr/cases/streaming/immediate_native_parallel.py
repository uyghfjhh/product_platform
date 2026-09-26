from framework.assertions import command_succeeds, rows_equal
from framework.steps import background_sql_step, command_step, sql_step, wait_background_sql_step, wait_sql_step

TABLE = "fbase_r_stream_imm_{run_id}"
PUBLICATION = "fbase_r_stream_imm_pub_{run_id}"
SUBSCRIPTION = "fbase_r_stream_imm_sub_{run_id}"
SOURCE, TARGET = "mmr:mmr1", "mmr:mmr2"

CASE = {
    "id": "mmr.streaming.immediate_native_parallel", "name": "immediate 下普通逻辑复制 streaming parallel",
    "document": "多活功能测试文档.md", "section": "9.4.1.2", "group": "streaming",
    "fixtures": ["cluster", {"type":"settings", "nodes":[SOURCE,TARGET], "apply":"reload", "values":{"debug_logical_replication_streaming":"immediate", "logical_decoding_work_mem":"64kB"}, "purpose":"按文档启用 immediate streaming 并设置 64kB 解码阈值"}, {"type":"settings", "node":TARGET, "apply":"reload", "values":{"fdd_streaming_parallel":SUBSCRIPTION}, "purpose":"按产品参数将专属订阅设为 parallel apply"}, {"type":"logical_replication_objects", "source":SOURCE, "target":TARGET, "source_table":TABLE, "target_table":TABLE, "publication":PUBLICATION, "subscription":SUBSCRIPTION}, {"type":"mmr_async_set_mode_recovery", "nodes":[SOURCE,TARGET]}],
    "requirements":{"clusters":["mmr"],"plugins":["fdd_mmr"],"groups":["mmr"],"writable_node":True,"node":SOURCE}, "evidence_nodes":[SOURCE,TARGET],
    "prerequisites":["隔离 streaming 集群 healthy；仅使用专属普通逻辑复制对象。"],
    "steps":[
        sql_step("确认两个成员 immediate 生效", "postgres", "SHOW debug_logical_replication_streaming", "返回 immediate", rows_equal([["immediate"]]), node=SOURCE),
        sql_step("创建发布端测试表", "postgres", "CREATE TABLE public.%s(id int PRIMARY KEY,payload text)" % TABLE, "返回 CREATE TABLE", command_succeeds(), node=SOURCE),
        sql_step("创建订阅端同构表", "postgres", "CREATE TABLE public.%s(id int PRIMARY KEY,payload text)" % TABLE, "返回 CREATE TABLE", command_succeeds(), node=TARGET),
        sql_step("创建专属 publication", "postgres", "CREATE PUBLICATION %s FOR TABLE public.%s" % (PUBLICATION,TABLE), "返回 CREATE PUBLICATION", command_succeeds(), node=SOURCE),
        sql_step("创建 streaming=on subscription", "postgres", "CREATE SUBSCRIPTION %s CONNECTION 'host=127.0.0.1 port=10011 user=postgres dbname=postgres' PUBLICATION %s WITH (copy_data=false,streaming=on)" % (SUBSCRIPTION,PUBLICATION), "返回 CREATE SUBSCRIPTION", command_succeeds(), node=TARGET),
        wait_sql_step("确认订阅已启动", "postgres", "SELECT count(*)::text FROM pg_subscription s JOIN pg_stat_subscription st ON st.subid=s.oid WHERE s.subname='%s' AND st.pid IS NOT NULL" % SUBSCRIPTION, "返回 1", rows_equal([["1"]]), node=TARGET, timeout=20),
        background_sql_step("保持 immediate 未提交大事务", "postgres", "BEGIN; INSERT INTO public.%s SELECT gs,repeat('immediate',128) FROM generate_series(1,64) gs" % TABLE, "COMMIT", "后台事务保持中", command_succeeds(), "immediate_commit", node=SOURCE, hold_seconds=8, settle_seconds=2),
        command_step("确认事务中订阅端出现 changes 临时文件", ["sh","-ec","for i in $(seq 1 6); do find /home/postgres/pgdata/mmr2/base -type f -name '*.changes.0' -print -quit | grep -q . && exit 0; sleep 1; done; exit 1"], "6 秒内命令返回 0", command_succeeds(), node=TARGET, timeout=8),
        wait_background_sql_step("等待 immediate 事务提交", "immediate_commit", "BEGIN、INSERT 和 COMMIT 成功", command_succeeds(), timeout=20),
        wait_sql_step("确认订阅端收到 immediate 数据", "postgres", "SELECT count(*)::text FROM public.%s WHERE id BETWEEN 1 AND 64" % TABLE, "返回 64", rows_equal([["64"]]), node=TARGET, timeout=20),
    ], "teardown":"fixture 删除专属订阅、发布和两端表，恢复两个 immediate 配置与 parallel 配置。"
}
