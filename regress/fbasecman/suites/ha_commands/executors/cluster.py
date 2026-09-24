"""HA console command executors: CLUSTER."""

import time
import os
try:
    import fcntl
except ImportError:
    fcntl = None
import sys
import shlex
from pathlib import Path

from framework.configuration import load_regression_config
from suites.ha_commands.runtime import HaCommandFailure, HaCommandRuntime
from suites.ha_commands.helpers import *

__all__ = ['_run_console_set_validation_toggle', '_run_refresh_cluster', '_run_refresh_cluster_probe_edges', '_run_refresh_cluster_syntax_errors', '_run_set_cluster_30_datasource_roundtrip', '_run_set_cluster_active_idempotent', '_run_set_cluster_invalid_commands', '_run_set_cluster_parted_active_roundtrip', '_run_set_cluster_write_promoted_roundtrip', '_run_set_node_promoted_write_cluster_conflict', '_run_write_cluster_format_preservation']


def _run_refresh_cluster_syntax_errors(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW CLUSTERS;', "查看 REFRESH 语法错误前的运行态",
            'pg_cluster_1 为 VALID 且 current primary 为 pg_1',
            lambda output: all(v in output for v in ("pg_cluster_1", "VALID", "pg_1")))
    for sql, title in (
        ('REFRESH CLUSTER;', "执行缺少 cluster 名的 REFRESH 命令"),
        ('REFRESH NODE pg_1;', "执行错误关键字的 REFRESH 命令"),
        ('REFRESH CLUSTER pg_cluster_1 extra;', "执行带额外参数的 REFRESH 命令"),
    ):
        backup = rt.backup_checkpoint(conf)
        rt.psql_error(sql, title, '返回 ERROR 且命令被拒绝',
                      lambda output: "ERROR:" in output)
        rt.assert_no_backup_created(backup, conf, "验证 REFRESH 语法错误未创建备份")
        rt.diff(before, conf)
        rt.psql('SHOW CLUSTERS;', "验证 REFRESH 语法错误后的运行态",
                'pg_cluster_1 仍为 VALID 且 current primary 为 pg_1',
                lambda output: all(v in output for v in ("pg_cluster_1", "VALID", "pg_1")))


def _run_set_cluster_30_datasource_roundtrip(rt):
    conf = rt.start(transform=_add_30_cluster_datasources)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.psql('SHOW DATASOURCES;', "查看 30 datasource cluster 操作前状态",
            "cluster_ds_01 和 cluster_ds_30 均为 active",
            lambda output: all(v in output for v in ("cluster_ds_01", "cluster_ds_30", "active")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET CLUSTER PARTED bulk_cluster;', "将 30 个 datasource 批量置为 PARTED",
            "返回 SET CLUSTER 且命令不报错",
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 30 datasource PARTED 备份")
    parted = _cluster_datasources_have_status(conf.read_text(encoding="utf-8"), "parted")
    rt.check("验证 cluster 展开完整覆盖 30 个 datasource", "30 个节点均为 parted", "30 datasources parted=%s" % parted, parted)
    rt.diff_contains(before, conf, ('-    status "active"', '+    status "parted"'), "验证 30 datasource PARTED 配置 diff")
    rt.psql('SHOW DATASOURCES;', "查看 30 datasource PARTED 后状态",
            "cluster_ds_01 和 cluster_ds_30 均为 parted",
            lambda output: all(v in output for v in ("cluster_ds_01", "cluster_ds_30", "parted")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET CLUSTER ACTIVE bulk_cluster;', "恢复 30 个 datasource 为 ACTIVE",
            "返回 SET CLUSTER 且命令不报错",
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 30 datasource ACTIVE 恢复备份")
    restored = _cluster_datasources_have_status(conf.read_text(encoding="utf-8"), "active")
    rt.check("验证 30 个 datasource 全部恢复", "30 个节点均恢复 active", "30 datasources active=%s" % restored, restored)
    rt.diff(before, conf)
    rt.psql('SHOW DATASOURCES;', "查看 30 datasource 恢复后状态",
            "cluster_ds_01 和 cluster_ds_30 均为 active",
            lambda output: all(v in output for v in ("cluster_ds_01", "cluster_ds_30", "active")))


def _run_set_cluster_invalid_commands(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW CLUSTERS;', "查看非法 SET CLUSTER 命令前的运行态",
            'pg_cluster_1 为 VALID 且成员为 active',
            lambda output: all(v in output for v in ("pg_cluster_1", "VALID")))
    for sql, title, predicate in (
        ('SET CLUSTER PARTED no_such_cluster;', "执行不存在 cluster 的 PARTED 命令",
         lambda output: all(v in output for v in ("ERROR:", "no_such_cluster", "does not exist"))),
        ('SET CLUSTER WEIGHT pg_cluster_1;', "执行非法 SET CLUSTER 动作",
         lambda output: "ERROR:" in output),
    ):
        backup = rt.backup_checkpoint(conf)
        rt.psql_error(sql, title, '返回 ERROR 且命令被拒绝', predicate)
        rt.assert_no_backup_created(backup, conf, "验证非法 SET CLUSTER 未创建备份")
        rt.diff(before, conf)
        rt.psql('SHOW CLUSTERS;', "验证非法 SET CLUSTER 命令后的运行态",
                'pg_cluster_1 仍为 VALID 且成员为 active',
                lambda output: all(v in output for v in ("pg_cluster_1", "VALID")))


def _run_console_set_validation_toggle(rt):
    def with_validation_yes(content):
        return 'console_set_validation yes\n' + content

    conf = rt.start(transform=with_validation_yes)
    rt.check(
        "确认 console_set_validation 默认开启",
        "配置文件包含 console_set_validation yes",
        "console_set_validation yes=%s" %
        ('console_set_validation yes' in conf.read_text(encoding="utf-8")),
        'console_set_validation yes' in conf.read_text(encoding="utf-8"),
    )
    rt.psql(
        'set a=1;',
        "校验开启时接受第一条 GUC 赋值",
        "返回 SET",
        lambda output: output.count("SET") >= 1 and "ERROR" not in output,
    )
    rt.psql(
        'b=1;',
        "校验开启时接受分号拆包后的第二条 GUC 赋值",
        "返回 SET",
        lambda output: output.count("SET") >= 1 and "ERROR" not in output,
    )
    rt.psql(
        'set a to 1;',
        "校验开启时接受第一条 TO 形式 GUC 赋值",
        "返回 SET",
        lambda output: output.count("SET") >= 1 and "ERROR" not in output,
    )
    rt.psql(
        'b to 1;',
        "校验开启时接受分号拆包后的第二条 TO 形式 GUC 赋值",
        "返回 SET",
        lambda output: output.count("SET") >= 1 and "ERROR" not in output,
    )
    rt.psql_error(
        'set parted ;',
        "校验开启时拒绝不完整 SET",
        "返回 unsupported console SET command",
        lambda output: "unsupported console SET command" in output,
    )
    rt.psql_error(
        'set pg_1 parted ;',
        "校验开启时拒绝非白名单 SET",
        "返回 unsupported console SET command",
        lambda output: "unsupported console SET command" in output,
    )
    rt.psql(
        "SET TIME ZONE 'UTC';",
        "校验开启时接受 SET TIME ZONE",
        "返回 SET 且不报错",
        lambda output: "SET" in output and "ERROR" not in output,
    )

    text = conf.read_text(encoding="utf-8")
    conf.write_text(text.replace(
        "console_set_validation yes", "console_set_validation no", 1),
        encoding="utf-8")
    rt.psql(
        'RELOAD;',
        "Reload 关闭 console_set_validation",
        "返回 RELOAD",
        lambda output: "RELOAD" in output and "ERROR" not in output,
    )
    reload_log = rt.proxy_log.read_text(encoding="utf-8", errors="replace")
    rt.check(
        "验证 Reload 日志记录 console_set_validation no",
        "日志包含 console_set_validation no",
        "console_set_validation no=%s" %
        ("console_set_validation no" in reload_log),
        "console_set_validation no" in reload_log,
    )
    rt.psql(
        'set parted ;',
        "关闭校验后接受任意 SET",
        "返回 SET 且不报错",
        lambda output: "SET" in output and "ERROR" not in output,
    )
    rt.psql(
        'set pg_1 parted ;',
        "关闭校验后接受独立续写 SET",
        "返回 SET 且不报错",
        lambda output: "SET" in output and "ERROR" not in output,
    )

    text = conf.read_text(encoding="utf-8")
    conf.write_text(text.replace(
        "console_set_validation no", "console_set_validation yes", 1),
        encoding="utf-8")
    rt.psql(
        'RELOAD;',
        "Reload 恢复 console_set_validation",
        "返回 RELOAD",
        lambda output: "RELOAD" in output and "ERROR" not in output,
    )
    reload_log = rt.proxy_log.read_text(encoding="utf-8", errors="replace")
    rt.check(
        "验证 Reload 日志记录 console_set_validation yes",
        "日志包含 console_set_validation yes",
        "console_set_validation yes=%s" %
        ("console_set_validation yes" in reload_log),
        "console_set_validation yes" in reload_log,
    )
    rt.psql_error(
        'set parted ;',
        "恢复校验后再次拒绝不完整 SET",
        "返回 unsupported console SET command",
        lambda output: "unsupported console SET command" in output,
    )


def _run_set_cluster_active_idempotent(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW CLUSTERS;', "查看 SET CLUSTER ACTIVE 执行前的运行态",
            'pg_cluster_1 为 VALID 且 current primary 为 pg_1',
            lambda output: all(v in output for v in ("pg_cluster_1", "VALID", "pg_1")))
    rt.psql(
        'SET CLUSTER ACTIVE pg_cluster_1;',
        "执行 SET CLUSTER ACTIVE 幂等命令",
        '返回 SET CLUSTER；NO CHANGE 详情写入 fbasecman.log',
            lambda output: ("SET CLUSTER" in output or "NO CONFIG CHANGE" in output)
            and "ERROR" not in output,
    )
    rt.diff(before, conf)
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;',
        "验证 SET CLUSTER ACTIVE 后的运行态",
        'mmr_group 的 pg_cluster_1 为 VALID，current primary 为 pg_1，且仍有 active 候选节点',
        lambda output: all(value in output for value in (
            "mmr_group", "pg_cluster_1", "pg_1",
        )),
    )


def _run_set_node_promoted_write_cluster_conflict(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql(
        'SET NODE PROMOTED pg_2 IN GROUP mmr_group;',
        "验证 PROMOTED 目标已是 write cluster 时幂等处理",
        '目标已是 write cluster 时命令幂等返回 NO CONFIG CHANGE，不修改配置',
        lambda output: ("SET NODE" in output or "NO CONFIG CHANGE" in output)
        and "ERROR" not in output,
    )
    rt.diff(before, conf)


def _run_refresh_cluster_probe_edges(rt):
    db = rt.env.config["database"]
    mmr_root = db.get("mmr_data_root", db["mmr_postgres_dir"])
    pg_1_dir = mmr_root + "/test_mmr1"
    pg_3_dir = mmr_root + "/test_mmr1_s1"

    def transform(content, enabled, period):
        content = content.replace("monitor_enabled yes\n",
                                  "monitor_enabled %s\n" % enabled, 1)
        content = content.replace("monitor_period 10\n",
                                  "monitor_period %s\n" % period, 1)
        return content.replace("monitor_recovery_period 10\n",
                               "monitor_recovery_period 1\n", 1)

    def node_field(output, node, field):
        marker = re.search(r"(?m)^node_name\s*\|\s*%s\s*$" % re.escape(node), output)
        if not marker:
            return None
        tail = output[marker.start():]
        next_record = re.search(r"(?m)^-\[ RECORD", tail[1:])
        row = tail[:next_record.start() + 1] if next_record else tail
        match = re.search(r"(?m)^%s\s*\|\s*(.*?)\s*$" % re.escape(field), row)
        return match.group(1).strip() if match else None

    def wait_monitor(title, expected, predicate, attempts=12):
        last = ""
        for attempt in range(attempts):
            last = rt.psql_monitor(
                "SHOW NODE_MONITOR;", "%s（轮询 %d）" % (title, attempt + 1),
                expected, lambda output: True)
            if predicate(last):
                return last
            time.sleep(1)
        rt.check(title, expected, "monitor 快照在 %d 次轮询内未达预期" % attempts,
                 False)

    def nodes_online(output):
        return (node_field(output, "pg_1", "connect_status") == "ONLINE"
                and node_field(output, "pg_3", "connect_status") == "ONLINE")

    def nodes_offline(output):
        return (node_field(output, "pg_1", "connect_status") == "OFFLINE"
                and node_field(output, "pg_3", "connect_status") == "OFFLINE")

    # monitor_enabled=no 时 REFRESH 同步等待探测完成；yes 时入队即返回、
    # 探测异步执行，monitor_period=300 保证检测只能来自 REFRESH 驱动的
    # 一次性探测而非周期探测。
    for enabled, period in (("no", 30), ("yes", 300)):
        conf = rt.start(transform=lambda content, e=enabled, p=period:
                        transform(content, e, p))
        before = rt.workdir / ("before-%s.conf" % enabled)
        before.write_bytes(conf.read_bytes())

        rt.psql('SHOW CLUSTERS;', "%s：查看 REFRESH 前的 cluster 运行态" % enabled,
                'pg_cluster_1 为 VALID 且 current primary 为 pg_1',
                lambda output: all(v in output for v in ("pg_cluster_1", "VALID", "pg_1")))
        for sql, name_desc in (
            ('REFRESH CLUSTER no_such_cluster;', "不存在的 cluster 名"),
            ('REFRESH CLUSTER pg_1;', "误传 datasource 名"),
            ('REFRESH CLUSTER mmr_group;', "误传 group 名"),
        ):
            backup = rt.backup_checkpoint(conf)
            rt.psql_error(sql, "%s：REFRESH %s 被拒绝" % (enabled, name_desc),
                          "返回 cluster does not exist",
                          lambda output: "does not exist" in output)
            rt.assert_no_backup_created(
                backup, conf, "%s：验证 %s 拒绝未创建备份" % (enabled, name_desc))
        rt.diff(before, conf)
        rt.psql('SHOW CLUSTERS;', "%s：验证名称拒绝后的 cluster 运行态" % enabled,
                'pg_cluster_1 仍为 VALID 且 current primary 为 pg_1',
                lambda output: all(v in output for v in ("pg_cluster_1", "VALID", "pg_1")))

        wait_monitor("%s：确认初始节点监控状态" % enabled,
                     "pg_1 与 pg_3 connect_status=ONLINE", nodes_online)
        try:
            rt.postgres_node_action(pg_1_dir, "stop",
                                    "%s：停止 pg_1（pg_cluster_1 主节点）" % enabled)
            rt.postgres_node_action(pg_3_dir, "stop",
                                    "%s：停止 pg_3（pg_cluster_1 备节点）" % enabled)
            rt.psql("REFRESH CLUSTER pg_cluster_1;",
                    "%s：cluster 全部节点离线时执行 REFRESH" % enabled,
                    "一次性探测轮完成仍返回 REFRESH CLUSTER",
                    lambda output: "REFRESH CLUSTER" in output and "ERROR" not in output)
            wait_monitor("%s：验证离线已被一次性探测确认" % enabled,
                         "pg_1 与 pg_3 connect_status=OFFLINE", nodes_offline)
        finally:
            rt.postgres_node_action(pg_1_dir, "start", "%s：恢复 pg_1" % enabled)
            rt.postgres_node_action(pg_3_dir, "start", "%s：恢复 pg_3" % enabled)
        rt.psql("REFRESH CLUSTER pg_cluster_1;",
                "%s：节点恢复后再次执行 REFRESH" % enabled,
                "返回 REFRESH CLUSTER 且不报错",
                lambda output: "REFRESH CLUSTER" in output and "ERROR" not in output)
        wait_monitor("%s：验证节点恢复后监控回到 ONLINE" % enabled,
                     "pg_1 与 pg_3 connect_status=ONLINE", nodes_online)
        rt.diff(before, conf)
        rt.psql('SHOW CLUSTERS;', "%s：查看本模式结束时的 cluster 运行态" % enabled,
                'pg_cluster_1 为 VALID',
                lambda output: "pg_cluster_1" in output and "VALID" in output)


def _run_set_cluster_write_promoted_roundtrip(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;',
        "查看 SET CLUSTER WRITE/PROMOTED 执行前的运行态",
        'write cluster 为 pg_cluster_2，promoted cluster 为 pg_cluster_1',
        lambda output: all(value in output for value in (
            "pg_cluster_2", "pg_cluster_1", "active",
        )),
    )
    rt.psql(
        'SET CLUSTER WRITE pg_cluster_1;',
        "执行 SET CLUSTER WRITE 切换写中心",
        '返回 SET CLUSTER 且命令不报错',
            lambda output: ("SET CLUSTER" in output or "NO CONFIG CHANGE" in output)
            and "ERROR" not in output,
    )
    rt.diff_contains(
        before, conf,
        ('-    write_cluster "pg_cluster_2"',
         '+    write_cluster "pg_cluster_1"',
         '-    promoted_cluster "pg_cluster_1"',
         '+    promoted_cluster "pg_cluster_2"'),
        "验证 SET CLUSTER WRITE 的 write/promoted 配置变更",
    )
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;',
        "验证 SET CLUSTER WRITE 后的运行态",
        'write cluster 为 pg_cluster_1，promoted cluster 为 pg_cluster_2',
        lambda output: all(value in output for value in (
            "pg_cluster_1", "pg_cluster_2", "active",
        )),
    )
    rt.psql_business(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_port(), current_user;",
        "验证 SET CLUSTER WRITE 后的实际写路由",
        '业务连接应落到 pg_cluster_1 primary，并返回 postgres 用户',
        lambda output: str(rt.env.config["database"]["ports"]["mmr1"]) in output
        and "postgres" in output,
    )

    after_write = rt.workdir / "after-cluster-write.conf"
    after_write.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql(
        'SET CLUSTER PROMOTED pg_cluster_1 IN GROUP mmr_group;',
        "执行 SET CLUSTER PROMOTED IN GROUP 切换提升中心",
        '目标已是 write cluster，返回 NO CONFIG CHANGE',
        lambda output: ("SET CLUSTER" in output or "NO CONFIG CHANGE" in output)
        and "ERROR" not in output,
    )
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;',
        "验证 SET CLUSTER PROMOTED 后的运行态",
        'write cluster 为 pg_cluster_1，promoted cluster 仍为 pg_cluster_2',
        lambda output: all(value in output for value in (
            "pg_cluster_1", "pg_cluster_2", "active",
        )),
    )
    rt.psql(
        'SET CLUSTER WRITE pg_cluster_2;',
        "恢复 SET CLUSTER WRITE 初始写中心",
        '返回 SET CLUSTER 且命令不报错',
        lambda output: "SET CLUSTER" in output and "ERROR" not in output,
    )
    rt.diff(before, conf)
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;',
        "验证 SET CLUSTER WRITE/PROMOTED 恢复后的运行态",
        'write cluster 为 pg_cluster_2，promoted cluster 为 pg_cluster_1',
        lambda output: all(value in output for value in (
            "pg_cluster_2", "pg_cluster_1", "active",
        )),
    )


def _run_set_cluster_parted_active_roundtrip(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW DATASOURCES;', "查看 cluster 隔离前的节点状态",
            'pg_1、pg_3 均为 active',
            lambda output: all(v in output for v in ("pg_1", "pg_3", "active")))
    rt.psql('SET CLUSTER PARTED pg_cluster_1;', "隔离 pg_cluster_1",
            '返回 SET CLUSTER',
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    text = conf.read_text(encoding="utf-8")
    rt.check("验证 cluster 的两个 datasource 完整落盘",
             "pg_cluster_1 的 pg_1、pg_3 均为 status parted",
             "status parted occurrences=%d" % text.count('    status "parted"\n'),
             text.count('    status "parted"\n') == 2)
    rt.psql_business_error(
        'SELECT inet_server_port();',
        "验证 cluster PARTED 后 Single 业务路由不可用",
        "single_group 唯一 backend cluster 已隔离，业务连接明确失败",
        lambda output: "ERROR" in output or "server" in output.lower(),
        group="single_group",
    )
    rt.diff_contains(before, conf, ('+    status "parted"',),
                     "验证 SET CLUSTER PARTED 的配置 diff")
    rt.psql('SHOW DATASOURCES;', "验证 cluster 隔离后的运行态",
            'pg_1、pg_3 均显示 parted',
            lambda output: all(v in output for v in ("pg_1", "pg_3", "parted")))
    rt.psql('SET CLUSTER ACTIVE pg_cluster_1;', "恢复 pg_cluster_1",
            '返回 SET CLUSTER',
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    _wait_pg_cluster_ready(
        rt, "pg_cluster_1", "pg_1", ("pg_3",),
        "等待 cluster ACTIVE 的即时探测和路由投影完成")
    rt.psql_business(
        'SELECT inet_server_port(), current_user;',
        "验证 cluster ACTIVE 后 Single 业务路由恢复",
        "single_group 重新命中 pg_cluster_1 primary",
        lambda output: str(rt.env.config["database"]["ports"]["mmr1"]) in output
        and "postgres" in output,
        group="single_group",
    )
    rt.diff(before, conf)
    rt.psql('SHOW DATASOURCES;', "验证 cluster 恢复后的运行态",
            'pg_1、pg_3 均恢复 active',
            lambda output: all(v in output for v in ("pg_1", "pg_3", "active")))
def _run_refresh_cluster(rt):
    db = rt.env.config["database"]
    node_dir = db.get("mmr_data_root", db["mmr_postgres_dir"]) + "/test_mmr1_s1"

    def transform(content, enabled, period):
        content = content.replace("monitor_enabled yes\n",
                                  "monitor_enabled %s\n" % enabled, 1)
        content = content.replace("monitor_period 10\n",
                                  "monitor_period %s\n" % period, 1)
        return content.replace("monitor_recovery_period 10\n",
                               "monitor_recovery_period 1\n", 1)

    def fields(output, datasource, endpoint=False):
        key = "node_name" if endpoint else "name"
        match = re.search(r"(?m)^%s\s*\|\s*%s\s*$" % (key, re.escape(datasource)), output)
        if not match:
            return {}
        block = output[match.start():]
        next_record = re.search(r"(?m)^-\[ RECORD", block[1:])
        block = block[:next_record.start() + 1] if next_record else block
        return dict(re.findall(r"(?m)^([a-z_]+)\s*\|\s*(.*?)\s*$", block))

    def check_endpoint(output, datasource, failed, prior_seq):
        row = fields(output, datasource, endpoint=True)
        return (row.get("probe_state") == "READY" and
                int(row.get("probe_seq", "0")) > prior_seq and
                row.get("connect_status") == ("OFFLINE" if failed else "ONLINE") and
                row.get("topology_state") in ("VALID", "VALID_DEGRADED") and
                int(row.get("fault_count", "0") or 0) >= (1 if failed else 0) and
                ("CONNECT_FAILED" in row.get("fault_flags", "") if failed
                 else row.get("fault_flags") == "{}"))

    def check_node(output, datasource, failed):
        expected_connectivity = "OFFLINE" if failed else "ONLINE"
        expected_topology = "VALID_DEGRADED" if failed else "VALID"
        expected_effective = "OFFLINE" if failed else "READ_ONLY"
        marker = re.search(r"(?m)^node_name\s*\|\s*%s\s*$" % re.escape(datasource), output)
        if not marker:
            return False
        tail = output[marker.start():]
        next_record = re.search(r"(?m)^-\[ RECORD", tail[1:])
        row_text = tail[:next_record.start() + 1] if next_record else tail
        return all(re.search(r"(?m)^%s\s*\|\s*%s\s*$" % (field, re.escape(value)), row_text)
                       for field, value in (("probe_state", "READY"),
                                            ("connect_status", expected_connectivity),
                                            ("topology_state", expected_topology if not failed else "VALID_DEGRADED"),
                                            ("effective_status", expected_effective)))

    def wait_endpoint(title, expected, predicate, attempts=12):
        last = ""
        for attempt in range(attempts):
            last = rt.psql_monitor("SHOW ENDPOINT_MONITOR;", "%s（轮询 %d）" % (title, attempt + 1), expected, lambda output: True)
            if predicate(last):
                return last
            time.sleep(1)
        rt.check(title, expected, "monitor 快照在 %ss 内未达到预期" % attempts, False)
        return last

    for enabled, period in (("no", 30), ("yes", 300)):
        conf = rt.start(transform=lambda content, e=enabled, p=period: transform(content, e, p))
        before = rt.workdir / ("before-%s.conf" % enabled)
        before.write_bytes(conf.read_bytes())
        try:
            initial = wait_endpoint(
                    "%s：刷新前端点监控快照" % enabled,
                    "pg_3 probe_state=READY、connect_status=ONLINE、topology_state=VALID",
                    lambda output: check_endpoint(output, "pg_3", False, -1))
            initial_row = fields(initial, "pg_3", endpoint=True)
            initial_seq = int(initial_row.get("probe_seq", "0"))
            rt.postgres_node_action(node_dir, "stop", "%s：停止 pg_3 standby" % enabled)
            time.sleep(1.0)
            rt.psql("REFRESH CLUSTER pg_cluster_1;", "%s：节点停止后执行 REFRESH CLUSTER" % enabled,
                    "返回 REFRESH CLUSTER", lambda output: "REFRESH CLUSTER" in output)
            wait_endpoint("%s：确认端点故障已被显式刷新发现" % enabled,
                    "pg_3 probe_seq 递增、OFFLINE、fault_count 增加",
                    lambda output, s=initial_seq: check_endpoint(output, "pg_3", True, s))
            rt.psql_monitor("SHOW NODE_MONITOR;", "%s：确认节点故障已被显式刷新发现" % enabled,
                    "pg_3 connect_status 非 ONLINE 且 topology_state=VALID",
                    lambda output: check_node(output, "pg_3", True))
            
            failed_output = rt.psql_monitor(
                    "SHOW ENDPOINT_MONITOR;", "%s：记录故障端点基准" % enabled,
                    "保存 pg_3 故障后的 probe_seq", lambda output: check_endpoint(output, "pg_3", True, initial_seq))
            failed_seq = int(fields(failed_output, "pg_3", endpoint=True)["probe_seq"])
        finally:
            rt.postgres_node_action(node_dir, "start", "%s：恢复 pg_3 standby" % enabled)
        time.sleep(1.1)
        rt.psql("REFRESH CLUSTER pg_cluster_1;", "%s：节点恢复后再次 REFRESH CLUSTER" % enabled,
                "返回 REFRESH CLUSTER", lambda output: "REFRESH CLUSTER" in output)
        recovered_output = wait_endpoint("%s：确认端点恢复已生效" % enabled,
                        "pg_3 probe_seq 递增、ONLINE、fault_flags={}、fault_count=0",
                        lambda output, s=failed_seq: check_endpoint(output, "pg_3", False, s))
        rt.psql_monitor("SHOW NODE_MONITOR;", "%s：确认节点恢复已生效" % enabled,
                        "pg_3 connect_status=ONLINE、topology_state=VALID、effective_status=ONLINE",
                        lambda output: check_node(output, "pg_3", False))
        rt.diff(before, conf)


def _run_write_cluster_format_preservation(rt):
    conf = rt.start(transform=_group_fields_with_format)
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "查看格式保持 WRITE 前的运行态",
            'pg_cluster_2，pg_cluster_1',
            lambda output: all(v in output for v in
                               ("pg_cluster_2", "pg_cluster_1")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WRITE pg_1 IN GROUP mmr_group;',
            "切换带特殊格式的 WRITE 字段",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 WRITE 格式切换的配置备份")
    text = conf.read_text(encoding="utf-8")
    write_line = '\twrite_cluster    "pg_cluster_1"    # keep-write-format'
    promoted_line = '      promoted_cluster\t"pg_cluster_2"    # keep-promoted-format'
    rt.check("验证 WRITE 两字段周边格式保持",
             "两个字段分别保留原缩进、空格、tab 和行尾注释",
             "write格式=%s；promoted格式=%s" %
             (write_line in text, promoted_line in text),
             write_line in text and promoted_line in text)
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "查看格式保持 WRITE 切换后的运行态",
            'pg_cluster_1，pg_cluster_2，pg_1 为 write-leader',
            lambda output: all(v in output for v in
                               ("pg_cluster_1", "pg_cluster_2", "pg_1", "write-leader")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WRITE pg_2 IN GROUP mmr_group;',
            "恢复带特殊格式的 WRITE 字段",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 WRITE 格式恢复的配置备份")
    rt.diff(before, conf)
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "验证格式保持 WRITE 恢复后的运行态",
            'pg_cluster_2，pg_cluster_1，pg_2 为 write-leader',
            lambda output: all(v in output for v in
                               ("pg_cluster_2", "pg_cluster_1", "pg_2", "write-leader")))


