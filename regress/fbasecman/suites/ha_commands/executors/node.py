"""HA console command executors: NODE."""

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

__all__ = ['_run_missing_promoted_set_promoted', '_run_missing_promoted_set_write', '_run_set_node_invalid_datasource', '_run_set_node_parted_active_roundtrip', '_run_set_node_promoted_idempotent', '_run_set_node_promoted_in_groups_missing_field', '_run_set_node_role_rejects_parted_target', '_run_set_node_role_rejects_unrelated_group', '_run_set_node_weight_idempotent', '_run_set_node_weight_invalid_values', '_run_set_node_weight_switch_and_restore', '_run_set_node_weight_zero_roundtrip', '_run_set_node_write_all_related_groups', '_run_set_node_write_idempotent', '_run_set_node_write_in_groups_cardinality_errors', '_run_set_node_write_in_groups_invalid_group', '_run_set_node_write_in_groups_roundtrip', '_run_set_node_write_non_mmr_group', '_run_set_node_write_switch_and_restore', '_run_weight_sum_overflow_rejected']


def _run_set_node_invalid_datasource(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "查看非法 datasource 命令前的运行态",
            'write cluster 为 pg_cluster_2，pg_2 为 write-leader',
            lambda output: all(v in output for v in ("pg_cluster_2", "pg_2", "write-leader")))
    rt.psql_error(
        'SET NODE WRITE no_such_node IN GROUP mmr_group;',
        "验证不存在 datasource 的错误",
        '返回 ERROR，包含 no_such_node 和 does not exist',
        lambda output: "ERROR:" in output and "no_such_node" in output and "does not exist" in output,
    )
    rt.diff(before, conf)
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "验证非法 datasource 命令后的运行态",
            'write cluster 仍为 pg_cluster_2，pg_2 仍为 write-leader',
            lambda output: all(v in output for v in ("pg_cluster_2", "pg_2", "write-leader")))


def _run_set_node_promoted_in_groups_missing_field(rt):
    conf = rt.start(transform=_add_groups_without_promoted)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    groups = ("mmr_group", "mmr_group_extra")
    for group in groups:
        rt.psql('SHOW GROUP_ROUTING %s;' % group,
                "查看 %s 执行 PROMOTED IN GROUPS 前的运行态" % group,
                "%s pg_cluster_2 且 promoted 为空" % group,
                lambda output, group=group: group in output and
                "pg_cluster_2" in output and "active" in output)
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE PROMOTED pg_1 IN GROUPS (mmr_group,mmr_group_extra);',
            "执行 PROMOTED IN GROUPS 多组补写",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证 PROMOTED IN GROUPS 多组补写备份")
    changed = conf.read_text(encoding="utf-8")
    updated = all(
        'promoted_cluster "pg_cluster_1"' in
        _datasource_block(changed.replace('group "', 'datasources "'), group)
        for group in groups)
    rt.check("验证两个 group 的 promoted_cluster 全部落盘",
             "mmr_group 和 mmr_group_extra 均 pg_cluster_1",
             "both groups updated=%s" % updated, updated)
    rt.diff_contains(before, conf,
                     ('+    promoted_cluster "pg_cluster_1"',),
                     "验证 PROMOTED IN GROUPS 多组补写配置 diff")
    for group in groups:
        rt.psql('SHOW GROUP_ROUTING %s;' % group,
                "查看 %s PROMOTED 多组补写后的运行态" % group,
                "%s pg_cluster_2 pg_cluster_1" % group,
                lambda output, group=group: all(v in output for v in
                                                (group, "pg_cluster_2",
                                                 "pg_cluster_1")))
    rt.psql('SET CLUSTER PARTED pg_cluster_2;',
            "隔离两个 group 的原 write cluster",
            "返回 SET CLUSTER，触发 promoted cluster 接管",
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    for group in groups:
        rt.psql_business(
            "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
            "SELECT inet_server_port(), current_user;",
            "验证 %s 使用批量补写的 promoted cluster 接管真实写路由" % group,
            "%s 的写连接命中 pg_cluster_1 primary" % group,
            lambda output: str(rt.env.config["database"]["ports"]["mmr1"]) in output
            and "postgres" in output,
            group=group,
        )
    rt.psql('SET CLUSTER ACTIVE pg_cluster_2;',
            "恢复两个 group 的原 write cluster",
            "返回 SET CLUSTER，pg_cluster_2 全部 datasource 恢复 active",
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    _wait_pg_cluster_ready(
        rt, "pg_cluster_2", "pg_2", ("pg_4",),
        "等待原 write cluster 的 monitor 投影恢复")


def _run_set_node_write_all_related_groups(rt):
    conf = rt.start(transform=_add_second_mmr_group)
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    for group in ("mmr_group", "mmr_group_extra"):
        rt.psql('SHOW GROUP_ROUTING %s;' % group,
                "查看 %s 切换前的写中心" % group,
                '%s 的 write cluster 为 pg_cluster_2' % group,
                lambda output, group=group: group in output and
                "pg_cluster_2" in output)
    rt.psql('SET NODE WRITE pg_1;', "不指定 group 切换全部关联 MMR group",
            '返回 SET NODE，命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    text = conf.read_text(encoding="utf-8")
    rt.check("验证两个 MMR group 完整落盘",
             "两个 group 的 write_cluster 均为 pg_cluster_1",
             "write_cluster pg_cluster_1 occurrences=%d" %
             text.count('    write_cluster "pg_cluster_1"\n'),
             text.count('    write_cluster "pg_cluster_1"\n') == 2)
    rt.diff_contains(before, conf,
                     ('-    write_cluster "pg_cluster_2"',
                      '+    write_cluster "pg_cluster_1"'),
                     "验证默认全组范围的配置 diff")
    for group in ("mmr_group", "mmr_group_extra"):
        rt.psql('SHOW GROUP_ROUTING %s;' % group,
                "验证 %s 切换后的运行态" % group,
                '%s 的 write cluster 为 pg_cluster_1，pg_1 为 write-leader' % group,
                lambda output, group=group: all(v in output for v in
                                                (group, "pg_cluster_1",
                                                 "pg_1", "write-leader")))
    rt.psql('SET NODE WRITE pg_2;', "恢复全部关联 MMR group 的写中心",
            '返回 SET NODE，命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.diff(before, conf)
    for group in ("mmr_group", "mmr_group_extra"):
        rt.psql('SHOW GROUP_ROUTING %s;' % group,
                "验证 %s 恢复后的运行态" % group,
                '%s 的 write cluster 恢复为 pg_cluster_2，pg_2 为 write-leader' % group,
                lambda output, group=group: all(v in output for v in
                                                (group, "pg_cluster_2", "pg_2", "write-leader")))


def _run_set_node_write_in_groups_invalid_group(rt):
    conf = rt.start(transform=_add_second_mmr_group)
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;',
        "查看非法 IN GROUPS 命令前的运行态",
        'mmr_group 的 write cluster 为 pg_cluster_2，pg_2 为 write-leader',
        lambda output: all(v in output for v in ("mmr_group", "pg_cluster_2", "pg_2", "write-leader")),
    )
    backup = rt.backup_checkpoint(conf)
    rt.psql_error(
        'SET NODE WRITE pg_1 IN GROUPS (mmr_group,no_such_group);',
        "执行包含不存在 group 的 IN GROUPS 命令",
        '返回 ERROR，包含 no_such_group 和 does not exist',
        lambda output: "ERROR:" in output and "no_such_group" in output and "does not exist" in output,
    )
    rt.assert_no_backup_created(backup, conf, "验证非法 group 未创建备份")
    rt.diff(before, conf)
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;',
        "验证非法 IN GROUPS 命令后的运行态",
        'mmr_group 的 write cluster 仍为 pg_cluster_2，pg_2 仍为 write-leader',
        lambda output: all(v in output for v in ("mmr_group", "pg_cluster_2", "pg_2", "write-leader")),
    )


def _run_set_node_write_non_mmr_group(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql_error(
        'SET NODE WRITE pg_1 IN GROUP rep_group;',
        "验证非 MMR group 的 WRITE 拒绝",
        '返回 ERROR，包含 rep_group 和 is not an MMR group',
        lambda output: "ERROR:" in output and "rep_group" in output and "is not an MMR group" in output,
    )
    rt.diff(before, conf)


def _run_set_node_write_in_groups_cardinality_errors(rt):
    conf = rt.start(transform=_add_second_mmr_group)
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;',
        "查看 IN GROUPS 参数校验前的运行态",
        'write cluster 为 pg_cluster_2，pg_2 为 write-leader',
        lambda output: all(v in output for v in ("pg_cluster_2", "pg_2", "write-leader")),
    )
    for sql, label in (
        ('SET NODE WRITE pg_1 IN GROUPS ();', "执行 IN GROUPS 空列表命令"),
        ('SET NODE WRITE pg_1 IN GROUPS (mmr_group);', "执行 IN GROUPS 单 group 命令"),
    ):
        backup = rt.backup_checkpoint(conf)
        rt.psql_error(
            sql, label,
            '返回 ERROR 且命令被拒绝',
            lambda output: "ERROR:" in output,
        )
        rt.assert_no_backup_created(backup, conf, "验证参数错误未创建备份")
        rt.diff(before, conf)
        rt.psql(
            'SHOW GROUP_ROUTING mmr_group;',
            "验证参数错误后的运行态",
            'write cluster 仍为 pg_cluster_2，pg_2 仍为 write-leader',
            lambda output: all(v in output for v in ("pg_cluster_2", "pg_2", "write-leader")),
        )


def _run_set_node_weight_invalid_values(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW NODES;', "查看非法 WEIGHT 命令前的节点权重",
            'pg_3 的 weight 为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))
    for sql, title in (
        ('SET NODE WEIGHT pg_3=-1;', "执行负数 WEIGHT 命令"),
        ('SET NODE WEIGHT pg_3=2147483648;', "执行超过 INT_MAX 的 WEIGHT 命令"),
        ('SET NODE WEIGHT pg_3;', "执行缺失赋值的 WEIGHT 命令"),
    ):
        backup = rt.backup_checkpoint(conf)
        rt.psql_error(sql, title, '返回 ERROR 且命令被拒绝',
                      lambda output: "ERROR:" in output)
        rt.assert_no_backup_created(backup, conf, "验证非法 WEIGHT 未创建备份")
        rt.diff(before, conf)
        rt.psql('SHOW NODES;', "验证非法 WEIGHT 后的节点权重",
                'pg_3 的 weight 仍为 10',
                lambda output: _node_has_weight(output, "pg_3", 10))


def _run_set_node_promoted_idempotent(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "查看 SET NODE PROMOTED 执行前的运行态",
            'promoted cluster 为 pg_cluster_1',
            lambda output: "pg_cluster_1" in output)
    rt.psql(
        'SET NODE PROMOTED pg_1 IN GROUP mmr_group;',
        "执行 SET NODE PROMOTED 幂等命令",
        '返回 SET NODE；NO CHANGE 详情写入 fbasecman.log',
            lambda output: ("SET NODE" in output or "NO CONFIG CHANGE" in output)
            and "ERROR" not in output,
    )
    rt.diff(before, conf)
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;',
        "验证 SET NODE PROMOTED 后的运行态",
        'mmr_group 为 VALID，promoted cluster 为 pg_cluster_1',
        lambda output: all(value in output for value in (
            "mmr_group", "active", "pg_cluster_1",
        )),
    )


def _run_set_node_parted_active_roundtrip(rt):
    # 保留 postgres 用户对 mmr_group 的路由范围，同时让 single_group
    # 继续使用 read_only，覆盖 PARTED 后回退 primary、ACTIVE 后恢复 replica。
    conf = rt.start(transform=_single_read_only_keep_scope)
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW DATASOURCES;', "查看修改前的 datasource 状态",
            'pg_3 的 manual_state 和 effective_state 均为 active',
            lambda output: "pg_3" in output and "active" in output)
    rt.psql(
        'SET NODE PARTED pg_3;', "将 pg_3 设置为 PARTED",
        '返回 SET NODE', lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.diff_contains(before, conf, ('-    status "active"', '+    status "parted"'),
                     "验证 PARTED 的配置 diff")
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;', "验证 PARTED 后的运行态",
        'mmr_group 保持 VALID，pg_cluster_1 的 current primary 仍为 pg_1',
        lambda output: all(value in output for value in (
            "mmr_group", "pg_cluster_1", "pg_1",
        )),
    )
    rt.psql_business(
        'SELECT inet_server_port();',
        "验证唯一只读 replica PARTED 后回退到 primary",
        "single_group 没有 active replica 时回退到 primary pg_1",
        lambda output: str(rt.env.config["database"]["ports"]["mmr1"]) in output,
        group="single_group",
    )
    rt.psql(
        'SET NODE ACTIVE pg_3;', "将 pg_3 恢复为 ACTIVE",
        '返回 SET NODE', lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.diff(before, conf)
    _wait_pg_cluster_ready(
        rt, "pg_cluster_1", "pg_1", ("pg_3",),
        "等待 pg_3 ACTIVE 后恢复可信 replica 投影")
    rt.psql_business(
        'SELECT inet_server_port(), pg_is_in_recovery();',
        "验证唯一只读 replica ACTIVE 后业务路由恢复",
        "single_group 命中 pg_3 且后端处于 recovery",
        lambda output: str(rt.env.config["database"]["ports"]["mmr1_standby1"]) in output
        and " t" in output,
        group="single_group",
    )


def _run_weight_sum_overflow_rejected(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.psql('SHOW NODES;', "查看权重总和溢出命令前的运行态",
            "pg_1 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_1', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WEIGHT pg_1=2147483647;',
                  "拒绝导致 group 权重总和溢出的合法单值",
                  "返回 candidate weight sum exceeds INT_MAX",
                  lambda output: "candidate weight sum exceeds INT_MAX" in output)
    rt.assert_no_backup_created(backup, conf,
                                "验证权重总和溢出未创建备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看权重总和溢出拒绝后的运行态",
            "pg_1 weight 仍为 10",
            lambda output: _node_has_weight(output, 'pg_1', 10))


def _run_set_node_weight_zero_roundtrip(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW NODES;', "查看零权重命令前的节点权重",
            'pg_3 的 weight 为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=0;', "将 pg_3 weight 修改为 0",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 weight 0 的配置备份")
    rt.diff_contains(before, conf, ('-    weight 10', '+    weight 0'),
                     "验证 weight 0 的配置 diff")
    rt.psql('SHOW NODES;', "查看 weight 0 修改后的运行态",
            'pg_3 的 weight 为 0',
            lambda output: _node_has_weight(output, "pg_3", 0))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复 pg_3 weight 为 10",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证恢复 weight 的配置备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "验证 weight 恢复后的运行态",
            'pg_3 的 weight 恢复为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))


def _run_set_node_weight_idempotent(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql(
        'SHOW NODES;',
        "查看 SET NODE WEIGHT 执行前的控制台状态",
        'SHOW NODES 中 pg_3 的当前 weight 为 10',
        lambda output: any(
            "pg_3" in line and "10" in line
            for line in output.splitlines()
        ),
    )
    rt.psql(
        'SET NODE WEIGHT pg_3=10;',
        "执行 SET NODE WEIGHT 幂等命令",
        '返回 SET NODE 或 NO CONFIG CHANGE',
        lambda output: ("SET NODE" in output or "NO CONFIG CHANGE" in output)
        and "ERROR" not in output,
    )
    rt.diff(before, conf)
    rt.psql(
        'SHOW NODES;',
        "验证幂等权重命令后的运行态",
        'SHOW NODES 中 pg_3 的 weight 仍为 10',
        lambda output: any(
            "pg_3" in line and "10" in line
            for line in output.splitlines()
        ),
    )


def _run_missing_promoted_set_promoted(rt):
    conf = rt.start(transform=_without_promoted)
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "查看命令前的缺省 promoted 状态",
            'write cluster 为 pg_cluster_2，promoted 为空',
            lambda output: "pg_cluster_2" in output and "active" in output)
    rt.psql('SET NODE PROMOTED pg_1 IN GROUP mmr_group;',
            "补写 promoted_cluster 为 pg_cluster_1",
            '返回 SET NODE', lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.diff_contains(before, conf, ('+    promoted_cluster "pg_cluster_1"',),
                     "验证 promoted_cluster 新增的配置 diff")
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "查看补写后的运行态",
            'write cluster 仍为 pg_cluster_2，promoted 为 pg_cluster_1',
            lambda output: all(v in output for v in
                               ("pg_cluster_2", "pg_cluster_1", "active")))
    rt.psql_business(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "验证补写 promoted 后写路由未改变",
        '写 Hint 连接仍落到 pg_2 对应端口',
        lambda output: str(rt.env.config["database"]["ports"]["mmr2"]) in output)


def _run_set_node_write_idempotent(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "查看 SET NODE WRITE 执行前的运行态",
            'write cluster 为 pg_cluster_2，pg_2 为 write-leader',
            lambda output: all(v in output for v in ("pg_cluster_2", "pg_2", "write-leader")))
    rt.psql(
        'SET NODE WRITE pg_2 IN GROUP mmr_group;',
        "执行 SET NODE WRITE 幂等命令",
        '返回 SET NODE；NO CHANGE 详情写入 fbasecman.log',
            lambda output: ("SET NODE" in output or "NO CONFIG CHANGE" in output)
            and "ERROR" not in output,
    )
    rt.diff(before, conf)
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;',
        "验证 SET NODE WRITE 后的运行态",
        'mmr_group 为 VALID，write cluster 为 pg_cluster_2，pg_2 为 write-leader',
        lambda output: all(value in output for value in (
            "mmr_group", "active", "pg_cluster_2", "pg_2", "write-leader",
        )),
    )


def _run_set_node_write_in_groups_roundtrip(rt):
    conf = rt.start(transform=_add_second_mmr_group)
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    groups = ("mmr_group", "mmr_group_extra")
    for group in groups:
        rt.psql(
            'SHOW GROUP_ROUTING %s;' % group,
            "查看 %s 执行 IN GROUPS 前的运行态" % group,
            '%s 的 write cluster 为 pg_cluster_2，pg_2 为 write-leader' % group,
            lambda output, group=group: all(v in output for v in
                                            (group, "pg_cluster_2", "pg_2", "write-leader")),
        )
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WRITE pg_1 IN GROUPS (mmr_group,mmr_group_extra);',
            "执行 IN GROUPS 多组写中心切换",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 IN GROUPS 切换的配置备份")
    rt.diff_contains(before, conf,
                     ('-    write_cluster "pg_cluster_2"',
                      '+    write_cluster "pg_cluster_1"'),
                     "验证 IN GROUPS 多组配置 diff")
    for group in groups:
        rt.psql('SHOW GROUP_ROUTING %s;' % group,
                "验证 %s 切换后的运行态" % group,
                '%s 的 write cluster 为 pg_cluster_1，pg_1 为 write-leader' % group,
                lambda output, group=group: all(v in output for v in
                                                (group, "pg_cluster_1",
                                                 "pg_1", "write-leader")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WRITE pg_2 IN GROUPS (mmr_group,mmr_group_extra);',
            "恢复 IN GROUPS 多组写中心",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 IN GROUPS 恢复的配置备份")
    rt.diff(before, conf)
    for group in groups:
        rt.psql('SHOW GROUP_ROUTING %s;' % group,
                "验证 %s 恢复后的运行态" % group,
                '%s 的 write cluster 恢复为 pg_cluster_2，pg_2 为 write-leader' % group,
                lambda output, group=group: all(v in output for v in
                                                (group, "pg_cluster_2",
                                                 "pg_2", "write-leader")))


def _run_set_node_role_rejects_unrelated_group(rt):
    conf = rt.start(transform=_add_single_cluster_mmr_group)
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW GROUP_ROUTING mmr_group_one_cluster;',
            "查看未关联目标 group 的初始运行态",
            'group 仅包含 pg_cluster_1，write cluster 为 pg_cluster_1',
            lambda output: all(v in output for v in
                               ("mmr_group_one_cluster", "pg_cluster_1", "pg_cluster_1")))
    for sql, title in (
        ('SET NODE WRITE pg_2 IN GROUP mmr_group_one_cluster;',
         "验证 WRITE 拒绝未关联 cluster"),
        ('SET NODE PROMOTED pg_2 IN GROUP mmr_group_one_cluster;',
         "验证 PROMOTED 拒绝未关联 cluster"),
    ):
        backup = rt.backup_checkpoint(conf)
        rt.psql_error(sql, title,
                      '返回 ERROR，包含 mmr_group_one_cluster 和 does not use cluster',
                      lambda output: all(v in output for v in
                                         ("ERROR:", "mmr_group_one_cluster", "does not use cluster")))
        rt.assert_no_backup_created(backup, conf, "验证未关联 group 命令未创建备份")
        rt.diff(before, conf)
        rt.psql('SHOW GROUP_ROUTING mmr_group_one_cluster;',
                "验证未关联 group 命令后的运行态",
                'group 仍仅包含 pg_cluster_1，write cluster 仍为 pg_cluster_1',
                lambda output: all(v in output for v in
                                   ("mmr_group_one_cluster", "pg_cluster_1", "pg_cluster_1")))


def _run_set_node_write_switch_and_restore(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;', "查看切换前的写节点状态",
        '切换前 write cluster 为 pg_cluster_2，pg_2 为 write-leader',
        lambda output: all(value in output for value in (
            "pg_cluster_2", "pg_2", "write-leader", "active",
        )),
    )
    rt.psql(
        'SET NODE WRITE pg_1 IN GROUP mmr_group;',
        "切换 mmr_group 写中心到 pg_1",
        '返回 SET NODE', lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.diff_contains(before, conf, ('-    write_cluster "pg_cluster_2"', '+    write_cluster "pg_cluster_1"'),
                     "验证切换写中心的配置 diff")
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;', "验证切换后的运行态",
        'pg_cluster_1 为 write cluster，pg_1 为 write-leader',
        lambda output: all(value in output for value in (
            "pg_cluster_1", "pg_1", "write-leader", "active",
        )),
    )
    rt.psql_business(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "验证切换后的实际写路由",
        '写 Hint 业务连接应落到 pg_1 对应端口，并返回 postgres 用户',
        lambda output: str(rt.env.config["database"]["ports"]["mmr1"]) in output
        and "postgres" in output,
    )
    rt.psql(
        'SET NODE WRITE pg_2 IN GROUP mmr_group;', "切回 mmr_group 写中心到 pg_2",
        '返回 SET NODE', lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;', "查看切回后的写节点状态",
        '切回后 write cluster 为 pg_cluster_2，pg_2 为 write-leader',
        lambda output: all(value in output for value in (
            "pg_cluster_2", "pg_2", "write-leader", "active",
        )),
    )
    rt.diff(before, conf)
    rt.psql(
        'SHOW GROUP_ROUTING mmr_group;', "验证切回后的运行态",
        'pg_cluster_2 为 write cluster，pg_2 为 write-leader',
        lambda output: all(value in output for value in (
            "pg_cluster_2", "pg_2", "write-leader", "active",
        )),
    )
    rt.psql_business(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "验证切回后的实际写路由",
        '写 Hint 业务连接应落到 pg_2 对应端口，并返回 postgres 用户',
        lambda output: str(rt.env.config["database"]["ports"]["mmr2"]) in output
        and "postgres" in output,
    )


def _run_set_node_role_rejects_parted_target(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW DATASOURCES;', "查看目标隔离前的运行态",
            'pg_1 为 active',
            lambda output: "pg_1" in output and "active" in output)
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE PARTED pg_1;', "将角色切换目标 pg_1 设置为 PARTED",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 PARTED 准备命令的配置备份")
    rt.psql('SHOW DATASOURCES;', "查看目标隔离后的运行态",
            'pg_1 为 parted',
            lambda output: "pg_1" in output and "parted" in output)
    for sql, title in (
        ('SET NODE WRITE pg_1 IN GROUP mmr_group;', "验证 WRITE 拒绝 PARTED 目标"),
        ('SET NODE PROMOTED pg_1 IN GROUP mmr_group;', "验证 PROMOTED 拒绝 PARTED 目标"),
    ):
        backup = rt.backup_checkpoint(conf)
        rt.psql_error(sql, title, '返回 ERROR，包含 pg_1 和 not active',
                      lambda output: all(v in output for v in ("ERROR:", "pg_1", "not active")))
        rt.assert_no_backup_created(backup, conf, "验证角色拒绝命令未创建备份")
        rt.psql('SHOW GROUP_ROUTING mmr_group;', "验证角色拒绝后的 group 运行态",
                'write cluster 仍为 pg_cluster_2 且写节点状态未改变',
                lambda output: all(v in output for v in
                                   ("pg_cluster_2", "write-leader")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE ACTIVE pg_1;', "恢复角色切换目标 pg_1 为 ACTIVE",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 ACTIVE 恢复命令的配置备份")
    rt.diff(before, conf)
    _wait_pg_cluster_ready(
        rt, "pg_cluster_1", "pg_1", ("pg_3",),
        "验证目标 ACTIVE 后 monitor 投影恢复")


def _run_set_node_weight_switch_and_restore(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW NODES;', "查看修改前的节点权重",
            'pg_3 的初始 weight 为 10',
            lambda output: "pg_3" in output and "10" in output)
    rt.psql(
        'SET NODE WEIGHT pg_3=11;', "将 pg_3 weight 修改为 11",
        '返回 SET NODE', lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.diff_contains(before, conf, ('-    weight 10', '+    weight 11'),
                     "验证 weight 修改的配置 diff")
    rt.psql(
        'SHOW NODES;', "验证 weight 修改后的运行态",
        'SHOW NODES 中 pg_3 的 weight 为 11',
        lambda output: "pg_3" in output and "11" in output,
    )
    rt.psql(
        'SET NODE WEIGHT pg_3=10;', "将 pg_3 weight 恢复为 10",
        '返回 SET NODE', lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看恢复后的节点权重",
            'pg_3 的 weight 已恢复为 10',
            lambda output: "pg_3" in output and "10" in output)


def _run_missing_promoted_set_write(rt):
    conf = rt.start(transform=_without_promoted)
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "查看命令前的缺省 promoted 状态",
            'write cluster 为 pg_cluster_2，promoted 为空',
            lambda output: "pg_cluster_2" in output and "active" in output)
    rt.psql('SET NODE WRITE pg_1 IN GROUP mmr_group;',
            "缺失 promoted_cluster 时切换写中心到 pg_1",
            '返回 SET NODE', lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.diff_contains(before, conf,
                     ('-    write_cluster "pg_cluster_2"',
                      '+    write_cluster "pg_cluster_1"'),
                     "验证只修改 write_cluster 的配置 diff")
    rt.check("验证未自动新增 promoted_cluster",
             "配置文件中不存在 promoted_cluster 字段",
             "promoted_cluster present=%s" % ('promoted_cluster' in conf.read_text(encoding='utf-8')),
             'promoted_cluster' not in conf.read_text(encoding='utf-8'))
    rt.psql('SHOW GROUP_ROUTING mmr_group;', "查看命令后的运行态",
            'write cluster 为 pg_cluster_1，promoted 仍为空，pg_1 为 write-leader',
            lambda output: all(v in output for v in
                               ("pg_cluster_1", "pg_1", "write-leader", "active")))
    rt.psql_business(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid(), current_user;",
        "验证缺省 promoted 场景的实际写路由",
        '写 Hint 连接落到 pg_1 对应端口',
        lambda output: str(rt.env.config["database"]["ports"]["mmr1"]) in output)


