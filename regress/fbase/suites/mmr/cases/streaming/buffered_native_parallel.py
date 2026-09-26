from framework.assertions import command_succeeds, output_contains_text, rows_equal
from framework.steps import (background_sql_step, command_step, sql_step,
                             wait_background_sql_step, wait_sql_step)


TABLE = "fbase_r_stream_pg_{run_id}"
PUBLICATION = "fbase_r_stream_pub_{run_id}"
SUBSCRIPTION = "fbase_r_stream_sub_{run_id}"
GID = "fbase_r_stream_2pc_{run_id}"
SOURCE = "mmr:mmr1"
TARGET = "mmr:mmr2"


def subscription_changes_absent(title):
    sql = "SELECT oid FROM pg_subscription WHERE subname='%s';\n\\! find $PGDATA/base -type f -name '<subscription_oid>-*.changes.0'" % SUBSCRIPTION
    script = ("oid=$(/usr/local/fbase15.15/bin/psql -X -At -h 127.0.0.1 -p 10021 -U postgres -d postgres "
              "-c \"SELECT oid FROM pg_subscription WHERE subname='%s'\"); test -n \"$oid\"; "
              "test -z \"$(find /home/postgres/pgdata/mmr2/base -type f -name \"$oid-*.changes.0\" -print)\"; "
              "echo \"没有属于订阅 $oid 的 changes 临时文件\"") % SUBSCRIPTION
    step = command_step(title, ["sh", "-ec", script],
                        "命令返回 0，订阅端没有属于本轮订阅 OID 的 changes 临时文件",
                        command_succeeds(), node=TARGET)
    step["display_sql"] = sql
    return step


CASE = {
    "id": "mmr.streaming.buffered_native_parallel",
    "name": "buffered 下普通逻辑复制 streaming parallel",
    "document": "多活功能测试文档.md", "section": "9.4.1.1", "group": "streaming",
    "fixtures": [
        "cluster",
        {"type": "settings", "node": SOURCE, "apply": "reload",
         "values": {"logical_decoding_work_mem": "64kB"},
         "purpose": "使文档 64 行 1KiB 事务真实超过 logical_decoding_work_mem"},
        {"type": "settings", "node": TARGET, "apply": "reload",
         "values": {"fdd_streaming_parallel": SUBSCRIPTION},
         "purpose": "按文档为专属普通逻辑订阅启用 parallel apply"},
        {"type": "logical_replication_objects", "source": SOURCE, "target": TARGET,
         "source_table": TABLE, "target_table": TABLE,
         "publication": PUBLICATION, "subscription": SUBSCRIPTION},
        {"type": "mmr_async_set_mode_recovery", "nodes": [SOURCE, TARGET]},
    ],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "groups": ["mmr"], "writable_node": True, "node": SOURCE},
    "evidence_nodes": [SOURCE, TARGET],
    "prerequisites": [
        "隔离 streaming 集群 healthy；debug_logical_replication_streaming 保持默认 buffered，发布端 logical_decoding_work_mem=64kB。",
        "测试仅创建专属普通 publication/subscription，不调用会被 D-012 阻断的默认复制集异步处理。",
    ],
    "steps": [
        sql_step("确认 buffered 配置", "postgres", "SHOW debug_logical_replication_streaming",
                 "返回 buffered", rows_equal([["buffered"]]), node=SOURCE),
        sql_step("确认发布端 streaming 内存阈值", "postgres", "SHOW logical_decoding_work_mem",
                 "返回 64kB", rows_equal([["64kB"]]), node=SOURCE),
        sql_step("确认专属订阅已写入 parallel apply 配置", "postgres",
                 "SHOW fdd_streaming_parallel", "返回专属订阅名",
                 rows_equal([[SUBSCRIPTION]]), node=TARGET),
        sql_step("在发布端创建 streaming 测试表", "postgres",
                 "CREATE TABLE public.%s(id int PRIMARY KEY,payload text)" % TABLE,
                 "返回 CREATE TABLE", command_succeeds(), node=SOURCE),
        sql_step("在订阅端创建同构 streaming 测试表", "postgres",
                 "CREATE TABLE public.%s(id int PRIMARY KEY,payload text)" % TABLE,
                 "返回 CREATE TABLE", command_succeeds(), node=TARGET),
        sql_step("创建仅发布测试表的普通 publication", "postgres",
                 "CREATE PUBLICATION %s FOR TABLE public.%s" % (PUBLICATION, TABLE),
                 "返回 CREATE PUBLICATION", command_succeeds(), node=SOURCE),
        sql_step("按文档创建 streaming=on 的普通 subscription", "postgres",
                 "CREATE SUBSCRIPTION %s CONNECTION 'host=127.0.0.1 port=10011 user=postgres dbname=postgres' "
                 "PUBLICATION %s WITH (copy_data=false,streaming=on)" % (SUBSCRIPTION, PUBLICATION),
                 "返回 CREATE SUBSCRIPTION", command_succeeds(), node=TARGET),
        wait_sql_step("确认专属订阅已启动且 substream=true", "postgres",
                      "SELECT count(*)::text,bool_and(substream)::text FROM pg_subscription s "
                      "JOIN pg_stat_subscription st ON st.subid=s.oid "
                      "WHERE s.subname='%s' AND st.pid IS NOT NULL" % SUBSCRIPTION,
                      "返回 1|true", rows_equal([["1", "true"]]), node=TARGET, timeout=20),
        sql_step("确认专属订阅 OID 供临时文件精确匹配", "postgres",
                 "SELECT oid::text || '|' || substream::text FROM pg_subscription WHERE subname='%s'" % SUBSCRIPTION,
                 "返回本轮订阅 OID 和 true", output_contains_text("|true"), node=TARGET),
        background_sql_step(
            "保持超过 logical_decoding_work_mem 的未提交事务", "postgres",
            "BEGIN; INSERT INTO public.%s SELECT i,repeat('a',1024) FROM generate_series(1,64) i" % TABLE,
            "COMMIT", "后台事务已启动并保持中", command_succeeds(), "buffered_commit",
            node=SOURCE, hold_seconds=8, settle_seconds=2),
        subscription_changes_absent("确认 buffered parallel 事务中未生成本订阅 changes 临时文件"),
        wait_background_sql_step("等待大事务提交", "buffered_commit",
                                 "BEGIN、INSERT 和 COMMIT 成功", command_succeeds(), timeout=20),
        wait_sql_step("确认订阅端收到 64 行大事务数据", "postgres",
                      "SELECT count(*)::text FROM public.%s WHERE id BETWEEN 1 AND 64" % TABLE,
                      "返回 64", rows_equal([["64"]]), node=TARGET, timeout=20),
        background_sql_step(
            "保持两阶段 streaming 事务直至 PREPARE", "postgres",
            "BEGIN; INSERT INTO public.%s SELECT i,repeat('b',1024) FROM generate_series(101,164) i" % TABLE,
            "PREPARE TRANSACTION '%s'" % GID,
            "后台事务完成 PREPARE TRANSACTION", command_succeeds(), "buffered_prepare",
            node=SOURCE, hold_seconds=8, settle_seconds=2),
        wait_background_sql_step("等待两阶段事务 PREPARE", "buffered_prepare",
                                 "BEGIN、INSERT 和 PREPARE TRANSACTION 成功", command_succeeds(), timeout=20),
        sql_step("确认发布端 prepared transaction 存在", "postgres",
                 "SELECT count(*)::text FROM pg_prepared_xacts WHERE gid='%s'" % GID,
                 "返回 1", rows_equal([["1"]]), node=SOURCE),
        subscription_changes_absent("确认 PREPARE 前后本订阅仍无 changes 临时文件"),
        sql_step("按文档提交 prepared transaction", "postgres",
                 "COMMIT PREPARED '%s'" % GID,
                 "返回 COMMIT PREPARED", command_succeeds(), node=SOURCE),
        wait_sql_step("确认订阅端收到两阶段提交数据", "postgres",
                      "SELECT count(*)::text FROM public.%s WHERE id BETWEEN 101 AND 164" % TABLE,
                      "返回 64", rows_equal([["64"]]), node=TARGET, timeout=20),
    ],
    "teardown": "fixture 删除专属 subscription、publication 和两端表；恢复 fdd_streaming_parallel，并收敛 MMR set_mode。",
}
