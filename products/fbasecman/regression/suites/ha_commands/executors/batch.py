"""HA console command executors: BATCH."""


from suites.ha_commands.helpers import *
from platform_regress.clients.psql import parse_psql_table
__all__ = ['_run_batch_mixed_no_change_and_change', '_run_batch_status_invalid_target_atomicity', '_run_batch_weight_atomicity', '_run_bulk_30_datasource_weight_roundtrip', '_run_bulk_30_group_write_roundtrip', '_run_bulk_30_groups_invalid_target', '_run_bulk_30_groups_non_mmr', '_run_comprehensive_all_groups_and_commands', '_run_default_group_expansion_34', '_run_duplicate_and_mixed_status_targets', '_run_mixed_topology_pool_mode_batch_write', '_run_name_endpoint_status_deduplication', '_run_set_node_write_in_groups_duplicate_group']


def _run_bulk_30_groups_non_mmr(context):
    ops = context.ops
    conf = ops.start(transform=_add_bulk_mmr_groups)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    names = ['bulk_mmr_%02d' % i for i in range(1, 31)]
    group_list = ','.join(names + ['rep_group'])
    ops.assert_routing(
        "bulk_mmr_01", "查看混入非 MMR group 命令前状态",
        write_cluster="pg_cluster_2", write_leader="pg_2")
    ops.assert_routing(
        "rep_group", "查看非 MMR group 命令前状态",
        write_cluster="pg_cluster_1", write_leader="pg_1")
    backup = ops.backup_checkpoint(conf)
    ops.psql_error('SET NODE WRITE pg_1 IN GROUPS (%s);' % group_list,
                  "30 个 MMR group 混入非 MMR group 时原子拒绝",
                  "返回 rep_group 和 is not an MMR group",
                  lambda output: "rep_group" in output and "is not an MMR group" in output)
    ops.assert_no_backup_created(backup, conf, "验证混入非 MMR group 未创建备份")
    ops.diff(before, conf)
    missing = _bulk_groups_missing(
        conf.read_text(encoding="utf-8"), "pg_cluster_2", "pg_cluster_1")
    ops.check("验证混入非 MMR 后 30 个 group 未部分修改",
             "全部保持 write_cluster=pg_cluster_2 promoted_cluster=pg_cluster_1",
             "符合=30/%d，不符项=%s" % (30 - len(missing), missing or "无"),
             not missing)
    ops.assert_routing(
        "bulk_mmr_30", "查看混入非 MMR 命令后 MMR 状态",
        write_cluster="pg_cluster_2", write_leader="pg_2")
    ops.assert_routing(
        "rep_group", "查看混入非 MMR 命令后 REP 状态",
        write_cluster="pg_cluster_1", write_leader="pg_1")


def _run_default_group_expansion_34(context):
    ops = context.ops
    conf = ops.start(transform=_add_34_mmr_groups)
    _wait_pg_cluster_ready(context, "pg_cluster_1", "pg_1", ("pg_3",))
    _wait_pg_cluster_ready(context, "pg_cluster_2", "pg_2", ("pg_4",))
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    names = ['expand_mmr_%02d' % i for i in range(1, 35)]
    ops.assert_routing(
        "expand_mmr_01", "查看 34 组默认展开前状态",
        write_cluster="pg_cluster_2", write_leader="pg_2")
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WRITE pg_1;', "省略 IN GROUPS 自动展开 34 个 MMR group",
            "返回 SET NODE 且命令不报错", lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 34 组默认展开备份")
    text = conf.read_text(encoding="utf-8")
    not_changed = [
        name for name in names
        if 'write_cluster "pg_cluster_1"' not in
        _datasource_block(text.replace('group "', 'datasources "'), name)]
    ops.check("验证默认范围完整覆盖 34 个 group", "34 个 group 全部切换到 pg_cluster_1",
             "已切换=%d/34，未切换项=%s" % (34 - len(not_changed), not_changed or "无"),
             not not_changed)
    ops.diff_contains(before, conf, ('-    write_cluster "pg_cluster_2"', '+    write_cluster "pg_cluster_1"'), "验证 34 组默认展开配置 diff")
    ops.assert_routing(
        "expand_mmr_34", "查看 34 组默认展开后状态",
        write_cluster="pg_cluster_1", write_leader="pg_1")
    idempotent = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WRITE pg_1;', "重复执行 34 组默认展开命令并记录 NO CHANGE 输出",
            "返回 NO CONFIG CHANGE；NO CHANGE 详情写入 fbasecman.log",
            lambda output: "NO CONFIG CHANGE" in output and "ERROR" not in output)
    ops.assert_no_backup_created(idempotent, conf,
                                "验证 34 组 NO CHANGE 未创建备份")
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WRITE pg_2;', "恢复默认展开的 34 个 MMR group", "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 34 组默认展开恢复备份")
    ops.diff(before, conf)


def _run_bulk_30_datasource_weight_roundtrip(context):
    ops = context.ops
    conf = ops.start(transform=_add_bulk_datasources)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    names = ['bulk_ds_%02d' % index for index in range(1, 31)]
    assignments_11 = ','.join('%s=11' % name for name in names)
    assignments_10 = ','.join('%s=10' % name for name in names)
    ops.assert_table(
        'SHOW DATASOURCES;',
        "查看 30 节点权重修改前的控制台状态",
        {"bulk_ds_01": {"config_status": "active", "weight": "10"},
         "bulk_ds_30": {"config_status": "active", "weight": "10"}},
        key="node_name")
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT %s;' % assignments_11,
            "一次修改 30 个 datasource 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 30 节点权重修改备份")
    changed = conf.read_text(encoding="utf-8")
    wrong = _bulk_datasources_wrong_weight(changed, 11)
    ops.check("验证 30 个 datasource 全部原子落盘",
             "bulk_ds_01 至 bulk_ds_30 均为 weight 11",
             "符合=%d/30，不符项=%s" % (30 - len(wrong), wrong or "无"),
             not wrong)
    ops.diff_contains(before, conf, ('-    weight 10', '+    weight 11'),
                     "验证 30 节点权重配置 diff")
    ops.assert_table(
        'SHOW DATASOURCES;',
        "查看 30 节点权重修改后的控制台状态",
        {"bulk_ds_01": {"config_status": "active", "weight": "11"},
         "bulk_ds_30": {"config_status": "active", "weight": "11"}},
        key="node_name")
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT %s;' % assignments_10,
            "一次恢复 30 个 datasource 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 30 节点权重恢复备份")
    wrong = _bulk_datasources_wrong_weight(conf.read_text(encoding="utf-8"), 10)
    ops.check("验证 30 个 datasource 全部恢复",
             "bulk_ds_01 至 bulk_ds_30 均恢复 weight 10",
             "符合=%d/30，不符项=%s" % (30 - len(wrong), wrong or "无"),
             not wrong)
    ops.diff(before, conf)
    ops.assert_table(
        'SHOW DATASOURCES;',
        "查看 30 节点权重恢复后的控制台状态",
        {"bulk_ds_01": {"config_status": "active", "weight": "10"},
         "bulk_ds_30": {"config_status": "active", "weight": "10"}},
        key="node_name")


def _run_batch_weight_atomicity(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    ops.assert_nodes("查看批量修改前的节点权重",
                     {"pg_3": {"weight": "10"}, "pg_4": {"weight": "10"}})
    ops.psql('SET NODE WEIGHT pg_3=11,pg_4=11;', "批量修改 pg_3、pg_4 权重",
            '返回 SET NODE', lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.diff_contains(before, conf, ('+    weight 11',), "验证两个 datasource 的批量配置 diff")
    text = conf.read_text(encoding='utf-8')
    weight_lines = _matching_lines(text, 'weight 11')
    ops.check("验证批量修改完整落盘", "pg_3、pg_4 均为 weight 11",
             "weight 11 行: %s" % weight_lines,
             text.count('    weight 11\n') == 2)
    ops.assert_nodes("查看批量修改后的运行态",
                     {"pg_3": {"weight": "11"}, "pg_4": {"weight": "11"}})
    ops.psql('SET NODE WEIGHT pg_3=10,pg_4=10;', "批量恢复 pg_3、pg_4 权重",
            '返回 SET NODE', lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.diff(before, conf)
    ops.psql_error('SET NODE WEIGHT pg_3=11,no_such_node=11;',
                  "验证批量目标含无效节点时整体拒绝",
                  '返回 no_such_node does not exist 错误',
                  lambda output: all(v in output for v in
                                     ("ERROR:", "no_such_node", "does not exist")))
    ops.diff(before, conf)
    ops.assert_nodes("查看原子拒绝后的节点权重",
                     {"pg_3": {"weight": "10"}, "pg_4": {"weight": "10"}})


def _run_name_endpoint_status_deduplication(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    host = ops.env.config["database"]["mmr_host"]
    port = ops.env.config["database"]["ports"]["mmr1_standby1"]
    target_list = "pg_3,%s:%s" % (host, port)
    ops.assert_table(
        'SHOW DATASOURCES;', "查看名称/endpoint 去重命令前的运行态",
        {"pg_3": {"config_status": "active"}},
        key="node_name")
    backup = ops.backup_checkpoint(conf)
    ops.psql(
        'SET NODE PARTED %s;' % target_list,
        "使用名称和 endpoint 隔离同一 datasource",
        '返回 SET NODE 且命令不报错',
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    ops.assert_backup_created(backup, conf, "验证名称/endpoint PARTED 的配置备份")
    ops.diff_contains(before, conf,
                     ('-    status "active"', '+    status "parted"'),
                     "验证名称/endpoint 去重后的配置 diff")
    ops.assert_table(
        'SHOW DATASOURCES;', "查看名称/endpoint PARTED 后的运行态",
        {"pg_3": {"config_status": "parted"}},
        key="node_name")
    backup = ops.backup_checkpoint(conf)
    ops.psql(
        'SET NODE ACTIVE %s;' % target_list,
        "使用名称和 endpoint 恢复同一 datasource",
        '返回 SET NODE 且命令不报错',
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    ops.assert_backup_created(backup, conf, "验证名称/endpoint ACTIVE 的配置备份")
    ops.diff(before, conf)
    _wait_pg_cluster_ready(
        context, "pg_cluster_1", "pg_1", ("pg_3",),
        "验证名称/endpoint ACTIVE 后 monitor 投影恢复")


def _run_set_node_write_in_groups_duplicate_group(context):
    ops = context.ops
    conf = ops.start(transform=_add_second_mmr_group)
    before = ops.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    ops.assert_routing(
        "mmr_group", "查看重复 group 命令前的运行态",
        write_cluster="pg_cluster_2", write_leader="pg_2")
    backup = ops.backup_checkpoint(conf)
    ops.psql_error(
        'SET NODE WRITE pg_1 IN GROUPS (mmr_group,mmr_group);',
        "执行重复 group 的 IN GROUPS 命令",
        '返回 ERROR 且命令被拒绝',
        lambda output: "ERROR:" in output,
    )
    ops.assert_no_backup_created(backup, conf, "验证重复 group 未创建备份")
    ops.diff(before, conf)
    ops.assert_routing(
        "mmr_group", "验证重复 group 命令后的运行态",
        write_cluster="pg_cluster_2", write_leader="pg_2")


def _run_batch_status_invalid_target_atomicity(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    ops.assert_table(
        'SHOW DATASOURCES;', "查看批量状态错误命令前的运行态",
        {"pg_3": {"config_status": "active"},
         "pg_4": {"config_status": "active"}},
        key="node_name")
    backup = ops.backup_checkpoint(conf)
    ops.psql_error(
        'SET NODE PARTED pg_3,no_such_node,pg_4;',
        "执行混入不存在 datasource 的批量 PARTED 命令",
        '返回 ERROR，包含 no_such_node 和 does not exist',
        lambda output: all(v in output for v in ("ERROR:", "no_such_node", "does not exist")),
    )
    ops.assert_no_backup_created(backup, conf, "验证批量状态错误未创建备份")
    ops.diff(before, conf)
    ops.assert_table(
        'SHOW DATASOURCES;', "验证批量状态错误命令后的运行态",
        {"pg_3": {"config_status": "active"},
         "pg_4": {"config_status": "active"}},
        key="node_name")


def _run_comprehensive_all_groups_and_commands(context):
    """Exercise the supported HA command matrix with one complex fixture."""
    ops = context.ops
    conf = ops.start(transform=_comprehensive_transform(context))
    before = ops.workdir / "comprehensive.initial.conf"
    before.write_bytes(conf.read_bytes())
    rendered_bytes = conf.read_bytes()
    rendered = rendered_bytes.decode("utf-8")
    db = ops.env.config["database"]
    ports = db["ports"]
    host = db["mmr_host"]
    # transform 在 metadata 中登记了 14 个 datasource 的真实
    # name/host/port/cluster/system_identifier——期望值从它推导。
    meta = {item["name"]: item for item in ops.datasource_metadata}
    pg_cluster_1 = [n for n, m in meta.items() if m["cluster"] == "pg_cluster_1"]
    pg_cluster_2 = [n for n, m in meta.items() if m["cluster"] == "pg_cluster_2"]

    def node_row(name):
        item = meta[name]
        primary = name in ("pg_1", "pg_2")
        return {
            "cluster_name": item["cluster"],
            "host": item["host"], "port": str(item["port"]),
            "storage_db": "postgres", "weight": "10",
            "config_status": "active",
            "effective_role": "PRIMARY" if primary else "REPLICA",
            "current_primary": "pg_1" if item["cluster"] == "pg_cluster_1"
                               else "pg_2",
        }

    def all_nodes(**overrides):
        rows = {}
        for name in meta:
            row = node_row(name)
            row.update(overrides.get(name, {}))
            rows[name] = row
        return rows

    def _line_with(needle):
        for ln in rendered.splitlines():
            if needle in ln:
                return ln
        return "<未找到>"

    en_comment = next(
        (ln.strip() for ln in rendered.splitlines()
         if ln.lstrip().startswith("#") and ln.strip().isascii()),
        "<未找到>")
    blank_ctx = rendered[
        rendered.index('\n\n    check "auto"'):][:40] \
        if '\n\n    check "auto"' in rendered else "<未找到>"
    ops.check(
        "阶段 1：检查线上配置格式特征",
        "中文/英文注释、Tab、非对齐缩进、连续空行、CRLF、末尾无换行和字符串内 # 均保留",
        "\n      ".join((
            "中文注释: %s" % _line_with("中文"),
            "英文注释: %s" % en_comment,
            "Tab 行: %r" % _line_with("\t"),
            "非对齐缩进: %r" % _line_with('  write_cluster "pg_cluster_1"'),
            "连续空行: %r" % blank_ctx,
            "CRLF 行数: %d" % rendered_bytes.count(b"\r\n"),
            "末尾字节: %r" % rendered_bytes[-30:],
            "字符串内 #: %s" % _line_with('"fbase#inside-string"'),
        )),
        all(marker in rendered for marker in (
            "中文", "# 线上共享物理节点", "\t",
            "  write_cluster \"pg_cluster_1\"",
            "\n\n    check \"auto\"", '"fbase#inside-string"',
        )) and b"\r\n" in rendered_bytes and not rendered_bytes.endswith(b"\n"),
    )

    ops.assert_groups(
        "阶段 1：检查 17 个 group 的模式、写中心与 promoted cluster",
        {
            "mmr_group": {"group_mode": "mmr", "write_cluster": "pg_cluster_2",
                          "promoted_cluster": "pg_cluster_1",
                          "write_port": str(ops.listen_port)},
            "mmr_group_b": {"group_mode": "mmr", "write_cluster": "pg_cluster_1",
                            "promoted_cluster": "pg_cluster_2"},
            "mmr_group_c": {"group_mode": "mmr", "write_cluster": "pg_cluster_2",
                            "promoted_cluster": "pg_cluster_1"},
            "mmr_group_d": {"group_mode": "mmr", "write_cluster": "pg_cluster_1",
                            "promoted_cluster": "pg_cluster_2"},
            "mmr_group_e": {"group_mode": "mmr", "write_cluster": "pg_cluster_2",
                            "promoted_cluster": "pg_cluster_1"},
            "mmr_group_f": {"group_mode": "mmr", "write_cluster": "pg_cluster_1",
                            "promoted_cluster": "pg_cluster_2"},
            "mmr_group_g": {"group_mode": "mmr", "write_cluster": "pg_cluster_2",
                            "promoted_cluster": "pg_cluster_1"},
            "mmr_group_h": {"group_mode": "mmr", "write_cluster": "pg_cluster_1",
                            "promoted_cluster": "pg_cluster_2"},
            "rep_group": {"group_mode": "replication",
                          "backend_clusters": "pg_cluster_1"},
            "rep_group_b": {"group_mode": "replication",
                            "backend_clusters": "pg_cluster_1"},
            "rep_group_c": {"group_mode": "replication",
                            "backend_clusters": "pg_cluster_1"},
            "balance_group": {"group_mode": "balance",
                              "access_mode": "read_write"},
            "balance_group_b": {"group_mode": "balance",
                                "access_mode": "read_only"},
            "balance_group_c": {"group_mode": "balance",
                                "access_mode": "read_write"},
            "single_group": {"group_mode": "single",
                             "access_mode": "read_write"},
            "single_group_b": {"group_mode": "single",
                               "access_mode": "read_only"},
            "single_group_c": {"group_mode": "single",
                               "access_mode": "read_only"},
        },
        row_count=17,
    )
    pool_text = conf.read_text(encoding="utf-8")
    pool_lines = {mode: _matching_line(pool_text, 'pool "%s"' % mode)
                  for mode in ("transaction", "session", "statement")}
    ops.check("阶段 1：检查 pool 模式组合", "session/transaction/statement 均在配置中",
             "transaction: %r\n      session: %r\n      statement: %r" % (
                 pool_lines["transaction"], pool_lines["session"],
                 pool_lines["statement"]),
             all(token in pool_text
                 for token in ('pool "transaction"', 'pool "session"', 'pool "statement"')))
    ops.assert_groups(
        "阶段 1：检查 MMR/REP 主组的多用户与 rw_split 方法集",
        {
            "mmr_group": {
                "user_names":
                    "ha_mmr_hint_tx,ha_mmr_port_tx,ha_mmr_sql_tx,"
                    "ha_mmr_session,postgres",
                "rw_split_methods": "hint,port,sql_parse,none,none",
            },
            "rep_group": {
                "user_names":
                    "ha_rep_hint_tx,ha_rep_port_tx,ha_rep_sql_tx,"
                    "ha_rep_session,postgres",
                "rw_split_methods": "hint,port,sql_parse,none,none",
            },
        },
    )
    ops.assert_members(
        "mmr_group",
        "阶段 1：检查 mmr_group 成员投影（两 cluster 各 7 节点）",
        {
            "pg_1": {"cluster_name": "pg_cluster_1", "weight": "10",
                     "group_role": "non-write-leader", "state": "active"},
            "pg_2": {"cluster_name": "pg_cluster_2", "weight": "10",
                     "group_role": "write-leader", "state": "active"},
            "pg_3": {"cluster_name": "pg_cluster_1", "weight": "10",
                     "group_role": "replica", "primary": "pg_1"},
            "pg_4": {"cluster_name": "pg_cluster_2", "weight": "10",
                     "group_role": "replica", "primary": "pg_2"},
        },
        user="postgres", row_count=14,
        retry_timeout=30,
    )
    ops.assert_nodes(
        "阶段 1：检查 14 个 datasource 的 cluster/权重/角色/状态",
        all_nodes(), row_count=14, retry_timeout=30)
    ops.assert_table(
        "SHOW NODE_STATUS;",
        "阶段 1：检查 mmr_group postgres 投影的初始路由角色",
        {
            "mmr_group|postgres|pg_1": {"group_role": "non-write-leader",
                                       "state": "active", "is_abnormal": "OK"},
            "mmr_group|postgres|pg_2": {"group_role": "write-leader",
                                       "state": "active", "is_abnormal": "OK"},
        },
        key=("group_name", "user", "node_name"), retry_timeout=30)
    ops.assert_routing("mmr_group", "阶段 2：检查 mmr_group 运行态路由",
                       write_cluster="pg_cluster_2", write_leader="pg_2",
                       retry_timeout=30)
    ops.assert_routing("rep_group", "阶段 2：检查 rep_group 运行态路由",
                       write_cluster="pg_cluster_1", write_leader="pg_1")
    for group in ("balance_group", "single_group"):
        ops.assert_routing(group, "阶段 2：检查 %s 运行态路由" % group)
    ops.assert_business_route(
        "SELECT inet_server_addr(), inet_server_port(), pg_is_in_recovery(), current_user;",
        "阶段 3：none 模式真实业务路由命中写中心",
        port=ports["mmr2"], retry_timeout=30)
    ops.assert_business_route(
        "SELECT current_user, inet_server_port(), pg_backend_pid();",
        "阶段 3：MMR session pool 用户命中当前写中心 pg_cluster_2",
        port=ports["mmr2"],
        group="mmr_group", user="ha_mmr_session", retry_timeout=30)
    ops.assert_business_route(
        "SELECT current_user, inet_server_port(), pg_is_in_recovery(), pg_backend_pid();",
        "阶段 3：REP session pool 用户命中 pg_cluster_1 primary",
        port=ports["mmr1"], recovery=False,
        group="rep_group", user="ha_rep_session", retry_timeout=30)
    member_ports = {str(item["port"]) for item in meta.values()}
    for user, group, label in (
            ("ha_mmr_hint_tx", "mmr_group", "MMR hint"),
            ("ha_mmr_port_tx", "mmr_group", "MMR port"),
            ("ha_mmr_sql_tx", "mmr_group", "MMR sql_parse"),
            ("ha_rep_hint_tx", "rep_group", "REP hint"),
            ("ha_rep_port_tx", "rep_group", "REP port"),
            ("ha_rep_sql_tx", "rep_group", "REP sql_parse")):
        ops.assert_business_route(
            "SELECT inet_server_addr(), inet_server_port(), pg_is_in_recovery(), current_user;",
            "阶段 3：%s 用户真实路由" % label,
            ports=member_ports, group=group, user=user)

    # Mixed endpoint/name batch: pg_3 is named, pg_4 is addressed by IPv4 endpoint.
    endpoint_pg4 = "%s:%s" % (host, ports["mmr2_standby1"])
    ops.psql(
        "SET NODE PARTED pg_3,%s;" % endpoint_pg4,
        "阶段 4：名称与 IPv4 host:port 混合批量 PARTED",
        "命令成功（SET NODE 完成标记）",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    ops.assert_nodes(
        "阶段 4：检查混合寻址后 pg_3/pg_4 均为 parted",
        {"pg_3": {"config_status": "parted"},
         "pg_4": {"config_status": "parted"}},
    )
    ops.psql(
        "SET NODE ACTIVE pg_3,%s;" % endpoint_pg4,
        "阶段 4：恢复混合寻址节点 ACTIVE",
        "命令成功（SET NODE 完成标记）",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    ops.assert_nodes(
        "阶段 4：pg_3/pg_4 恢复 active",
        {"pg_3": {"config_status": "active"},
         "pg_4": {"config_status": "active"}},
    )
    _wait_pg_cluster_ready(context, "pg_cluster_1", "pg_1", ("pg_3",))
    _wait_pg_cluster_ready(context, "pg_cluster_2", "pg_2", ("pg_4",))
    ops.psql(
        "SET NODE PARTED pg_3;",
        "阶段 4：准备批量部分无需修改场景",
        "仅 pg_3 进入 parted（SET NODE 完成标记）",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    ops.assert_nodes(
        "阶段 4：仅 pg_3 parted，pg_1/pg_4 不受影响",
        {"pg_3": {"config_status": "parted"},
         "pg_1": {"config_status": "active"},
         "pg_4": {"config_status": "active"}},
    )
    ops.psql(
        "SET NODE ACTIVE (pg_1,pg_3);",
        "阶段 4：批量 ACTIVE 部分目标无需修改",
        "pg_1 已 active 保持不变，pg_3 从 parted 恢复",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    ops.assert_nodes(
        "阶段 4：批量 ACTIVE 后 pg_1/pg_3 均为 active",
        {"pg_1": {"config_status": "active", "effective_role": "PRIMARY"},
         "pg_3": {"config_status": "active"}},
    )
    _wait_pg_cluster_ready(
        context, "pg_cluster_1", "pg_1", ("pg_3",),
        "阶段 4：验证部分无需修改的 ACTIVE 批量 monitor 投影")

    ops.psql(
        "SET NODE WEIGHT (pg_3=11,%s=12);" % endpoint_pg4,
        "阶段 5：批量 WEIGHT 全部目标修改",
        "命令成功（SET NODE 完成标记）",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    ops.assert_nodes(
        "阶段 5：pg_3 权重=11、endpoint(pg_4) 权重=12",
        {"pg_3": {"weight": "11"}, "pg_4": {"weight": "12"}},
    )
    ops.psql(
        "SET NODE WEIGHT (pg_3=10,%s=10);" % endpoint_pg4,
        "阶段 5：恢复批量权重",
        "命令成功（SET NODE 完成标记）",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    ops.assert_nodes(
        "阶段 5：权重恢复为 10",
        {"pg_3": {"weight": "10"}, "pg_4": {"weight": "10"}},
    )

    ops.psql_error(
        "SET NODE WEIGHT (pg_3=11,no_such_node=12);",
        "阶段 5：批量 WEIGHT 部分非法目标原子拒绝",
        "返回 ERROR 且错误信息含 no_such_node",
        lambda output: "ERROR" in output and "no_such_node" in output,
    )
    ops.assert_nodes(
        "阶段 5：原子拒绝后 pg_3 权重未被部分写入",
        {"pg_3": {"weight": "10"}},
    )

    groups_write_2 = ["mmr_group", "mmr_group_c", "mmr_group_e", "mmr_group_g"]
    groups_write_1 = ["mmr_group_b", "mmr_group_d", "mmr_group_f", "mmr_group_h"]
    all_mmr_groups = groups_write_2 + groups_write_1
    ops.psql(
        "SET NODE WRITE pg_1 IN GROUPS (%s);" % ",".join(groups_write_2),
        "阶段 6：4 个 MMR group 批量全修改 WRITE",
        "命令成功（SET NODE 完成标记）",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    wc1_text = conf.read_text(encoding="utf-8")
    not_switched = [g for g in all_mmr_groups
                    if 'write_cluster "pg_cluster_1"' not in
                    _datasource_block(
                        wc1_text.replace('group "', 'datasources "'), g)]
    ops.check(
        "阶段 6：检查批量全修改配置",
        "8 个 MMR group 当前均写向 pg_cluster_1",
        "符合=%d/8，未切换项=%s" % (8 - len(not_switched), not_switched or "无"),
        wc1_text.count('write_cluster "pg_cluster_1"') == 8 and not not_switched,
    )
    for group in groups_write_1:
        ops.assert_routing(
            group, "阶段 6：%s 切换后写中心为 pg_cluster_1" % group,
            write_cluster="pg_cluster_1", write_leader="pg_1")
    ops.psql(
        "SET NODE WRITE pg_2 IN GROUPS (%s);" % ",".join(groups_write_2),
        "阶段 6：恢复批量全修改的 4 个 MMR group",
        "命令成功（SET NODE 完成标记）",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    for group in groups_write_2:
        ops.assert_routing(
            group, "阶段 6：%s 恢复写中心为 pg_cluster_2" % group,
            write_cluster="pg_cluster_2", write_leader="pg_2")
    ops.psql(
        "SET NODE WRITE pg_1 IN GROUPS (%s);" % ",".join(all_mmr_groups),
        "阶段 6：8 个 MMR group 批量部分修改",
        "4 个 group 无需修改，另外 4 个切换到 pg_cluster_1",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    wc2_text = conf.read_text(encoding="utf-8")
    not_switched = [g for g in all_mmr_groups
                    if 'write_cluster "pg_cluster_1"' not in
                    _datasource_block(
                        wc2_text.replace('group "', 'datasources "'), g)]
    ops.check(
        "阶段 6：检查批量部分修改配置",
        "部分无需修改后 8 个 MMR group 均写向 pg_cluster_1",
        "符合=%d/8，未切换项=%s" % (8 - len(not_switched), not_switched or "无"),
        wc2_text.count('write_cluster "pg_cluster_1"') == 8 and not not_switched,
    )
    ops.psql(
        "SET NODE WRITE pg_2 IN GROUPS (%s);" % ",".join(groups_write_2),
        "阶段 6：恢复批量部分修改的 4 个变更 group",
        "命令成功（SET NODE 完成标记）",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )

    ops.psql_error(
        "SET NODE WRITE pg_1 IN GROUPS (mmr_group,mmr_group);",
        "阶段 6：重复 group 作用域拒绝",
        "返回语法错误且配置不变",
        lambda output: "ERROR" in output,
    )
    ops.psql(
        "SET NODE WRITE pg_1 IN GROUP mmr_group;",
        "阶段 6：MMR 指定 group 切换 WRITE",
        "命令成功（SET NODE 完成标记）",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    ops.assert_groups(
        "阶段 6：WRITE 联动 promoted 互换",
        {"mmr_group": {"write_cluster": "pg_cluster_1",
                       "promoted_cluster": "pg_cluster_2"}},
    )
    ops.assert_routing(
        "mmr_group", "阶段 6：mmr_group 写中心切到 pg_cluster_1",
        write_cluster="pg_cluster_1", write_leader="pg_1")
    ops.assert_business_route(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_port(), pg_is_in_recovery();",
        "阶段 6：WRITE 切换后业务命中 pg_cluster_1 写节点",
        port=ports["mmr1"], recovery=False, retry_timeout=30)
    # fb_console_command.c: PROMOTED 仅在 write!=target 且
    # promoted!=target 时落盘；两 cluster 组中两字段占满时恒为空操作。
    ops.psql(
        "SET NODE PROMOTED pg_2 IN GROUP mmr_group;",
        "阶段 7：PROMOTED 已是备用写中心的 cluster（幂等空操作）",
        "返回 NO CONFIG CHANGE，配置不变",
        lambda output: "NO CONFIG CHANGE" in output,
    )
    ops.psql(
        "SET NODE PROMOTED pg_1 IN GROUP mmr_group;",
        "阶段 7：PROMOTED 当前写中心 cluster（空操作）",
        "返回 NO CONFIG CHANGE，配置不变",
        lambda output: "NO CONFIG CHANGE" in output,
    )
    ops.assert_groups(
        "阶段 7：两次幂等 PROMOTED 后写/备中心不变",
        {"mmr_group": {"write_cluster": "pg_cluster_1",
                       "promoted_cluster": "pg_cluster_2"}},
    )
    ops.psql(
        "SET NODE WRITE pg_2 IN GROUP mmr_group;",
        "阶段 7：恢复 mmr_group 写中心到 pg_cluster_2",
        "命令成功（SET NODE 完成标记）",
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    ops.assert_groups(
        "阶段 7：写/备中心恢复初始值（WRITE 再次互换 promoted）",
        {"mmr_group": {"write_cluster": "pg_cluster_2",
                       "promoted_cluster": "pg_cluster_1"}},
    )
    ops.psql(
        "SET CLUSTER PARTED pg_cluster_1;",
        "阶段 8：SET CLUSTER PARTED 批量隔离 cluster",
        "命令成功（SET CLUSTER 完成标记）",
        lambda output: "SET CLUSTER" in output and "ERROR" not in output,
    )
    ops.assert_nodes(
        "阶段 8：pg_cluster_1 全部 7 节点进入 parted，pg_cluster_2 不受影响",
        {name: {"config_status": "parted"} for name in pg_cluster_1},
    )
    ops.assert_nodes(
        "阶段 8：pg_cluster_2 成员仍为 active",
        {name: {"config_status": "active"} for name in pg_cluster_2},
    )
    ops.psql(
        "SET CLUSTER ACTIVE pg_cluster_1;",
        "阶段 8：SET CLUSTER ACTIVE 恢复 cluster",
        "命令成功（SET CLUSTER 完成标记）",
        lambda output: "SET CLUSTER" in output and "ERROR" not in output,
    )
    ops.assert_nodes(
        "阶段 8：pg_cluster_1 全部恢复 active",
        {name: {"config_status": "active"} for name in pg_cluster_1},
    )
    _wait_pg_cluster_ready(
        context, "pg_cluster_1", "pg_1", ("pg_3",),
        "阶段 8：验证 cluster ACTIVE 后 monitor 投影恢复")
    # 阶段 7 已把 mmr_group 恢复为 write=pg_cluster_2/promoted=
    # pg_cluster_1；此处 WRITE 同一目标是幂等空操作（NO CONFIG
    # CHANGE），同时校验最终路由确实回到初始写中心。
    ops.psql(
        "SET NODE WRITE pg_2 IN GROUP mmr_group;",
        "阶段 10：幂等恢复初始 MMR write cluster",
        "返回 SET NODE 或 NO CONFIG CHANGE（已是目标写中心）",
        lambda output: ("SET NODE" in output or
                        "NO CONFIG CHANGE" in output)
        and "ERROR" not in output,
    )
    ops.assert_routing(
        "mmr_group", "阶段 10：mmr_group 写中心为 pg_cluster_2",
        write_cluster="pg_cluster_2", write_leader="pg_2")
    ops.assert_groups(
        "阶段 10：mmr_group 写/备中心恢复初始配置",
        {"mmr_group": {"write_cluster": "pg_cluster_2",
                       "promoted_cluster": "pg_cluster_1"}},
    )
    final_bytes = conf.read_bytes()
    final_text = final_bytes.decode("utf-8")
    preserved = [
        "中文注释行=%r" % next(
            (ln for ln in final_text.splitlines() if "中文" in ln), "<缺失>"),
        "Tab 行=%r" % next(
            (ln for ln in final_text.splitlines() if "\t" in ln), "<缺失>"),
        "非对齐缩进=%r" % next(
            (ln for ln in final_text.splitlines()
             if '  write_cluster "pg_cluster_1"' in ln), "<缺失>"),
        "字符串内 #=%r" % next(
            (ln for ln in final_text.splitlines()
             if '"fbase#inside-string"' in ln), "<缺失>"),
        "CRLF 行数=%d" % final_bytes.count(b"\r\n"),
        "末尾字节=%r" % final_bytes[-30:],
    ]
    ops.check(
        "阶段 10：检查高可用命令后格式内容仍保留",
        "中文注释、Tab、非对齐缩进、空行、CRLF 与字符串内 # 未被配置写回破坏",
        "\n      ".join(preserved),
        all(marker in final_text for marker in (
            "中文", "# 线上共享物理节点", "\t",
            "  write_cluster \"pg_cluster_1\"",
            "\n\n    check \"auto\"", '"fbase#inside-string"',
        )) and b"\r\n" in final_bytes and not final_bytes.endswith(b"\n"),
    )
    ops.diff(before, conf)


def _run_mixed_topology_pool_mode_batch_write(context):
    ops = context.ops
    conf = ops.start(transform=_mixed_topology_transform(context))
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    group_names = "mmr_group,mmr_hint_mix,mmr_sql_mix"
    ops.assert_routing(
        "mmr_group", "查看混合拓扑批量命令前的基准 MMR 状态",
        write_cluster="pg_cluster_2", write_leader="pg_2")
    ops.assert_groups(
        "查看混合拓扑只读 Balance 配置",
        {"balance_read_mix": {"group_mode": "balance",
                              "access_mode": "read_only"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WRITE pg_1 IN GROUPS (%s);' % group_names,
            "混合 topology 中批量切换 3 个 MMR group 的写中心",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证混合 MMR 批量切换备份")
    text = conf.read_text(encoding="utf-8")
    for group in ("mmr_group", "mmr_hint_mix", "mmr_sql_mix"):
        block = _datasource_block(text.replace('group "', 'datasources "'), group)
        ops.check("验证混合配置 group %s 的 write_cluster 已切换" % group,
                 "write_cluster 为 pg_cluster_1",
                 "group %s 命中行: %r" % (group, _matching_line(block, 'write_cluster')),
                 'write_cluster "pg_cluster_1"' in block)
    ops.diff_contains(before, conf,
                     ('-    write_cluster "pg_cluster_2"', '+    write_cluster "pg_cluster_1"'),
                     "验证混合配置只替换目标 MMR 的写中心")
    ops.assert_routing(
        "mmr_hint_mix", "查看混合配置批量切换后的 hint MMR 状态",
        write_cluster="pg_cluster_1", write_leader="pg_1")
    ops.assert_routing(
        "rep_port_mix", "确认混合配置 REP port group 未被批量 MMR 命令修改",
        write_cluster="pg_cluster_1", write_leader="pg_1")
    expected_mmr1 = ops.env.config["database"]["ports"]["mmr1"]
    expected_mmr2 = ops.env.config["database"]["ports"]["mmr2"]
    ops.assert_business_route(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_port(), current_user;",
        "验证批量 WRITE 后 MMR hint 真实路由",
        port=expected_mmr1, group="mmr_hint_mix", user="mix_hint",
        retry_timeout=30)
    ops.assert_business_route(
        "BEGIN; CREATE TEMP TABLE ha_mixed_sql_probe(id int); "
        "SELECT inet_server_port(); ROLLBACK;",
        "验证批量 WRITE 后 MMR sql_parse 真实路由",
        port=expected_mmr1, group="mmr_sql_mix", user="mix_sql",
        retry_timeout=30)
    ops.psql('SET CLUSTER PARTED pg_cluster_1;',
            "在 mixed topology 隔离当前 MMR write/REP backend cluster",
            "返回 SET CLUSTER，并由各路由入口立即观察新状态",
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    ops.assert_business_route(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; "
        "SELECT inet_server_port(), current_user;",
        "验证 cluster PARTED 后 MMR hint promoted 路由",
        port=expected_mmr2, group="mmr_hint_mix", user="mix_hint",
        retry_timeout=30)
    ops.assert_business_route(
        "BEGIN; CREATE TEMP TABLE ha_mixed_sql_failover(id int); "
        "SELECT inet_server_port(); ROLLBACK;",
        "验证 cluster PARTED 后 MMR sql_parse promoted 路由",
        port=expected_mmr2, group="mmr_sql_mix", user="mix_sql",
        retry_timeout=30)
    ops.psql_business_error(
        "SELECT inet_server_port();",
        "验证 cluster PARTED 后 REP port 路由不可用",
        "rep_port_mix 唯一 backend cluster 已隔离，业务连接失败",
        lambda output: "ERROR" in output or "server" in output.lower(),
        group="rep_port_mix", port=ops.listen_port, user="mix_port")
    ops.psql('SET CLUSTER ACTIVE pg_cluster_1;',
            "恢复 mixed topology 的 pg_cluster_1",
            "返回 SET CLUSTER，MMR 与 REP 路由候选恢复",
            lambda output: "SET CLUSTER" in output and "ERROR" not in output)
    _wait_pg_cluster_ready(
        context, "pg_cluster_1", "pg_1", ("pg_3",),
        "等待 mixed topology 的 pg_cluster_1 路由投影恢复")
    ops.assert_business_route(
        "SELECT inet_server_port(), current_user;",
        "验证 cluster ACTIVE 后 REP port 路由恢复",
        port=expected_mmr1, group="rep_port_mix", user="mix_port",
        retry_timeout=30)
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WRITE pg_2 IN GROUPS (%s);' % group_names,
            "恢复混合 topology 中 3 个 MMR group 的写中心",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证混合 MMR 批量恢复备份")
    ops.diff(before, conf)
    ops.assert_routing(
        "mmr_sql_mix", "查看混合配置恢复后的 sql_parse MMR 状态",
        write_cluster="pg_cluster_2", write_leader="pg_2")


def _run_batch_mixed_no_change_and_change(context):
    ops = context.ops
    conf = ops.start()
    initial = ops.workdir / "initial.conf"
    initial.write_bytes(conf.read_bytes())
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE PARTED pg_3;', "准备部分目标已满足的状态",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证准备状态的配置备份")
    mixed_before = ops.workdir / "before-mixed-command.conf"
    mixed_before.write_bytes(conf.read_bytes())
    pg_3_before = _datasource_block(mixed_before.read_text(encoding="utf-8"), "pg_3")
    ops.assert_table(
        'SHOW DATASOURCES;', "查看混合批量命令前的运行态",
        {"pg_3": {"config_status": "parted"},
         "pg_4": {"config_status": "active"}},
        key="node_name")
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE PARTED pg_3,pg_4;',
            "批量处理已为 parted 的 pg_3 和待修改的 pg_4",
            "整条命令返回 SET NODE，只修改仍为 active 的 pg_4",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证混合 NO CHANGE/CHANGE 只生成一个备份")
    ops.diff_contains(mixed_before, conf,
                     ('-    status "active"', '+    status "parted"'),
                     "验证混合批量命令只落盘待修改目标")
    pg_3_after = _datasource_block(conf.read_text(encoding="utf-8"), "pg_3")
    ops.check("验证已满足目标未被重复修改",
             "pg_3 datasource block 逐字节保持不变",
             "修改后 block=%r" % pg_3_after,
             pg_3_before == pg_3_after)
    ops.assert_table(
        'SHOW DATASOURCES;', "查看混合批量命令后的运行态",
        {"pg_3": {"config_status": "parted"},
         "pg_4": {"config_status": "parted"}},
        key="node_name")
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE ACTIVE pg_3,pg_4;', "恢复混合批量状态",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证混合批量恢复备份")
    ops.diff(initial, conf)
    _wait_pg_cluster_ready(context, "pg_cluster_1", "pg_1", ("pg_3",))
    _wait_pg_cluster_ready(context, "pg_cluster_2", "pg_2", ("pg_4",))


def _run_bulk_30_group_write_roundtrip(context):
    ops = context.ops
    conf = ops.start(transform=_add_bulk_mmr_groups)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    names = ['bulk_mmr_%02d' % index for index in range(1, 31)]
    group_list = ','.join(names)
    for group in (names[0], names[-1]):
        ops.assert_routing(
            group, "查看 30 组切换前 %s 的运行态" % group,
            write_cluster="pg_cluster_2", write_leader="pg_2")
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WRITE pg_1 IN GROUPS (%s);' % group_list, "一次切换 30 个 MMR group 的写中心", "返回 SET NODE 且命令不报错", lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 30 组切换的配置备份")
    missing = _bulk_groups_missing(
        conf.read_text(encoding="utf-8"), "pg_cluster_1", "pg_cluster_2")
    ops.check("验证 30 个 group 全部原子落盘",
             "30 个 group 均已切换到 write=pg_cluster_1/promoted=pg_cluster_2",
             "符合=%d/30，不符项=%s" % (30 - len(missing), missing or "无"),
             not missing)
    ops.diff_contains(before, conf, ('-    write_cluster "pg_cluster_2"', '+    write_cluster "pg_cluster_1"'), "验证 30 组写中心切换配置 diff")
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WRITE pg_2 IN GROUPS (%s);' % group_list, "一次恢复 30 个 MMR group 的写中心", "返回 SET NODE 且命令不报错", lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 30 组恢复的配置备份")
    ops.diff(before, conf)


def _run_bulk_30_groups_invalid_target(context):
    ops = context.ops
    conf = ops.start(transform=_add_bulk_mmr_groups)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    names = ['bulk_mmr_%02d' % i for i in range(1, 31)]
    group_list = ','.join(names + ['no_such_group'])
    ops.assert_routing(
        "bulk_mmr_01", "查看批量非法 group 命令前状态",
        write_cluster="pg_cluster_2", write_leader="pg_2")
    backup = ops.backup_checkpoint(conf)
    ops.psql_error('SET NODE WRITE pg_1 IN GROUPS (%s);' % group_list,
                  "30 个合法 group 混入不存在 group 时原子拒绝",
                  "返回 no_such_group does not exist",
                  lambda output: "no_such_group" in output and "does not exist" in output)
    ops.assert_no_backup_created(backup, conf, "验证批量非法 group 未创建备份")
    ops.diff(before, conf)
    missing = _bulk_groups_missing(
        conf.read_text(encoding="utf-8"), "pg_cluster_2", "pg_cluster_1")
    ops.check("验证 30 个合法 group 未部分修改",
             "全部保持初始 write=pg_cluster_2/promoted=pg_cluster_1",
             "符合=%d/30，不符项=%s" % (30 - len(missing), missing or "无"),
             not missing)
    ops.assert_routing(
        "bulk_mmr_30", "查看批量非法 group 命令后状态",
        write_cluster="pg_cluster_2", write_leader="pg_2")


def _run_duplicate_and_mixed_status_targets(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    backup = ops.backup_checkpoint(conf)
    ops.assert_table(
        'SHOW DATASOURCES;', "查看重复状态命令前的运行态",
        {"pg_3": {"config_status": "active"},
         "pg_4": {"config_status": "active"}},
        key="node_name")
    ops.psql(
        'SET NODE PARTED pg_3,pg_3,pg_4;',
        "执行重复与混合 datasource 的 PARTED 命令",
        '返回 SET NODE 且命令不报错',
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    ops.assert_backup_created(backup, conf, "验证重复与混合 PARTED 的配置备份")
    ops.diff_contains(
        before, conf,
        ('+    status "parted"',),
        "验证重复与混合状态目标的配置 diff",
    )
    backup = ops.backup_checkpoint(conf)
    ops.assert_table(
        'SHOW DATASOURCES;', "查看重复与混合 PARTED 命令后的运行态",
        {"pg_3": {"config_status": "parted"},
         "pg_4": {"config_status": "parted"}},
        key="node_name")
    ops.psql(
        'SET NODE ACTIVE pg_3,pg_3,pg_4;',
        "恢复重复与混合 datasource 的 ACTIVE 状态",
        '返回 SET NODE 且命令不报错',
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    ops.assert_backup_created(backup, conf, "验证重复与混合 ACTIVE 的配置备份")
    ops.diff(before, conf)
    _wait_pg_cluster_ready(context, "pg_cluster_1", "pg_1", ("pg_3",))
    _wait_pg_cluster_ready(context, "pg_cluster_2", "pg_2", ("pg_4",))


