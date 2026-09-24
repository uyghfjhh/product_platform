# Handover 文档问题

本文件仅记录已由原始转测文档、当前产品行为和源码共同确认的文档错误，例如错误命令、错误预期或错误时序。

## 汇总

- 已确认文档问题总数：2

产品代码 bug 不在本文件记录，统一写入 `HANDOVER_ISSUES.md`。环境缺失、测试脚本错误、无法复现的现象也不记录为文档问题。
## D-001: 8.4 全部 MMR 主节点故障后的 replication 降级方案已失效

- 文档位置：8.4.2、8.4.3。
- 原步骤：将原 MMR group 动态改成 replication group，以 `pg_240` 继续提供只读。
- 新配置模型：提交 `2a1da0f820dcf241743bf720c587654dd82f9398` 删除
  `read_datasource_names`、`parted_datasource_names` 等字段；reload 明确禁止修改
  既有 MMR group 的 `group_mode` 和 `backend_clusters`。
- 实际：将两个 MMR 主节点 datasource 设置为 `status "parted"` 后，默认
  读写连接均因没有 active write node 被拒绝。若按原文把 group 改为
  `replication`、将 `backend_clusters` 缩为数据完整的 `mmr_cluster_1`，再执行
  `RELOAD`，当前源码在发布前拒绝该变更：控制台返回 `reload contract violation`，
  同一日志窗口记录 `reload cannot change existing MMR group postgres group_mode`。
  停止并重新启动 fbasecman 也不能实现旧文档目标：主节点已经停止时，topology checker
  无法从 `pg_240` 的 `pg_stat_wal_receiver` 得到 `conninfo`，日志记录
  `standby datasource pg_240 cannot expose pg_stat_wal_receiver.conninfo` 和
  `failed to groups check`，进程拒绝完成启动。
- 源码依据：`sources/rules.c` 的 reload 兼容性检查明确拒绝既有 MMR group 的
  `group_mode` 与 `backend_clusters` 改动；同文件的 group 组装逻辑要求
  replication group 只引用一个 backend cluster 且不能保留 `write_cluster` /
  `promoted_cluster`。
- 结论：文档的“修改后 RELOAD”步骤和“重启加载等价 replication 配置”的替代方案在
  当前产品中均不成立，无法按旧时序让 `pg_240` 单独提供 `BEGIN READ ONLY`。自动化先
  验证默认路由拒绝，再分别验证 RELOAD 合同拒绝和重启时 group checker 拒绝，以
  `SUCCESS` 表示已准确复现并确认文档失效；文档需要为当前 cluster/topology checker
  模型重新设计可执行的降级方案和预期。

## D-002: 11.3 attach 优化的 `check "none"` 空缓存前置不再适用于新配置模型

- 文档位置：11.3.1、11.3.2.1、11.3.3.1、11.3.4.1。
- 原步骤：MMR group 配置 `check "none"`，启动后 `SHOW SERVERS` 为 `(0 rows)`，
  再用读写标签、heartbeat 和 GUC 验证不创建后端缓存。
- 新配置模型：提交 `2a1da0f820dcf241743bf720c587654dd82f9398` 改由
  `backend_clusters`、`write_cluster` 和 `promoted_cluster` 描述 MMR 拓扑。
  datasource 的 `status "active"` 只设置节点状态；节点的
  `WRITE_LEADER`、`NON_WRITE_LEADER`、`REPLICA` 角色仍由 topology checker
  根据实际 PostgreSQL 拓扑建立。
- 实际：使用 `check "none"` 时 `SHOW SERVERS` 虽为 `(0 rows)`，但客户端在发送
  可拦截 SQL 前已被路由层拒绝，错误为 `group(postgres) has no any active write node`；
  使用 `check "auto"` 时业务可连接，但启动检查会在 pool 中保留四条 checker 后端。
- 源码依据：`sources/rules.c` 的 `fb_group_add_cluster()` 初始化 group role 为
  `REPLICA`；`sources/router.c` 的 hint 初始路由要求已识别的 active write/promoted
  节点；`sources/fb_group.c` 的 topology checker 负责识别并写入物理角色。
- 结论：重构后无法同时满足旧文档的 `check "none"`、`SHOW SERVERS (0 rows)` 和
  客户端可连接三个条件。自动化改为以 checker 连接集合为基线，验证三类本地拦截
  请求执行后没有新增任何后端连接；文档应同步更新测试前置和预期。
