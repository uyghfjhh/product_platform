"""HA console command executors: BATCH."""

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

__all__ = ['_run_batch_mixed_no_change_and_change', '_run_batch_status_invalid_target_atomicity', '_run_batch_weight_atomicity', '_run_bulk_30_datasource_weight_roundtrip', '_run_bulk_30_group_write_roundtrip', '_run_bulk_30_groups_invalid_target', '_run_bulk_30_groups_non_mmr', '_run_comprehensive_all_groups_and_commands', '_run_default_group_expansion_34', '_run_duplicate_and_mixed_status_targets', '_run_mixed_topology_pool_mode_batch_write', '_run_name_endpoint_status_deduplication', '_run_set_node_write_in_groups_duplicate_group']


def _run_bulk_30_groups_non_mmr(rt):
    conf = rt.start(transform=_add_bulk_mmr_groups)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    names = ['bulk_mmr_%02d' % i for i in range(1, 31)]
    group_list = ','.join(names + ['rep_group'])
    rt.psql('SHOW GROUP_ROUTING bulk_mmr_01;', "查看混入非 MMR group 命令前状态", "pg_cluster_2",
            lambda output: "bulk_mmr_01" in output and "pg_cluster_2" in output)
    rt.psql('SHOW GROUP_ROUTING rep_group;', "查看非 MMR group 命令前状态", "rep_group 为 replication",
            lambda output: "rep_group" in output and "replication" in output)
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WRITE pg_1 IN GROUPS (%s);' % group_list,
                  "30 个 MMR group 混入非 MMR group 时原子拒绝",
                  "返回 rep_group 和 is not an MMR group",
                  lambda output: "rep_group" in output and "is not an MMR group" in output)
    rt.assert_no_backup_created(backup, conf, "验证混入非 MMR group 未创建备份")
    rt.diff(before, conf)
    unchanged = _bulk_groups_have(conf.read_text(encoding="utf-8"), "pg_cluster_2", "pg_cluster_1")
    rt.check("验证混入非 MMR 后 30 个 group 未部分修改", "全部保持初始状态", "unchanged=%s" % unchanged, unchanged)
    rt.psql('SHOW GROUP_ROUTING bulk_mmr_30;', "查看混入非 MMR 命令后 MMR 状态", "pg_cluster_2",
            lambda output: "bulk_mmr_30" in output and "pg_cluster_2" in output)
    rt.psql('SHOW GROUP_ROUTING rep_group;', "查看混入非 MMR 命令后 REP 状态", "rep_group 仍为 replication",
            lambda output: "rep_group" in output and "replication" in output)


def _run_default_group_expansion_34(rt):
    conf = rt.start(transform=_add_34_mmr_groups)
    _wait_pg_cluster_ready(rt, "pg_cluster_1", "pg_1", ("pg_3",))
    _wait_pg_cluster_ready(rt, "pg_cluster_2", "pg_2", ("pg_4",))
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    names = ['expand_mmr_%02d' % i for i in range(1, 35)]
    rt.psql('SHOW GROUP_ROUTING expand_mmr_01;', "查看 34 组默认展开前状态", "pg_cluster_2",
            lambda output: "expand_mmr_01" in output and "pg_cluster_2" in output)
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WRITE pg_1;', "省略 IN GROUPS 自动展开 34 个 MMR group",
            "返回 SET NODE 且命令不报错", lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 34 组默认展开备份")
    text = conf.read_text(encoding="utf-8")
    changed = all('write_cluster "pg_cluster_1"' in _datasource_block(text.replace('group "', 'datasources "'), name) for name in names)
    rt.check("验证默认范围完整覆盖 34 个 group", "34 个 group 全部切换", "34 groups changed=%s" % changed, changed)
    rt.diff_contains(before, conf, ('-    write_cluster "pg_cluster_2"', '+    write_cluster "pg_cluster_1"'), "验证 34 组默认展开配置 diff")
    rt.psql('SHOW GROUP_ROUTING expand_mmr_34;', "查看 34 组默认展开后状态", "pg_cluster_1",
            lambda output: "expand_mmr_34" in output and "pg_cluster_1" in output)
    idempotent = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WRITE pg_1;', "重复执行 34 组默认展开命令并记录 NO CHANGE 输出",
            "返回 NO CONFIG CHANGE；NO CHANGE 详情写入 fbasecman.log",
            lambda output: "NO CONFIG CHANGE" in output and "ERROR" not in output)
    rt.assert_no_backup_created(idempotent, conf,
                                "验证 34 组 NO CHANGE 未创建备份")
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WRITE pg_2;', "恢复默认展开的 34 个 MMR group", "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 34 组默认展开恢复备份")
    rt.diff(before, conf)


def _run_bulk_30_datasource_weight_roundtrip(rt):
    conf = rt.start(transform=_add_bulk_datasources)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    names = ['bulk_ds_%02d' % index for index in range(1, 31)]
    assignments_11 = ','.join('%s=11' % name for name in names)
    assignments_10 = ','.join('%s=10' % name for name in names)
    rt.psql('SHOW DATASOURCES;', "查看 30 节点权重修改前的控制台状态",
            "bulk_ds_01 和 bulk_ds_30 均为 active",
            lambda output: all(v in output for v in
                               ("bulk_ds_01", "bulk_ds_30", "active")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT %s;' % assignments_11,
            "一次修改 30 个 datasource 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 30 节点权重修改备份")
    changed = conf.read_text(encoding="utf-8")
    all_changed = _bulk_datasources_have_weight(changed, 11)
    rt.check("验证 30 个 datasource 全部原子落盘",
             "bulk_ds_01 至 bulk_ds_30 均为 weight 11",
             "30 datasources changed=%s" % all_changed, all_changed)
    rt.diff_contains(before, conf, ('-    weight 10', '+    weight 11'),
                     "验证 30 节点权重配置 diff")
    rt.psql('SHOW DATASOURCES;', "查看 30 节点权重修改后的控制台状态",
            "bulk_ds_01 和 bulk_ds_30 均仍为 active，Reload 后节点完整",
            lambda output: all(v in output for v in
                               ("bulk_ds_01", "bulk_ds_30", "active")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT %s;' % assignments_10,
            "一次恢复 30 个 datasource 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 30 节点权重恢复备份")
    all_restored = _bulk_datasources_have_weight(conf.read_text(encoding="utf-8"), 10)
    rt.check("验证 30 个 datasource 全部恢复",
             "bulk_ds_01 至 bulk_ds_30 均恢复 weight 10",
             "30 datasources restored=%s" % all_restored, all_restored)
    rt.diff(before, conf)
    rt.psql('SHOW DATASOURCES;', "查看 30 节点权重恢复后的控制台状态",
            "bulk_ds_01 和 bulk_ds_30 均为 active，恢复后节点完整",
            lambda output: all(v in output for v in
                               ("bulk_ds_01", "bulk_ds_30", "active")))


def _run_batch_weight_atomicity(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW NODES;', "查看批量修改前的节点权重",
            'pg_3、pg_4 的初始 weight 均为 10',
            lambda output: all(v in output for v in ("pg_3", "pg_4", "10")))
    rt.psql('SET NODE WEIGHT pg_3=11,pg_4=11;', "批量修改 pg_3、pg_4 权重",
            '返回 SET NODE', lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.diff_contains(before, conf, ('+    weight 11',), "验证两个 datasource 的批量配置 diff")
    text = conf.read_text(encoding='utf-8')
    rt.check("验证批量修改完整落盘", "pg_3、pg_4 均为 weight 11",
             "weight 11 occurrences=%d" % text.count('    weight 11\n'),
             text.count('    weight 11\n') == 2)
    rt.psql('SHOW NODES;', "查看批量修改后的运行态",
            'pg_3、pg_4 的 weight 均为 11',
            lambda output: all(v in output for v in ("pg_3", "pg_4", "11")))
    rt.psql('SET NODE WEIGHT pg_3=10,pg_4=10;', "批量恢复 pg_3、pg_4 权重",
            '返回 SET NODE', lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.diff(before, conf)
    rt.psql_error('SET NODE WEIGHT pg_3=11,no_such_node=11;',
                  "验证批量目标含无效节点时整体拒绝",
                  '返回 no_such_node does not exist 错误',
                  lambda output: all(v in output for v in
                                     ("ERROR:", "no_such_node", "does not exist")))
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看原子拒绝后的节点权重",
            'pg_3、pg_4 仍为初始 weight 10',
            lambda output: all(v in output for v in ("pg_3", "pg_4", "10")))


def _run_name_endpoint_status_deduplication(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    host = rt.env.config["database"]["mmr_host"]
    port = rt.env.config["database"]["ports"]["mmr1_standby1"]
    target_list = "pg_3,%s:%s" % (host, port)
    rt.psql('SHOW DATASOURCES;', "查看名称/endpoint 去重命令前的运行态",
            'pg_3 为 active',
            lambda output: "pg_3" in output and "active" in output)
    backup = rt.backup_checkpoint(conf)
    rt.psql(
        'SET NODE PARTED %s;' % target_list,
        "使用名称和 endpoint 隔离同一 datasource",
        '返回 SET NODE 且命令不报错',
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.assert_backup_created(backup, conf, "验证名称/endpoint PARTED 的配置备份")
    rt.diff_contains(before, conf,
                     ('-    status "active"', '+    status "parted"'),
                     "验证名称/endpoint 去重后的配置 diff")
    rt.psql('SHOW DATASOURCES;', "查看名称/endpoint PARTED 后的运行态",
            'pg_3 为 parted',
            lambda output: "pg_3" in output and "parted" in output)
    backup = rt.backup_checkpoint(conf)
    rt.psql(
        'SET NODE ACTIVE %s;' % target_list,
        "使用名称和 endpoint 恢复同一 datasource",
        '返回 SET NODE 且命令不报错',
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.assert_backup_created(backup, conf, "验证名称/endpoint ACTIVE 的配置备份")
    rt.diff(before, conf)
    _wait_pg_cluster_ready(
        rt, "pg_cluster_1", "pg_1", ("pg_3",),
        "验证名称/endpoint ACTIVE 后 monitor 投影恢复")


def _run_set_node_write_in_groups_duplicate_group(rt):
    conf = rt.start(transform=_add_second_mmr_group)
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;',
        "查看重复 group 命令前的运行态",
        'write cluster 为 pg_cluster_2，pg_2 为 write-leader',
        lambda output: all(v in output for v in ("pg_cluster_2", "pg_2", "write-leader")),
    )
    backup = rt.backup_checkpoint(conf)
    rt.psql_error(
        'SET NODE WRITE pg_1 IN GROUPS (mmr_group,mmr_group);',
        "执行重复 group 的 IN GROUPS 命令",
        '返回 ERROR 且命令被拒绝',
        lambda output: "ERROR:" in output,
    )
    rt.assert_no_backup_created(backup, conf, "验证重复 group 未创建备份")
    rt.diff(before, conf)
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;',
        "验证重复 group 命令后的运行态",
        'write cluster 仍为 pg_cluster_2，pg_2 仍为 write-leader',
        lambda output: all(v in output for v in ("pg_cluster_2", "pg_2", "write-leader")),
    )


def _run_batch_status_invalid_target_atomicity(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW DATASOURCES;', "查看批量状态错误命令前的运行态",
            'pg_3、pg_4 均为 active',
            lambda output: all(v in output for v in ("pg_3", "pg_4", "active")))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error(
        'SET NODE PARTED pg_3,no_such_node,pg_4;',
        "执行混入不存在 datasource 的批量 PARTED 命令",
        '返回 ERROR，包含 no_such_node 和 does not exist',
        lambda output: all(v in output for v in ("ERROR:", "no_such_node", "does not exist")),
    )
    rt.assert_no_backup_created(backup, conf, "验证批量状态错误未创建备份")
    rt.diff(before, conf)
    rt.psql('SHOW DATASOURCES;', "验证批量状态错误命令后的运行态",
            'pg_3、pg_4 均仍为 active',
            lambda output: all(v in output for v in ("pg_3", "pg_4", "active")))


def _run_comprehensive_all_groups_and_commands(rt):
    """Exercise the supported HA command matrix with one complex fixture."""
    conf = rt.start(transform=_comprehensive_transform(rt))
    before = rt.workdir / "comprehensive.initial.conf"
    before.write_bytes(conf.read_bytes())
    rendered_bytes = conf.read_bytes()
    rendered = rendered_bytes.decode("utf-8")
    rt.check(
        "阶段 1：检查线上配置格式特征",
            "中文/英文注释、Tab、非对齐缩进、连续空行、CRLF、末尾无换行和字符串内 # 均保留",
            "comment_cn=%s comment_en=%s tab=%s uneven_indent=%s blank_line=%s crlf=%s no_final_newline=%s hash_in_string=%s" % (
            "中文" in rendered,
            "# 线上共享物理节点" in rendered,
            "\t" in rendered,
            "  write_cluster \"pg_cluster_1\"" in rendered,
                "\n\n    check \"auto\"" in rendered,
                b"\r\n" in rendered_bytes,
                not rendered_bytes.endswith(b"\n"),
                "#" in rendered,
        ),
        all(marker in rendered for marker in (
            "中文", "# 线上共享物理节点", "\t",
            "  write_cluster \"pg_cluster_1\"",
            "\n\n    check \"auto\"", "#",
        )) and b"\r\n" in rendered_bytes and not rendered_bytes.endswith(b"\n"),
    )

    rt.psql("SHOW GROUPS;", "阶段 1：检查 17 个 group 和 pool/rw_split 配置",
            "17 个 group、四类 group mode 及 none/hint/port/sql_parse 均可见",
            lambda output: all(value in output for value in (
                "mmr_group", "mmr_group_h", "rep_group_c", "balance_group_c",
                "single_group_c", "rep_group", "balance_group", "single_group",
                "hint", "port", "sql_parse",)),)
    rt.check("阶段 1：检查 pool 模式组合", "session/transaction/statement 均在配置中",
             "pool transaction/session/statement present=%s" % all(
                 token in conf.read_text(encoding="utf-8")
                 for token in ('pool "transaction"', 'pool "session"', 'pool "statement"')),
             all(token in conf.read_text(encoding="utf-8")
                 for token in ('pool "transaction"', 'pool "session"', 'pool "statement"')))
    rt.psql(
        "SHOW GROUPS;",
        "阶段 1：检查 MMR/REP 主组的多用户和 session pool 场景",
        "mmr_group 和 rep_group 均加载多个用户，并包含各自的 session pool 用户",
        lambda output: all(value in output for value in (
            "mmr_group", "ha_mmr_session", "ha_mmr_hint_tx",
            "rep_group", "ha_rep_session", "ha_rep_hint_tx", "postgres")),
    )
    rt.psql("SHOW GROUP_MEMBERS;", "阶段 1：检查 group 成员",
            "MMR、REP、Balance、Single 成员均可见",
            lambda output: all(value in output for value in ("pg_1", "pg_2", "pg_3", "pg_4")),)
    rt.psql("SHOW NODES;", "阶段 1：检查 datasource 节点和权重",
            "四个 datasource 均可见",
            lambda output: all(value in output for value in ("pg_1", "pg_2", "pg_3", "pg_4")),)
    rt.psql("SHOW NODE_STATUS;", "阶段 1：检查节点初始状态",
            "节点状态输出可用",
            lambda output: "pg_1" in output and "pg_2" in output)
    for group in ("mmr_group", "rep_group", "balance_group", "single_group"):
        rt.psql("SHOW GROUP_ROUTING %s;" % group,
                "阶段 2：检查 %s 运行态路由" % group,
                "%s route 返回有效状态" % group,
                lambda output, group=group: group in output)
    rt.psql_business(
        "SELECT inet_server_addr(), inet_server_port(), pg_is_in_recovery(), current_user;",
        "阶段 3：none 模式真实业务路由",
        "返回后端地址、端口、恢复状态和用户",
        lambda output: "inet_server_port" in output or "postgres" in output,
    )
    rt.psql_business(
        "SELECT current_user, inet_server_port(), pg_backend_pid();",
        "阶段 3：MMR 多用户组的 session pool 用户真实路由",
        "客户端 ha_mmr_session 以 session pool 连接 mmr_group，后端使用 storage_user postgres 并命中当前写中心 pg_cluster_2",
        lambda output: "postgres" in output and
        str(rt.env.config["database"]["ports"]["mmr2"]) in output,
        group="mmr_group", user="ha_mmr_session", retry_timeout=30,
    )
    rt.psql_business(
        "SELECT current_user, inet_server_port(), pg_is_in_recovery(), pg_backend_pid();",
        "阶段 3：REP 多用户组的 session pool 用户真实路由",
        "客户端 ha_rep_session 以 session pool 连接 rep_group，后端使用 storage_user postgres 并命中 pg_cluster_1 primary",
        lambda output: "postgres" in output and
        str(rt.env.config["database"]["ports"]["mmr1"]) in output and
        " f " in output,
        group="rep_group", user="ha_rep_session", retry_timeout=30,
    )
    for user, group, label in (
            ("ha_mmr_hint_tx", "mmr_group", "MMR hint"),
            ("ha_mmr_port_tx", "mmr_group", "MMR port"),
            ("ha_mmr_sql_tx", "mmr_group", "MMR sql_parse"),
            ("ha_rep_hint_tx", "rep_group", "REP hint"),
            ("ha_rep_port_tx", "rep_group", "REP port"),
            ("ha_rep_sql_tx", "rep_group", "REP sql_parse")):
        rt.psql_business(
            "SELECT inet_server_addr(), inet_server_port(), pg_is_in_recovery(), current_user;",
            "阶段 3：%s 用户真实路由" % label,
            "%s 用户能完成 SQL 并返回后端端口" % label,
            lambda output: "inet_server_port" in output or "postgres" in output,
            group=group, user=user,
        )

    # Mixed endpoint/name batch: pg_3 is named, pg_4 is addressed by IPv4 endpoint.
    rt.psql(
        "SET NODE PARTED pg_3,%s:%s;" % (
            rt.env.config["database"]["mmr_host"],
            rt.env.config["database"]["ports"]["mmr2_standby1"]),
        "阶段 4：名称与 IPv4 host:port 混合批量 PARTED",
        "命令成功并按物理节点更新状态",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.psql(
        "SHOW NODE_STATUS;",
        "阶段 4：检查混合寻址后的节点状态",
        "pg_3 和 pg_4 均为 parted",
        lambda output: "pg_3" in output and "pg_4" in output and "parted" in output.lower(),
    )
    rt.psql(
        "SET NODE ACTIVE pg_3,%s:%s;" % (
            rt.env.config["database"]["mmr_host"],
            rt.env.config["database"]["ports"]["mmr2_standby1"]),
        "阶段 4：恢复混合寻址节点 ACTIVE",
        "命令成功",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    _wait_pg_cluster_ready(rt, "pg_cluster_1", "pg_1", ("pg_3",))
    _wait_pg_cluster_ready(rt, "pg_cluster_2", "pg_2", ("pg_4",))
    rt.psql(
        "SET NODE PARTED pg_3;",
        "阶段 4：准备批量部分无需修改场景",
        "仅 pg_3 进入 parted",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.psql(
        "SET NODE ACTIVE (pg_1,pg_3);",
        "阶段 4：批量 ACTIVE 部分目标无需修改",
        "pg_1 已 active 保持不变，pg_3 从 parted 恢复",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    _wait_pg_cluster_ready(
        rt, "pg_cluster_1", "pg_1", ("pg_3",),
        "阶段 4：验证部分无需修改的 ACTIVE 批量 monitor 投影")

    rt.psql(
        "SET NODE WEIGHT (pg_3=11,%s:%s=12);" % (
            rt.env.config["database"]["mmr_host"],
            rt.env.config["database"]["ports"]["mmr2_standby1"]),
        "阶段 5：批量 WEIGHT 全部目标修改",
        "pg_3 和 endpoint 目标权重均更新",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.psql("SHOW NODES;", "阶段 5：检查批量权重运行态",
            "节点显示权重 11 和 12",
            lambda output: "11" in output and "12" in output)
    rt.psql(
        "SET NODE WEIGHT (pg_3=10,%s:%s=10);" % (
            rt.env.config["database"]["mmr_host"],
            rt.env.config["database"]["ports"]["mmr2_standby1"]),
        "阶段 5：恢复批量权重",
        "权重恢复成功",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )

    rt.psql_error(
        "SET NODE WEIGHT (pg_3=11,no_such_node=12);",
        "阶段 5：批量 WEIGHT 部分非法目标原子拒绝",
        "返回错误且合法目标不被部分写入",
        lambda output: "ERROR" in output and "no_such_node" in output,
    )

    groups_write_2 = "mmr_group,mmr_group_c,mmr_group_e,mmr_group_g"
    groups_write_1 = "mmr_group_b,mmr_group_d,mmr_group_f,mmr_group_h"
    all_mmr_groups = groups_write_2 + "," + groups_write_1
    rt.psql(
        "SET NODE WRITE pg_1 IN GROUPS (%s);" % groups_write_2,
        "阶段 6：4 个 MMR group 批量全修改 WRITE",
        "4 个原写向 pg_cluster_2 的 group 全部切换到 pg_cluster_1",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.check(
        "阶段 6：检查批量全修改配置",
        "8 个 MMR group 当前均写向 pg_cluster_1",
        "write_cluster pg_cluster_1 occurrences=%s" %
        conf.read_text(encoding="utf-8").count('write_cluster "pg_cluster_1"'),
        conf.read_text(encoding="utf-8").count('write_cluster "pg_cluster_1"') == 8,
    )
    rt.psql(
        "SET NODE WRITE pg_2 IN GROUPS (%s);" % groups_write_2,
        "阶段 6：恢复批量全修改的 4 个 MMR group",
        "4 个 group 恢复写向 pg_cluster_2",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.psql(
        "SET NODE WRITE pg_1 IN GROUPS (%s);" % all_mmr_groups,
        "阶段 6：8 个 MMR group 批量部分修改",
        "4 个 group 无需修改，另外 4 个切换到 pg_cluster_1",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.check(
        "阶段 6：检查批量部分修改配置",
        "部分无需修改后 8 个 MMR group 均写向 pg_cluster_1",
        "write_cluster pg_cluster_1 occurrences=%s" %
        conf.read_text(encoding="utf-8").count('write_cluster "pg_cluster_1"'),
        conf.read_text(encoding="utf-8").count('write_cluster "pg_cluster_1"') == 8,
    )
    rt.psql(
        "SET NODE WRITE pg_2 IN GROUPS (%s);" % groups_write_2,
        "阶段 6：恢复批量部分修改的 4 个变更 group",
        "原写中心为 pg_cluster_2 的 4 个 group 完成恢复",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )

    rt.psql_error(
        "SET NODE WRITE pg_1 IN GROUPS (mmr_group,mmr_group);",
        "阶段 6：重复 group 作用域拒绝",
        "返回语法错误且配置不变",
        lambda output: "ERROR" in output,
    )
    rt.psql(
        "SET NODE WRITE pg_1 IN GROUP mmr_group;",
        "阶段 6：MMR 指定 group 切换 WRITE",
        "写中心切换到 pg_cluster_1",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.psql_business(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_port(), pg_is_in_recovery();",
        "阶段 6：WRITE 切换后的真实业务路由",
        "命中 pg_cluster_1 写节点",
        lambda output: str(rt.env.config["database"]["ports"]["mmr1"]) in output,
    )
    rt.psql(
        "SET NODE PROMOTED pg_2 IN GROUP mmr_group;",
        "阶段 7：切换 MMR promoted cluster",
        "目标已是 promoted cluster，返回 SET NODE 或 NO CONFIG CHANGE",
        lambda output: ("SET NODE" in output or "NO CONFIG CHANGE" in output)
        and "ERROR" not in output,
    )
    rt.psql(
        "SET NODE WRITE pg_2 IN GROUP mmr_group;",
        "阶段 7：为恢复 promoted 先恢复原写中心",
        "write cluster 临时恢复到 pg_cluster_2",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.psql(
        "SET NODE PROMOTED pg_1 IN GROUP mmr_group;",
        "阶段 7：恢复 MMR promoted cluster",
        "promoted cluster 已随 WRITE 恢复，返回 SET NODE 或 NO CONFIG CHANGE",
        lambda output: ("SET NODE" in output or "NO CONFIG CHANGE" in output)
        and "ERROR" not in output,
    )
    rt.psql(
        "SET CLUSTER PARTED pg_cluster_1;",
        "阶段 8：SET CLUSTER PARTED 批量隔离 cluster",
        "cluster 成员全部进入 parted",
        lambda output: "SET CLUSTER" in output and "ERROR" not in output,
    )
    rt.psql(
        "SET CLUSTER ACTIVE pg_cluster_1;",
        "阶段 8：SET CLUSTER ACTIVE 恢复 cluster",
        "cluster 成员恢复 active",
        lambda output: "SET CLUSTER" in output and "ERROR" not in output,
    )
    _wait_pg_cluster_ready(
        rt, "pg_cluster_1", "pg_1", ("pg_3",),
        "阶段 8：验证 cluster ACTIVE 后 monitor 投影恢复")
    rt.psql(
        "SET NODE WRITE pg_2 IN GROUP mmr_group;",
        "阶段 10：恢复初始 MMR write cluster",
        "write cluster 已恢复到 pg_cluster_2，返回 SET NODE 或 NO CONFIG CHANGE",
        lambda output: ("SET NODE" in output or "NO CONFIG CHANGE" in output)
        and "ERROR" not in output,
    )
    final_bytes = conf.read_bytes()
    final_text = final_bytes.decode("utf-8")
    rt.check(
        "阶段 10：检查高可用命令后格式内容仍保留",
        "中文注释、Tab、非对齐缩进、空行和行尾注释未被配置写回破坏",
        "format_markers_preserved=%s" % all(marker in final_text for marker in (
            "中文", "# 线上共享物理节点", "\t",
            "  write_cluster \"pg_cluster_1\"",
            "\n\n    check \"auto\"", "#",
        )),
        all(marker in final_text for marker in (
            "中文", "# 线上共享物理节点", "\t",
            "  write_cluster \"pg_cluster_1\"",
            "\n\n    check \"auto\"", "#",
        )) and b"\r\n" in final_bytes and not final_bytes.endswith(b"\n"),
    )
    rt.diff(before, conf)


def _run_mixed_topology_pool_mode_batch_write(rt):
    conf = rt.start(transform=_mixed_topology_transform(rt))
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    group_names = "mmr_group,mmr_hint_mix,mmr_sql_mix"
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "查看混合拓扑批量命令前的基准 MMR 状态",
            "mmr_group 为 VALID 且 pg_cluster_2",
            lambda output: all(v in output for v in ("mmr_group", "active", "pg_cluster_2")))
    rt.psql('SHOW GROUPS;', "查看混合拓扑只读 Balance 配置",
            "balance_read_mix 为 balance/read_only",
            lambda output: all(v in output for v in
                               ("balance_read_mix", "balance", "read_only")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WRITE pg_1 IN GROUPS (%s);' % group_names,
            "混合 topology 中批量切换 3 个 MMR group 的写中心",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证混合 MMR 批量切换备份")
    text = conf.read_text(encoding="utf-8")
    for group in ("mmr_group", "mmr_hint_mix", "mmr_sql_mix"):
        block = _datasource_block(text.replace('group "', 'datasources "'), group)
        rt.check("验证混合配置 group %s 的 write_cluster 已切换" % group,
                 "write_cluster 为 pg_cluster_1",
                 "group=%s switched=%s" % (group, 'write_cluster "pg_cluster_1"' in block),
                 'write_cluster "pg_cluster_1"' in block)
    rt.diff_contains(before, conf,
                     ('-    write_cluster "pg_cluster_2"', '+    write_cluster "pg_cluster_1"'),
                     "验证混合配置只替换目标 MMR 的写中心")
    rt.psql('SHOW GROUP_ROUTING mmr_hint_mix;', "查看混合配置批量切换后的 hint MMR 状态",
            "mmr_hint_mix 为 VALID 且 pg_cluster_1",
            lambda output: all(v in output for v in ("mmr_hint_mix", "active", "pg_cluster_1")))
    rt.psql('SHOW GROUP_ROUTING rep_port_mix;', "确认混合配置 REP port group 未被批量 MMR 命令修改",
            "rep_port_mix 仍为 replication",
            lambda output: "rep_port_mix" in output and "replication" in output)
    expected_mmr1 = str(rt.env.config["database"]["ports"]["mmr1"])
    expected_mmr2 = str(rt.env.config["database"]["ports"]["mmr2"])
    rt.psql_business(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_port(), current_user;",
        "验证批量 WRITE 后 MMR hint 真实路由",
        "mix_hint 写事务命中 pg_cluster_1 primary",
        lambda output: expected_mmr1 in output and "postgres" in output,
        group="mmr_hint_mix", user="mix_hint")
    rt.psql_business(
        "BEGIN; CREATE TEMP TABLE ha_mixed_sql_probe(id int); "
        "SELECT inet_server_port(); ROLLBACK;",
        "验证批量 WRITE 后 MMR sql_parse 真实路由",
        "mix_sql 含 DDL 的事务命中 pg_cluster_1 primary 并回滚",
        lambda output: expected_mmr1 in output and "CREATE TABLE" in output
        and "ROLLBACK" in output,
        group="mmr_sql_mix", user="mix_sql")
    rt.psql('SET CLUSTER PARTED pg_cluster_1;',
            "在 mixed topology 隔离当前 MMR write/REP backend cluster",
            "返回 SET CLUSTER，并由各路由入口立即观察新状态",
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    rt.psql_business(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_port(), current_user;",
        "验证 cluster PARTED 后 MMR hint promoted 路由",
        "mix_hint 写事务切换到 promoted pg_cluster_2 primary",
        lambda output: expected_mmr2 in output and "postgres" in output,
        group="mmr_hint_mix", user="mix_hint")
    rt.psql_business(
        "BEGIN; CREATE TEMP TABLE ha_mixed_sql_failover(id int); "
        "SELECT inet_server_port(); ROLLBACK;",
        "验证 cluster PARTED 后 MMR sql_parse promoted 路由",
        "mix_sql 写事务切换到 promoted pg_cluster_2 primary",
        lambda output: expected_mmr2 in output and "CREATE TABLE" in output
        and "ROLLBACK" in output,
        group="mmr_sql_mix", user="mix_sql")
    rt.psql_business_error(
        "SELECT inet_server_port();",
        "验证 cluster PARTED 后 REP port 路由不可用",
        "rep_port_mix 唯一 backend cluster 已隔离，业务连接失败",
        lambda output: "ERROR" in output or "server" in output.lower(),
        group="rep_port_mix", port=rt.listen_port, user="mix_port")
    rt.psql('SET CLUSTER ACTIVE pg_cluster_1;',
            "恢复 mixed topology 的 pg_cluster_1",
            "返回 SET CLUSTER，MMR 与 REP 路由候选恢复",
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    _wait_pg_cluster_ready(
        rt, "pg_cluster_1", "pg_1", ("pg_3",),
        "等待 mixed topology 的 pg_cluster_1 路由投影恢复")
    rt.psql_business(
        "SELECT inet_server_port(), current_user;",
        "验证 cluster ACTIVE 后 REP port 路由恢复",
        "mix_port write_port 重新命中 replication primary",
        lambda output: expected_mmr1 in output and "postgres" in output,
        group="rep_port_mix", port=rt.listen_port, user="mix_port")
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WRITE pg_2 IN GROUPS (%s);' % group_names,
            "恢复混合 topology 中 3 个 MMR group 的写中心",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证混合 MMR 批量恢复备份")
    rt.diff(before, conf)
    rt.psql('SHOW GROUP_ROUTING mmr_sql_mix;', "查看混合配置恢复后的 sql_parse MMR 状态",
            "mmr_sql_mix 为 VALID 且 pg_cluster_2",
            lambda output: all(v in output for v in ("mmr_sql_mix", "active", "pg_cluster_2")))


def _run_batch_mixed_no_change_and_change(rt):
    conf = rt.start()
    initial = rt.workdir / "initial.conf"
    initial.write_bytes(conf.read_bytes())
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE PARTED pg_3;', "准备部分目标已满足的状态",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证准备状态的配置备份")
    mixed_before = rt.workdir / "before-mixed-command.conf"
    mixed_before.write_bytes(conf.read_bytes())
    pg_3_before = _datasource_block(mixed_before.read_text(encoding="utf-8"), "pg_3")
    rt.psql('SHOW DATASOURCES;', "查看混合批量命令前的运行态",
            "pg_3 为 parted，pg_4 为 active",
            lambda output: "pg_3" in output and "pg_4" in output and
                           "parted" in output and "active" in output)
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE PARTED pg_3,pg_4;',
            "批量处理已为 parted 的 pg_3 和待修改的 pg_4",
            "整条命令返回 SET NODE，只修改仍为 active 的 pg_4",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证混合 NO CHANGE/CHANGE 只生成一个备份")
    rt.diff_contains(mixed_before, conf,
                     ('-    status "active"', '+    status "parted"'),
                     "验证混合批量命令只落盘待修改目标")
    pg_3_after = _datasource_block(conf.read_text(encoding="utf-8"), "pg_3")
    rt.check("验证已满足目标未被重复修改",
             "pg_3 datasource block 逐字节保持不变",
             "pg_3 block unchanged=%s" % (pg_3_before == pg_3_after),
             pg_3_before == pg_3_after)
    rt.psql('SHOW DATASOURCES;', "查看混合批量命令后的运行态",
            "pg_3、pg_4 均为 parted",
            lambda output: output.count("parted") >= 2)
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE ACTIVE pg_3,pg_4;', "恢复混合批量状态",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证混合批量恢复备份")
    rt.diff(initial, conf)
    _wait_pg_cluster_ready(rt, "pg_cluster_1", "pg_1", ("pg_3",))
    _wait_pg_cluster_ready(rt, "pg_cluster_2", "pg_2", ("pg_4",))


def _run_bulk_30_group_write_roundtrip(rt):
    conf = rt.start(transform=_add_bulk_mmr_groups)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    names = ['bulk_mmr_%02d' % index for index in range(1, 31)]
    group_list = ','.join(names)
    for group in (names[0], names[-1]):
        rt.psql('SHOW GROUP_ROUTING %s;' % group,
                "查看 30 组切换前 %s 的运行态" % group,
                "%s pg_cluster_2 pg_cluster_1" % group,
                lambda output, group=group: all(v in output for v in
                                                (group, "pg_cluster_2",
                                                 "pg_cluster_1", "active")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WRITE pg_1 IN GROUPS (%s);' % group_list, "一次切换 30 个 MMR group 的写中心", "返回 SET NODE 且命令不报错", lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 30 组切换的配置备份")
    switched = _bulk_groups_have(conf.read_text(encoding="utf-8"), "pg_cluster_1", "pg_cluster_2")
    rt.check("验证 30 个 group 全部原子落盘", "30 个 group 均已切换", "30 groups switched=%s" % switched, switched)
    rt.diff_contains(before, conf, ('-    write_cluster "pg_cluster_2"', '+    write_cluster "pg_cluster_1"'), "验证 30 组写中心切换配置 diff")
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WRITE pg_2 IN GROUPS (%s);' % group_list, "一次恢复 30 个 MMR group 的写中心", "返回 SET NODE 且命令不报错", lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 30 组恢复的配置备份")
    rt.diff(before, conf)


def _run_bulk_30_groups_invalid_target(rt):
    conf = rt.start(transform=_add_bulk_mmr_groups)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    names = ['bulk_mmr_%02d' % i for i in range(1, 31)]
    group_list = ','.join(names + ['no_such_group'])
    rt.psql('SHOW GROUP_ROUTING bulk_mmr_01;', "查看批量非法 group 命令前状态", "pg_cluster_2",
            lambda output: "bulk_mmr_01" in output and "pg_cluster_2" in output)
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WRITE pg_1 IN GROUPS (%s);' % group_list,
                  "30 个合法 group 混入不存在 group 时原子拒绝",
                  "返回 no_such_group does not exist",
                  lambda output: "no_such_group" in output and "does not exist" in output)
    rt.assert_no_backup_created(backup, conf, "验证批量非法 group 未创建备份")
    rt.diff(before, conf)
    unchanged = _bulk_groups_have(conf.read_text(encoding="utf-8"), "pg_cluster_2", "pg_cluster_1")
    rt.check("验证 30 个合法 group 未部分修改", "全部保持初始 write/promoted", "unchanged=%s" % unchanged, unchanged)
    rt.psql('SHOW GROUP_ROUTING bulk_mmr_30;', "查看批量非法 group 命令后状态", "pg_cluster_2",
            lambda output: "bulk_mmr_30" in output and "pg_cluster_2" in output)


def _run_duplicate_and_mixed_status_targets(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    backup = rt.backup_checkpoint(conf)
    rt.psql(
        'SHOW DATASOURCES;',
        "查看重复状态命令前的运行态",
        'pg_3、pg_4 均为 active',
        lambda output: all(v in output for v in ("pg_3", "pg_4", "active")),
    )
    rt.psql(
        'SET NODE PARTED pg_3,pg_3,pg_4;',
        "执行重复与混合 datasource 的 PARTED 命令",
        '返回 SET NODE 且命令不报错',
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.assert_backup_created(backup, conf, "验证重复与混合 PARTED 的配置备份")
    rt.diff_contains(
        before, conf,
        ('+    status "parted"',),
        "验证重复与混合状态目标的配置 diff",
    )
    backup = rt.backup_checkpoint(conf)
    rt.psql(
        'SHOW DATASOURCES;',
        "查看重复与混合 PARTED 命令后的运行态",
        'pg_3、pg_4 均为 parted',
        lambda output: all(v in output for v in ("pg_3", "pg_4", "parted")),
    )
    rt.psql(
        'SET NODE ACTIVE pg_3,pg_3,pg_4;',
        "恢复重复与混合 datasource 的 ACTIVE 状态",
        '返回 SET NODE 且命令不报错',
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.assert_backup_created(backup, conf, "验证重复与混合 ACTIVE 的配置备份")
    rt.diff(before, conf)
    _wait_pg_cluster_ready(rt, "pg_cluster_1", "pg_1", ("pg_3",))
    _wait_pg_cluster_ready(rt, "pg_cluster_2", "pg_2", ("pg_4",))


