"""High-availability persistence command test manifest.

The first case intentionally mirrors the first idempotent SET NODE WRITE
scenario in ``高可用命令测试.md`` while using the regression fixture's dynamic
PostgreSQL ports.
"""

from .case import HaCommandCase


HA_COMMAND_CASES = (
    HaCommandCase(
        name="jdbc_console_ha_commands",
        summary="JDBC Simple Query 控制台连接覆盖全部高可用管理命令并恢复配置",
        source_sections=("sources/console.c:od_console_set", "sources/fb_console_command.c",),
        executor="jdbc_console_ha_commands",
        report_groups=("mmr_group", "single_group"),
        report_all_datasources=True,
        notes=(
            "JDBC 使用 preferQueryMode=simple 连接 console，覆盖 JDBC 初始化 application_name。",
            "覆盖 SET NODE ACTIVE/PARTED/WEIGHT/WRITE/PROMOTED。",
            "覆盖 SET CLUSTER ACTIVE/PARTED 和 REFRESH CLUSTER，并恢复初始配置。",
        ),
    ),
    HaCommandCase(
        name="comprehensive_all_groups_and_commands",
        summary="复杂多组配置下覆盖路由、寻址、批量原子性及主要高可用命令并恢复",
        source_sections=("sources/fb_console_command.c:SET NODE/SET CLUSTER",),
        executor="comprehensive_all_groups_and_commands",
        report_groups=(
            "mmr_group", "mmr_group_b", "mmr_group_c", "mmr_group_d",
            "mmr_group_e", "mmr_group_f", "mmr_group_g", "mmr_group_h",
            "rep_group", "rep_group_b", "rep_group_c",
            "balance_group", "balance_group_b", "balance_group_c",
            "single_group", "single_group_b", "single_group_c",
        ),
        report_all_datasources=True,
        notes=(
            "覆盖 8 个 MMR、3 个 Replication、3 个 Balance、3 个 Single group，全部 check auto。",
            "配置包含 none、hint、port、sql_parse 以及 session、transaction、statement pool 的合法组合。",
            "覆盖名称、IPv4 host:port、多组批量全修改、部分无需修改和部分失败原子拒绝。",
            "配置包含 20 个真实 datasource，并故意使用中文/英文注释、Tab、非对齐缩进、连续空行、CRLF 和末尾无换行；命令前后检查格式保留。",
            "不包含 REFRESH CLUSTER、SET AUTH 和 IPv6 endpoint。",
        ),
    ),
    HaCommandCase(
        name="set_node_write_idempotent",
        summary="SET NODE WRITE 已是当前写中心时不修改配置并保持运行态",
        source_sections=("控制台命令与持久化实现.md:12.2",),
        executor="set_node_write_idempotent",
        notes=(
            "SET NODE WRITE 目标已是当前 write cluster 时客户端返回 SET NODE，NO CHANGE 详情写日志。",
            "配置文件无 diff，不能创建新的持久化变更。",
            "SHOW GROUP_ROUTING 显示 mmr_group 仍由 pg_cluster_2 写入，pg_2 为 write-leader。",
        ),
    ),
    HaCommandCase(
        name="set_node_promoted_idempotent",
        summary="SET NODE PROMOTED 已是当前提升中心时不修改配置并保持运行态",
        source_sections=("控制台命令与持久化实现.md:13.2",),
        executor="set_node_promoted_idempotent",
        notes=(
            "SET NODE PROMOTED 目标已是当前 promoted cluster 时客户端返回 SET NODE，NO CHANGE 详情写日志。",
            "配置文件无 diff，不能创建新的持久化变更。",
            "SHOW GROUP_ROUTING 显示 mmr_group 仍由 pg_cluster_1 作为 promoted cluster。",
        ),
    ),
    HaCommandCase(
        name="set_cluster_active_idempotent",
        summary="SET CLUSTER ACTIVE 已是当前状态时不修改配置并保持运行态",
        source_sections=("控制台命令与持久化实现.md:15.4.3",),
        executor="set_cluster_active_idempotent",
        notes=(
            "SET CLUSTER ACTIVE 目标 cluster 的所有 datasource 已为 active。",
            "配置文件无 diff，不能创建新的持久化变更。",
            "SHOW GROUP_ROUTING 仍显示 pg_cluster_1 为 VALID，primary 为 pg_1。",
        ),
    ),
    HaCommandCase(
        name="console_set_validation_toggle",
        summary="console_set_validation 开关控制普通 SET 校验并支持 Reload 动态切换",
        source_sections=("sources/console.c:od_console_query", "sources/console.c:od_console_set"),
        executor="console_set_validation_toggle",
        notes=(
            "默认 yes 时接受普通 GUC 的 =、TO 和 SET TIME ZONE 白名单语法。",
            "默认 yes 时拒绝不完整或非白名单 SET。",
            "Reload 切换为 no 后不校验普通 SET，日志记录配置变化。",
            "再次 Reload 切回 yes 后恢复普通 SET 校验，日志记录配置变化。",
        ),
    ),
    HaCommandCase(
        name="set_cluster_write_promoted_roundtrip",
        summary="SET CLUSTER WRITE/PROMOTED 切换并恢复 MMR 写中心配置",
        source_sections=("sources/fb_console_command.c:SET CLUSTER WRITE/PROMOTED",),
        executor="set_cluster_write_promoted_roundtrip",
        notes=(
            "验证 SET CLUSTER WRITE 按 cluster 目标修改 write_cluster 并同步 promoted_cluster。",
            "验证 SET CLUSTER PROMOTED 修改 promoted_cluster，且运行态 group 投影同步。",
            "验证真实业务写路由切换到目标 cluster，恢复后配置与运行态回到初始状态。",
        ),
    ),
    HaCommandCase(
        name="set_cluster_parted_active_roundtrip",
        summary="SET CLUSTER PARTED/ACTIVE 批量隔离并恢复整个 cluster",
        source_sections=("控制台命令与持久化实现.md:15.4.1",),
        executor="set_cluster_parted_active_roundtrip",
        report_groups=("mmr_group", "rep_group", "balance_group", "single_group"),
        report_all_datasources=True,
        notes=(
            "SET CLUSTER PARTED 将目标 cluster 的全部 datasource 持久化为 parted。",
            "SHOW DATASOURCES 和配置 diff 同时证明两个成员均已隔离。",
            "SET CLUSTER ACTIVE 恢复全部成员，最终配置与初始快照一致。",
        ),
    ),
    HaCommandCase(
        name="set_node_write_switch_and_restore",
        summary="SET NODE WRITE 切换写中心并切回初始中心，配置与运行态同步恢复",
        source_sections=("控制台命令与持久化实现.md:12.2",),
        executor="set_node_write_switch_and_restore",
        notes=(
            "切换到 pg_1 后 write_cluster 持久化为 pg_cluster_1。",
            "SHOW GROUP_ROUTING 显示 pg_1 为 write-leader。",
            "切回 pg_2 后 write_cluster 恢复为 pg_cluster_2，最终文件与初始文件一致。",
        ),
    ),
    HaCommandCase(
        name="set_node_write_all_related_groups",
        summary="SET NODE WRITE 未指定 group 时切换所有关联 MMR group",
        source_sections=("控制台命令与持久化实现.md:11.3-默认全组范围",),
        executor="set_node_write_all_related_groups",
        notes=(
            "同一 datasource cluster 同时关联两个 MMR group。",
            "不带 IN GROUP/IN GROUPS 的 SET NODE WRITE 必须原子修改两个 group。",
            "两个 group 的 SHOW 运行态均切换，恢复后配置与初始快照一致。",
        ),
    ),
    HaCommandCase(
        name="refresh_cluster",
        summary="REFRESH CLUSTER 在 monitor 开启/关闭时触发节点与端点复探并验证故障恢复",
        source_sections=("控制台命令与持久化实现.md:16.5.1",),
        executor="refresh_cluster",
        notes=(
            "分别覆盖 monitor_enabled=no 与 yes（monitor_period=300）两种场景。",
            "停止并恢复 pg_3，使用 SHOW NODE_MONITOR/SHOW ENDPOINT_MONITOR 验证故障和恢复状态。",
            "REFRESH CLUSTER 只刷新运行态，不修改配置。",
        ),
    ),
    HaCommandCase(
        name="set_node_invalid_datasource",
        summary="SET NODE WRITE 的不存在 datasource 返回明确错误且不修改配置",
        source_sections=("控制台命令与持久化实现.md:7.2",),
        executor="set_node_invalid_datasource",
        notes=(
            "不存在 datasource 返回 ERROR，错误文本包含输入目标和 does not exist。",
            "失败命令不写回配置，文件无 diff。",
        ),
    ),
    HaCommandCase(
        name="set_node_write_in_groups_roundtrip",
        summary="SET NODE WRITE IN GROUPS 指定多个 MMR group 切换并恢复写中心",
        source_sections=("sources/fb_console_command.c:1051",),
        executor="set_node_write_in_groups_roundtrip",
        report_groups=("mmr_group", "mmr_group_extra"),
        notes=(
            "IN GROUPS 明确指定两个关联 MMR group。",
            "切换后两个 group 的运行态和配置均指向 pg_cluster_1。",
            "恢复后两个 group 回到 pg_cluster_2，配置与初始快照一致。",
        ),
    ),
    HaCommandCase(
        name="set_node_promoted_in_groups_missing_field",
        summary="SET NODE PROMOTED IN GROUPS 为多个缺省 group 补写提升中心",
        source_sections=("sources/fb_console_command.c:1103",),
        executor="set_node_promoted_in_groups_missing_field",
        report_groups=("mmr_group", "mmr_group_extra"),
        notes=("验证两个缺失 promoted 字段的 group 前后 SHOW、原子补写和正确备份。",),
    ),
    HaCommandCase(
        name="set_node_write_in_groups_invalid_group",
        summary="SET NODE WRITE IN GROUPS 混入不存在 group 时原子拒绝",
        source_sections=("sources/fb_console_command.c:1110",),
        executor="set_node_write_in_groups_invalid_group",
        notes=(
            "IN GROUPS 同时包含有效 group 和不存在 group。",
            "命令返回明确错误且不产生部分配置写入。",
            "有效 group 的运行态仍保持原 write cluster。",
        ),
    ),
    HaCommandCase(
        name="duplicate_and_conflicting_weights",
        summary="SET NODE WEIGHT 重复同值去重且冲突值原子拒绝",
        source_sections=("sources/fb_console_command.c:604",),
        executor="duplicate_and_conflicting_weights",
        notes=(
            "同一 datasource 重复指定相同 weight 时只持久化一次。",
            "恢复初始 weight 后配置回到初始快照。",
            "同一 datasource 指定不同 weight 时整条拒绝且运行态不变。",
        ),
    ),
    HaCommandCase(
        name="duplicate_and_mixed_status_targets",
        summary="SET NODE PARTED 重复目标去重并正确处理混合 datasource",
        source_sections=("sources/fb_console_command.c:604",),
        executor="duplicate_and_mixed_status_targets",
        notes=(
            "同一 datasource 在状态列表中重复出现时只执行一次持久化。",
            "重复目标与其他 datasource 混合时各目标均正确更新。",
            "ACTIVE 恢复后配置和运行态回到初始状态。",
        ),
    ),
    HaCommandCase(
        name="name_endpoint_status_deduplication",
        summary="SET NODE 状态命令按名称和 endpoint 定位同一 datasource 时去重",
        source_sections=("sources/fb_console_command.c:560",),
        executor="name_endpoint_status_deduplication",
        notes=(
            "datasource 名称和 host:port endpoint 解析到同一对象。",
            "PARTED 命令只产生一次配置修改且运行态正确。",
            "使用相同混合输入恢复 ACTIVE 后回到初始状态。",
        ),
    ),
    HaCommandCase(
        name="batch_mixed_no_change_and_change",
        summary="批量状态命令中已满足目标与待修改目标共同提交",
        source_sections=("sources/fb_console_command.c:922",),
        executor="batch_mixed_no_change_and_change",
        notes=(
            "pg_3 已为 parted、pg_4 仍为 active 时批量执行 PARTED。",
            "命令整体成功且只修改 pg_4，不重复修改 pg_3。",
            "只生成一个备份，恢复后配置和运行态回到初始状态。",
        ),
    ),
    HaCommandCase(
        name="set_node_write_in_groups_duplicate_group",
        summary="SET NODE WRITE IN GROUPS 重复 group 名称时拒绝命令",
        source_sections=("sources/fb_console_command.c:354",),
        executor="set_node_write_in_groups_duplicate_group",
        notes=(
            "执行前记录目标 group 的运行态。",
            "IN GROUPS 重复指定同一 group 时返回语法错误。",
            "配置文件和有效 group 运行态均保持不变。",
        ),
    ),
    HaCommandCase(
        name="set_node_write_in_groups_cardinality_errors",
        summary="SET NODE WRITE IN GROUPS 空列表或单 group 时拒绝命令",
        source_sections=("sources/fb_console_command.c:381",),
        executor="set_node_write_in_groups_cardinality_errors",
        notes=(
            "IN GROUPS 空列表和单 group 均返回语法错误。",
            "每次失败命令前后配置文件保持一致。",
            "目标 group 运行态不发生变化。",
        ),
    ),
    HaCommandCase(
        name="batch_status_invalid_target_atomicity",
        summary="SET NODE PARTED 批量目标混入不存在 datasource 时原子拒绝",
        source_sections=("sources/fb_console_command.c:604",),
        executor="batch_status_invalid_target_atomicity",
        notes=(
            "批量列表包含有效 datasource 和不存在 datasource。",
            "命令返回明确错误且配置文件不产生部分修改。",
            "有效 datasource 的运行态保持 active。",
        ),
    ),
    HaCommandCase(
        name="set_node_weight_invalid_values",
        summary="SET NODE WEIGHT 非法数值和缺失赋值时拒绝命令",
        source_sections=("sources/fb_console_command.c:286",),
        executor="set_node_weight_invalid_values",
        notes=(
            "负数、超过 INT_MAX 和缺失 weight 均返回语法错误。",
            "每次失败均不修改配置且不创建备份。",
            "目标 datasource 的运行态 weight 保持不变。",
        ),
    ),
    HaCommandCase(
        name="weight_sum_overflow_rejected",
        summary="合法单节点权重导致 group 总和溢出时原子拒绝",
        source_sections=("sources/fb_console_command.c:766",),
        executor="weight_sum_overflow_rejected",
        notes=("验证 INT_MAX 单值触发 group 总和溢出、无备份、无 diff、前后 SHOW 不变。",),
    ),
    HaCommandCase(
        name="locked_invalid_numeric_token",
        summary="锁内配置解析拒绝 10abc 非法数字 token",
        source_sections=("sources/fb_config_writer.c:648",),
        executor="locked_invalid_numeric_token",
        notes=("运行中注入非法数字 token，验证对象解析错误、无备份、无 diff 和前后 SHOW。",),
    ),
    HaCommandCase(
        name="locked_disk_object_resolution",
        summary="高可用写回按锁内磁盘 datasource 名称定位对象",
        source_sections=("sources/fb_config_writer.c:1335",),
        executor="locked_disk_object_resolution",
        notes=("启动后以磁盘对象名修改和恢复权重，验证备份、diff 和拓扑运行态。",),
    ),
    HaCommandCase(
        name="set_node_weight_zero_roundtrip",
        summary="SET NODE WEIGHT 支持零权重并可恢复初始值",
        source_sections=("sources/fb_console_command.c:286",),
        executor="set_node_weight_zero_roundtrip",
        notes=(
            "weight 0 是合法边界值。",
            "修改和恢复分别产生一个内容正确的备份。",
            "SHOW NODES 与配置文件同步显示修改和恢复结果。",
        ),
    ),
    HaCommandCase(
        name="set_cluster_invalid_commands",
        summary="SET CLUSTER 不存在目标或非法动作时拒绝命令",
        source_sections=("sources/fb_console_command.c:464",),
        executor="set_cluster_invalid_commands",
        notes=(
            "不存在 cluster 返回明确错误。",
            "SET CLUSTER 非法动作返回语法错误。",
            "失败命令不修改配置、不创建备份且运行态不变。",
        ),
    ),
    HaCommandCase(
        name="refresh_cluster_syntax_errors",
        summary="REFRESH CLUSTER 缺失或多余参数时拒绝命令",
        source_sections=("sources/fb_console_command.c:1424",),
        executor="refresh_cluster_syntax_errors",
        notes=(
            "缺少 cluster 名、错误关键字和额外参数均返回语法错误。",
            "每次失败均不修改配置且不创建备份。",
            "cluster 运行态保持不变。",
        ),
    ),
    HaCommandCase(
        name="refresh_cluster_probe_edges",
        summary="REFRESH CLUSTER 未知名称拒绝、全节点离线仍完成一次性探测及恢复",
        source_sections=(
            "sources/fb_console_command.c:fb_console_command_refresh_cluster",
            "sources/system.c:fb_system_monitor_refresh_cluster",
            "sources/monitor/fb_monitor_thread.c:fb_monitor_submit_probe_by_key",
        ),
        executor="refresh_cluster_probe_edges",
        notes=(
            "分别覆盖 monitor_enabled=no（REFRESH 同步等待探测完成）与 "
            "yes（monitor_period=300，检测只能来自 REFRESH 驱动的一次性探测）。",
            "不存在的 cluster 名、误传 datasource 名、误传 group 名均返回 "
            "cluster does not exist，不修改配置、不创建备份、运行态不变。",
            "cluster 全部 datasource 离线时探测轮仍完成并返回 REFRESH CLUSTER，"
            "NODE_MONITOR 显示 pg_1/pg_3 connect_status=OFFLINE；恢复节点后"
            "再次 REFRESH 回到 ONLINE。",
        ),
    ),
    HaCommandCase(
        name="set_node_role_rejects_parted_target",
        summary="SET NODE WRITE/PROMOTED 拒绝处于 PARTED 状态的目标",
        source_sections=("sources/fb_console_command.c:1078",),
        executor="set_node_role_rejects_parted_target",
        notes=(
            "先将目标 datasource 人工隔离为 PARTED。",
            "WRITE 和 PROMOTED 均拒绝非 active 目标且不创建额外备份。",
            "恢复 ACTIVE 后配置和运行态回到初始状态。",
        ),
    ),
    HaCommandCase(
        name="set_node_role_rejects_unrelated_group",
        summary="SET NODE WRITE/PROMOTED 拒绝目标 cluster 未关联的 MMR group",
        source_sections=("sources/fb_console_command.c:1115",),
        executor="set_node_role_rejects_unrelated_group",
        report_groups=("mmr_group_one_cluster",),
        notes=(
            "测试 group 仅关联 pg_cluster_1。",
            "使用 pg_cluster_2 的 datasource 执行 WRITE/PROMOTED 时返回错误。",
            "配置、备份集合和 group 运行态保持不变。",
        ),
    ),
    HaCommandCase(
        name="weight_format_preservation",
        summary="SET NODE WEIGHT 修改值时保持混合缩进和行尾注释",
        source_sections=("sources/fb_config_writer.c",),
        executor="weight_format_preservation",
        notes=(
            "目标 weight 字段使用 tab、额外空格和行尾注释。",
            "修改只替换数值，不改变字段周边格式。",
            "恢复后配置与初始快照一致且两次备份内容正确。",
        ),
    ),
    HaCommandCase(
        name="write_cluster_format_preservation",
        summary="SET NODE WRITE 同时修改两个 group 字段时保持原格式",
        source_sections=("sources/fb_config_writer.c",),
        executor="write_cluster_format_preservation",
        notes=(
            "write_cluster 和 promoted_cluster 使用不同缩进、空格和注释。",
            "WRITE 切换同时替换两个值但不重排周边文本。",
            "恢复后配置完全回到初始快照且备份内容正确。",
        ),
    ),
    HaCommandCase(
        name="status_format_preservation",
        summary="SET NODE PARTED/ACTIVE 修改状态时保持字段原格式",
        source_sections=("sources/fb_config_writer.c",),
        executor="status_format_preservation",
        notes=(
            "status 字段使用 tab、额外空格和行尾注释。",
            "PARTED/ACTIVE 只替换引号内状态值。",
            "恢复后配置完全回到初始快照且两次备份内容正确。",
        ),
    ),
    HaCommandCase(
        name="crlf_format_preservation",
        summary="SET NODE WEIGHT 写回时保持 CRLF 换行格式",
        source_sections=("sources/fb_config_writer.c",),
        executor="crlf_format_preservation",
        notes=(
            "整个配置文件使用 CRLF 换行。",
            "修改和恢复过程中不得引入裸 LF 或转换换行格式。",
            "恢复后配置字节与初始快照一致且备份内容正确。",
        ),
    ),
    HaCommandCase(
        name="eof_without_newline_preservation",
        summary="SET NODE WEIGHT 写回时保持 EOF 无结尾换行",
        source_sections=("sources/fb_config_writer.c",),
        executor="eof_without_newline_preservation",
        notes=(
            "初始配置文件结尾没有换行符。",
            "修改后仍不得自动添加结尾换行。",
            "恢复后配置字节与初始快照一致且备份内容正确。",
        ),
    ),
    HaCommandCase(
        name="single_line_block_preservation",
        summary="SET NODE WEIGHT 修改单行 datasource block 时保持单行格式",
        source_sections=("sources/fb_config_writer.c",),
        executor="single_line_block_preservation",
        notes=(
            "目标 datasource 的完整 block 位于同一行。",
            "修改 weight 时 block 不展开、不重排其他字段。",
            "恢复后配置字节与初始快照一致且备份内容正确。",
        ),
    ),
    HaCommandCase(
        name="hash_inside_string_preservation",
        summary="SET NODE WEIGHT 写回时保持字符串内部的 # 字符",
        source_sections=("sources/fb_config_writer.c",),
        executor="hash_inside_string_preservation",
        notes=(
            "配置中的字符串值包含 #，不能被解析为注释起点。",
            "修改目标字段时包含 # 的非目标字符串逐字保持。",
            "恢复后配置与初始快照一致且备份内容正确。",
        ),
    ),
    HaCommandCase(
        name="set_node_weight_idempotent",
        summary="SET NODE WEIGHT 已是目标值时不修改配置并保持运行态",
        source_sections=("sources/fb_console_command.c:923",),
        executor="set_node_weight_idempotent",
        notes=(
            "目标 datasource 当前 weight 已等于请求值。",
            "客户端返回 SET NODE，NO CHANGE 详情写日志，且配置文件无 diff。",
            "SHOW NODES 仍显示原 weight。",
        ),
    ),
    HaCommandCase(
        name="set_node_weight_switch_and_restore",
        summary="SET NODE WEIGHT 修改并恢复，持久化与运行态同步",
        source_sections=("控制台命令与持久化实现.md:10.4",),
        executor="set_node_weight_switch_and_restore",
        notes=(
            "pg_3 的 weight 从 10 改为 11 后配置 diff 精确记录变更。",
            "SHOW NODES 显示 pg_3 的 weight 为 11。",
            "恢复为 10 后配置文件回到初始内容。",
        ),
    ),
    HaCommandCase(
        name="set_node_parted_active_roundtrip",
        summary="SET NODE PARTED/ACTIVE 单节点状态往返并恢复运行态",
        source_sections=("控制台命令与持久化实现.md:8.3,9.2",),
        executor="set_node_parted_active_roundtrip",
        notes=(
            "pg_3 PARTED 后配置持久化为 status parted，主库 pg_1 仍保持可用。",
            "恢复 ACTIVE 后 status 回到 active，文件恢复到初始内容。",
            "每次更新均经过完整 Reload，而不是只改磁盘文件。",
        ),
    ),
    HaCommandCase(
        name="set_node_write_non_mmr_group",
        summary="SET NODE WRITE 拒绝非 MMR group 且不修改配置",
        source_sections=("控制台命令与持久化实现.md:11.5",),
        executor="set_node_write_non_mmr_group",
        notes=(
            "基础配置同时包含 mmr、replication、balance、single 四种 group_mode。",
            "SET NODE WRITE 指向 rep_group 时返回 is not an MMR group。",
            "错误命令不得修改配置文件。",
        ),
    ),
    HaCommandCase(
        name="four_group_modes_route_visibility",
        summary="MMR、REP、BALANCE、SINGLE 四种 group_mode 的 SHOW GROUP_ROUTING 运行态",
        source_sections=("控制台命令与持久化实现.md:1.3",),
        executor="four_group_modes_route_visibility",
        report_groups=("mmr_group", "rep_group", "balance_group", "single_group"),
        report_all_datasources=True,
        notes=(
            "同一份测试配置定义 mmr_group、rep_group、balance_group、single_group。",
            "每个 group 通过 SHOW GROUP_ROUTING 验证名称、group_mode 与至少一个可用路由候选。",
            "该用例只观察运行态，不产生配置写回。",
        ),
    ),
    HaCommandCase(
        name="mmr_hint_route",
        summary="MMR hint 模式的运行态和真实写路由",
        source_sections=("控制台命令与持久化实现.md:11.1",),
        executor="mmr_hint_route",
        route_mode="hint",
        report_groups=("mmr_group",),
        notes=("SHOW GROUP_ROUTING 与写 Hint 的真实 backend 端口一致。",),
    ),
    HaCommandCase(
        name="replication_route",
        summary="Replication group 的运行态和真实只读路由",
        source_sections=("控制台命令与持久化实现.md:1.3",),
        executor="replication_route",
        topology="replication",
        route_mode="hint",
        report_groups=("rep_group",),
        notes=("只读业务连接落到 replication cluster 的真实成员。",),
    ),
    HaCommandCase(
        name="mmr_hint_read_route",
        summary="MMR hint 模式的真实只读路由",
        source_sections=("sources/router.c:1300",),
        executor="mmr_hint_read_route",
        route_mode="hint",
        report_groups=("mmr_group",),
        notes=("READ ONLY 事务不落到当前 write-leader，并记录真实 backend 端口。",),
    ),
    HaCommandCase(
        name="rep_hint_write_route",
        summary="Replication hint 模式的真实写路由",
        source_sections=("sources/router.c:1300",),
        executor="rep_hint_write_route",
        topology="replication",
        route_mode="hint",
        report_groups=("rep_group",),
        notes=("READ WRITE 事务固定落到 replication primary。",),
    ),
    HaCommandCase(
        name="mmr_port_write_route",
        summary="MMR port 模式 write_port 的真实写路由",
        source_sections=("控制台命令与持久化实现.md:11.1",),
        executor="mmr_port_write_route",
        route_mode="port",
        report_groups=("mmr_group",),
        notes=("write_port 业务连接固定落到当前 MMR write cluster。",),
    ),
    HaCommandCase(
        name="rep_port_write_route",
        summary="Replication port 模式 write_port 的真实主库路由",
        source_sections=("控制台命令与持久化实现.md:1.3",),
        executor="rep_port_write_route",
        topology="replication",
        route_mode="port",
        report_groups=("rep_group",),
        notes=("write_port 业务连接固定落到 replication primary。",),
    ),
    HaCommandCase(
        name="mmr_port_read_route",
        summary="MMR port 模式非 write_port 的真实只读路由",
        source_sections=("sources/router.c:971",),
        executor="mmr_port_read_route",
        route_mode="port",
        report_groups=("mmr_group",),
        notes=("连接非 write_port 后选择 MMR 可读候选并记录真实 backend 端口。",),
    ),
    HaCommandCase(
        name="rep_port_read_route",
        summary="Replication port 模式非 write_port 的真实只读路由",
        source_sections=("sources/router.c:971",),
        executor="rep_port_read_route",
        topology="replication",
        route_mode="port",
        report_groups=("rep_group",),
        notes=("连接非 write_port 后选择 replication 读候选并记录真实 backend 端口。",),
    ),
    HaCommandCase(
        name="mmr_sql_parse_read_write_transactions",
        summary="MMR sql_parse 模式真实读写事务路由",
        source_sections=("sources/frontend.c:1911", "sources/router.c:1300"),
        executor="mmr_sql_parse_read_write_transactions",
        route_mode="sql_parse",
        report_groups=("mmr_group",),
        notes=("分别执行只读 SELECT 和含临时表 DDL 的写事务，验证真实 backend 端口。",),
    ),
    HaCommandCase(
        name="rep_sql_parse_read_write_transactions",
        summary="Replication sql_parse 模式真实读写事务路由",
        source_sections=("sources/frontend.c:1911", "sources/router.c:1300"),
        executor="rep_sql_parse_read_write_transactions",
        topology="replication",
        route_mode="sql_parse",
        report_groups=("rep_group",),
        notes=("分别执行只读 SELECT 和含临时表 DDL 的写事务，验证 replica/primary 路由。",),
    ),
    HaCommandCase(
        name="sql_parse_extended_protocol",
        summary="sql_parse 使用 JDBC PreparedStatement 覆盖扩展协议与失败事务恢复",
        source_sections=("sources/parser/fb_frontend.c", "sources/router.c:1300"),
        executor="sql_parse_extended_protocol",
        route_mode="sql_parse",
        report_groups=("mmr_group",),
        notes=(
            "JDBC 强制 prepareThreshold=1 和 preferQueryMode=extended。",
            "参数化 SELECT 与含 DDL 写事务分别核对真实 backend 端口。",
            "除零错误后分别执行 ROLLBACK 和 COMMIT，随后 PreparedStatement 必须恢复可用。",
        ),
    ),
    HaCommandCase(
        name="balance_route",
        summary="Balance group 的运行态和真实业务路由",
        source_sections=("控制台命令与持久化实现.md:1.3",),
        executor="balance_route",
        topology="balance",
        report_groups=("balance_group",),
        notes=("业务连接落到 balance group 配置的真实候选端口。",),
    ),
    HaCommandCase(
        name="balance_read_only_route",
        summary="Balance read-only 按权重选择 replica，并验证无 replica 回退与 ACTIVE 恢复",
        source_sections=("sources/router.c:balance", "sources/fb_console_command.c:SET NODE"),
        executor="balance_read_only_route",
        topology="balance",
        report_groups=("balance_read_only",),
        notes=(
            "两个真实 replica 中将 pg_3 weight 设为 0，连续连接只允许命中 pg_5。",
            "两个 replica 全部 PARTED 后按源码语义回退 primary，ACTIVE 后恢复 replica 路由。",
            "最终恢复权重和 datasource 状态，配置与初始快照一致。",
        ),
    ),
    HaCommandCase(
        name="ha_command_role_change_route_matrix",
        summary="HA 角色变化由 MMR hint/sql_parse 与 REP port 真实业务路由共同观察",
        source_sections=("sources/router.c:971", "sources/router.c:1300",
                         "sources/fb_console_command.c:SET NODE/SET CLUSTER"),
        executor="ha_command_role_change_route_matrix",
        report_groups=("mmr_group", "mmr_hint_mix", "mmr_sql_mix",
                       "rep_port_mix", "balance_read_mix", "single_write_mix"),
        notes=(
            "批量 WRITE 后分别以 hint 和 sql_parse 发送真实写事务并核对 backend 端口。",
            "PARTED 当前 write cluster 后，两种 MMR 入口均切换到 promoted cluster。",
            "同一命令使 REP port 唯一 backend 不可用，ACTIVE 后真实连接恢复。",
        ),
    ),
    HaCommandCase(
        name="single_route",
        summary="Single group 的运行态和固定后端路由",
        source_sections=("控制台命令与持久化实现.md:1.3",),
        executor="single_route",
        topology="single",
        report_groups=("single_group",),
        notes=("业务连接固定落到 single group 的唯一主端口。",),
    ),
    HaCommandCase(
        name="set_node_promoted_write_cluster_conflict",
        summary="SET NODE PROMOTED 对当前写 cluster 幂等跳过",
        source_sections=("控制台命令与持久化实现.md:13.2",),
        executor="set_node_promoted_write_cluster_conflict",
        notes=(
            "目标 pg_2 属于当前 write cluster pg_cluster_2。",
            "命令返回 NO CONFIG CHANGE，不执行运行态健康校验。",
            "幂等命令不修改配置文件。",
        ),
    ),
    HaCommandCase(
        name="missing_promoted_set_write",
        summary="MMR 缺失 promoted_cluster 时 SET NODE WRITE 只切换写中心",
        source_sections=("控制台命令与持久化实现.md:11.3",),
        executor="missing_promoted_set_write",
        notes=(
            "命令前 SHOW 明确 promoted 为空。",
            "SET NODE WRITE 只修改 write_cluster，不自动新增 promoted_cluster。",
            "Reload 后 SHOW 与写 Hint 实际路由均指向新写中心。",
        ),
    ),
    HaCommandCase(
        name="missing_promoted_set_promoted",
        summary="MMR 缺失 promoted_cluster 时 SET NODE PROMOTED 补写字段",
        source_sections=("控制台命令与持久化实现.md:11.3",),
        executor="missing_promoted_set_promoted",
        notes=(
            "命令前 SHOW 明确 promoted 为空。",
            "SET NODE PROMOTED 在原 block 中新增 promoted_cluster。",
            "Reload 后 promoted 生效且原 write_cluster 的写 Hint 路由不变。",
        ),
    ),
    HaCommandCase(
        name="batch_weight_atomicity",
        summary="SET NODE WEIGHT 批量修改、恢复与无效目标原子拒绝",
        source_sections=("控制台命令与持久化实现.md:10.4",),
        executor="batch_weight_atomicity",
        notes=(
            "批量命令同时修改 pg_3、pg_4，并通过配置和 SHOW 验证。",
            "反向命令恢复初始配置。",
            "混入不存在 datasource 时整条命令拒绝且配置无变化。",
        ),
    ),
    HaCommandCase(
        name="include_rejected_after_start",
        summary="配置含 include 时拒绝高可用写命令并保持原文件",
        source_sections=("sources/fb_config_writer.c:1304",),
        executor="include_rejected_after_start",
        notes=("运行中注入 include，验证命令前后 SHOW、文件和备份均不变。",),
    ),
    HaCommandCase(
        name="duplicate_object_rejected_after_start",
        summary="配置含同名 datasource 时拒绝高可用写命令",
        source_sections=("sources/fb_config_writer.c:1414",),
        executor="duplicate_object_rejected_after_start",
        notes=("运行中注入同名 datasource，验证运行态和持久化均保持不变。",),
    ),
    HaCommandCase(
        name="application_name_persistence",
        summary="高可用写回保持 application_name 显式值与默认缺省状态",
        source_sections=("sources/fb_config_writer.c:580",),
        executor="application_name_persistence",
        notes=("同时修改显式和缺省 application_name 节点，验证前后配置、运行态与备份。",),
    ),
    HaCommandCase(
        name="group_defaults_persistence",
        summary="高可用写回保持 Balance/Single 的 access_mode 缺省状态",
        source_sections=("sources/fb_config_writer.c:680",),
        executor="group_defaults_persistence",
        report_groups=("rep_group", "balance_group", "single_group"),
        notes=("确认 access_mode 默认值生效，并验证权重写回不物化省略字段；check auto group 保留可连接 storage_db。",),
    ),
    HaCommandCase(
        name="single_read_only_persistence",
        summary="Single read_only 路由下高可用写回保持只读语义",
        source_sections=("sources/fb_console_command.c:1470",),
        executor="single_read_only_persistence",
        topology="single",
        report_groups=("single_group",),
        notes=("验证备库读路由、业务写拒绝、权重持久化和两次正确备份。",),
    ),
    HaCommandCase(
        name="bulk_30_group_write_roundtrip",
        summary="IN GROUPS 原子切换并恢复 30 个 MMR group",
        source_sections=("sources/fb_console_command.c:1103",),
        executor="bulk_30_group_write_roundtrip",
        notes=("逐组验证 30 个配置对象，控制台对比首尾 group，并校验两次备份。",),
    ),
    HaCommandCase(
        name="default_group_expansion_34",
        summary="省略 IN GROUPS 时自动展开并恢复 34 个 MMR group",
        source_sections=("sources/fb_console_command.c:1113",),
        executor="default_group_expansion_34",
        notes=("验证默认范围覆盖 34 个 group、控制台状态、配置 diff 和两次备份。",),
    ),
    HaCommandCase(
        name="bulk_30_groups_invalid_target",
        summary="30 个合法 MMR group 混入不存在目标时原子拒绝",
        source_sections=("sources/fb_console_command.c:1113",),
        executor="bulk_30_groups_invalid_target",
        notes=("验证无备份、无 diff、30 个 group 无部分修改及前后 SHOW。",),
    ),
    HaCommandCase(
        name="bulk_30_groups_non_mmr",
        summary="30 个 MMR group 混入 REP group 时原子拒绝",
        source_sections=("sources/fb_console_command.c:1031",),
        executor="bulk_30_groups_non_mmr",
        report_groups=("rep_group",),
        notes=("验证非 MMR 目标错误、无备份、30 个 MMR 与 REP 前后状态不变。",),
    ),
    HaCommandCase(
        name="set_cluster_30_datasource_roundtrip",
        summary="SET CLUSTER 批量展开并恢复 30 个 datasource",
        source_sections=("sources/fb_console_command.c:1335",),
        executor="set_cluster_30_datasource_roundtrip",
        report_all_datasources=True,
        notes=("验证 cluster PARTED/ACTIVE 展开 30 个节点、首尾 SHOW、diff 和备份。",),
    ),
    HaCommandCase(
        name="bulk_30_datasource_weight_roundtrip",
        summary="SET NODE WEIGHT 原子修改并恢复 30 个 datasource",
        source_sections=("sources/fb_console_command.c:604",),
        executor="bulk_30_datasource_weight_roundtrip",
        report_all_datasources=True,
        notes=("逐节点验证 30 个配置对象，控制台对比首尾节点，并校验两次备份。",),
    ),
    HaCommandCase(
        name="candidate_validation_rejected",
        summary="候选完整配置校验失败时不提交、不备份且运行态不变",
        source_sections=("sources/fb_config_writer.c:1531",),
        executor="candidate_validation_rejected",
        notes=("运行中注入未知顶层参数，验证候选校验错误、文件、备份和临时文件。",),
    ),
    HaCommandCase(
        name="reload_failure_rollback",
        summary="复测 single_group 唯一只读副本 pg_3 的 PARTED/ACTIVE 往返",
        source_sections=("sources/fb_console_command.c:850",),
        executor="reload_failure_rollback",
        topology="single",
        report_groups=("single_group",),
        notes=("当前代码允许 single read_only 暂时没有 active replica，验证命令、备份和运行态。",),
    ),
    HaCommandCase(
        name="file_metadata_preservation",
        summary="高可用写回保持正式配置和备份的权限及属主",
        source_sections=("sources/fb_config_writer.c:1590",),
        executor="file_metadata_preservation",
        notes=("以 0640 配置执行修改和恢复，验证文件、备份、运行态及内容。",),
    ),
    HaCommandCase(
        name="backup_symlink_rejected",
        summary="conf-backup 为符号链接时安全拒绝高可用写回",
        source_sections=("sources/fb_config_writer.c:1114",),
        executor="backup_symlink_rejected",
        notes=("验证不跟随备份目录符号链接，目标目录、正式配置和运行态均不变。",),
    ),
    HaCommandCase(
        name="stable_lock_contention",
        summary="稳定锁被占用时限时拒绝且释放后可正常写回",
        source_sections=("sources/fb_config_writer.c:1238",),
        executor="stable_lock_contention",
        notes=("验证锁冲突等待上限、无备份、前后 SHOW，以及释放后的修改和恢复。",),
    ),
    HaCommandCase(
        name="stable_lock_symlink_rejected",
        summary="稳定锁为符号链接时安全拒绝且不修改链接目标",
        source_sections=("sources/fb_config_writer.c:1238",),
        executor="stable_lock_symlink_rejected",
        notes=("验证锁链接拒绝、目标不变、无备份及恢复普通锁后的正常写回。",),
    ),
    HaCommandCase(
        name="backup_directory_permissions",
        summary="conf-backup 可被其他用户写入时拒绝并可在修复后恢复写回",
        source_sections=("sources/fb_config_writer.c:1114",),
        executor="backup_directory_permissions",
        notes=("验证 0777 目录拒绝、无备份、前后 SHOW，以及改为 0700 后修改和恢复。",),
    ),
    HaCommandCase(
        name="stable_lock_permissions",
        summary="稳定锁可被其他用户写入时拒绝并可在修复后恢复写回",
        source_sections=("sources/fb_config_writer.c:1238",),
        executor="stable_lock_permissions",
        notes=("验证 0666 锁拒绝、无备份、前后 SHOW，以及恢复 0600 后修改和恢复。",),
    ),
    HaCommandCase(
        name="stable_lock_directory_rejected",
        summary="稳定锁为目录时拒绝并可在恢复普通文件后正常写回",
        source_sections=("sources/fb_config_writer.c:1238",),
        executor="stable_lock_directory_rejected",
        notes=("验证锁目录类型拒绝、无备份、前后 SHOW，以及恢复普通锁后的修改和恢复。",),
    ),
    HaCommandCase(
        name="backup_path_regular_file_rejected",
        summary="conf-backup 为普通文件时拒绝且不替换占位文件",
        source_sections=("sources/fb_config_writer.c:1114",),
        executor="backup_path_regular_file_rejected",
        notes=("验证普通文件拒绝、文件内容和运行态不变，以及恢复安全目录后的修改和恢复。",),
    ),
    HaCommandCase(
        name="config_backup_dir",
        summary="config_backup_dir 显式目录生效、Reload 切换/拒绝/回落默认及运行期目录失效",
        source_sections=(
            "sources/config_writer/fb_config_writer.c:fb_config_writer_prepare_paths",
            "sources/config_writer/fb_config_writer.c:fb_config_writer_validate_backup_dir",
            "sources/config.c:od_config_reload",
            "sources/system.c:fb_reload_config_equal",
        ),
        executor="config_backup_dir",
        notes=(
            "启动时配置 config_backup_dir 指向显式目录：目录自动创建为 0700，"
            "备份文件落显式目录且权限属主继承正式配置，默认 conf-backup 不产生备份。",
            "修改配置文件将 config_backup_dir 指向第二个目录后 RELOAD："
            "后续写配置命令的备份落新目录，旧目录不再新增。",
            "将 config_backup_dir 改为普通文件后 RELOAD：Reload 被拒绝，"
            "运行态继续使用 Reload 前的目录，后续命令备份位置不变。",
            "删除 config_backup_dir 配置项后 RELOAD：备份回落到配置文件旁的 "
            "conf-backup 默认目录。",
            "运行期将生效中的备份目录改为只读：写配置命令在备份阶段被拒绝，"
            "配置文件逐字节不变、无临时文件和错误备份残留，恢复权限后写回正常。",
            "最后恢复初始配置并 Reload，再用非法目录验证启动期校验拒绝启动。",
        ),
    ),
    HaCommandCase(
        name="readonly_config_directory",
        summary="配置父目录不可写时拒绝且恢复权限后可正常写回",
        source_sections=("sources/fb_config_writer.c:1531",),
        executor="readonly_config_directory",
        notes=("验证候选创建失败、无备份/临时文件、前后 SHOW，以及权限恢复后的写回。",),
    ),
    HaCommandCase(
        name="readonly_config_file",
        summary="配置文件为 0444 时拒绝且恢复权限后可正常写回",
        source_sections=("sources/fb_config_writer.c:1234",),
        executor="readonly_config_file",
        notes=("验证不可写文件拒绝、无备份、前后 SHOW，以及恢复 0640 后修改和恢复。",),
    ),
    HaCommandCase(
        name="rename_failure_protection",
        summary="候选 renameat 失败时保留正式配置并清理临时文件",
        source_sections=("sources/fb_config_writer.c:1602",),
        executor="rename_failure_protection",
        notes=("以路径限定 fault hook 注入 EIO，验证备份、文件、临时文件及前后 SHOW。",),
    ),
    HaCommandCase(
        name="external_edit_conflict",
        summary="提交期间外部编辑时拒绝覆盖并保留外部修改",
        source_sections=("sources/fb_config_writer.c:1591",),
        executor="external_edit_conflict",
        notes=("以路径限定 hook 在首次提交前注入注释，验证无备份、外部 diff 和运行态。",),
    ),
    HaCommandCase(
        name="reload_restore_failure",
        summary="[用例不完整] 当前仅验证候选污染后的结构化发布保护，尚未真正触发 Reload/restore 双重失败",
        source_sections=("sources/fb_console_command.c:fb_console_commit_writer",
                         "sources/fb_config_writer.c:fb_config_writer_restore_old"),
        executor="reload_restore_failure",
        notes=(
            "路径限定 LD_PRELOAD hook 只污染当前 case 已校验的候选配置。",
            "当前尚未验证真实 Reload 失败、restore 失败及 fbasecman 错误日志输出。",
            "验证运行态未部分发布，随后恢复初始文件并 Reload，避免遗留脏状态。",
        ),
    ),
)

# These are router-only probes.  They do not execute SET NODE, SET CLUSTER,
# or REFRESH CLUSTER, so they do not belong to the HA command gate.
_ROUTE_ONLY_CASES = frozenset((
    "four_group_modes_route_visibility",
    "mmr_hint_route",
    "replication_route",
    "mmr_hint_read_route",
    "rep_hint_write_route",
    "mmr_port_write_route",
    "rep_port_write_route",
    "mmr_port_read_route",
    "rep_port_read_route",
    "mmr_sql_parse_read_write_transactions",
    "rep_sql_parse_read_write_transactions",
    "sql_parse_extended_protocol",
    "balance_route",
    "balance_read_only_route",
    "single_route",
))
HA_COMMAND_CASES = tuple(case for case in HA_COMMAND_CASES
                         if case.name not in _ROUTE_ONLY_CASES)


def case_items():
    return [case for case in HA_COMMAND_CASES if case.enabled]


def find_case(name):
    for case in HA_COMMAND_CASES:
        if case.name == name:
            return case
    raise KeyError(name)


def validate_manifest():
    seen = set()
    for case in HA_COMMAND_CASES:
        if case.name in seen:
            raise ValueError("duplicate HA command case: %s" % case.name)
        seen.add(case.name)
        if not case.source_sections:
            raise ValueError("%s has no source section" % case.target)
        if not case.notes:
            raise ValueError("%s has no document coverage" % case.target)
    return True
