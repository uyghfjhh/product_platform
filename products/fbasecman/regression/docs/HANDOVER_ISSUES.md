# Handover 代码问题

本文件仅记录已由转测步骤、实际复现输出和 fbasecman 源码共同确认的产品代码缺陷。
转测文档本身的错误统一记录在 `HANDOVER_DOCUMENT_ISSUES.md`。

## 汇总

- 已确认代码问题总数：2
- `[LONG-TIME]` 用例总数：6

## H-001: 带目标的 `RESET <type> <target>;` 被 console 拒绝

- 引入提交：
  - Commit：`085f3b76c546adbe76412ec53092bc2f21424b12`
  - Author：`eileen.wu <eileen.wu@fexbase.com>`
  - AuthorDate：`2025-06-26T10:20:47+08:00`
  - Committer：`eileen.wu <eileen.wu@fexbase.com>`
  - CommitDate：`2025-06-26T10:20:47+08:00`
  - 提交信息：`1、新增线程、客户端、服务端、连接池的统计信息以及统计信息重置命令 2、上传统计信息测试文档和技术文档`
- 引入结论：该提交首次加入
  `FB_RESET` 命令解析、`fb_reset_info()` 和 `fb_reset_by_type()`，问题从功能首次实现时
  即存在；不是后续配置文件重构引入。
- 文档位置：`fbasecman转测文档.md` 10.2.5.1。
- 复现：在管理员 console 连接执行 `RESET ALL SERVERS;`、`RESET REQUEST CLIENTS;` 等文档定义的带目标命令。
- 预期：均返回 `RESET SUCCESS`，只清零目标统计项。
- 实际：当前重新编译的二进制返回 `ERROR: fbasecman: ... console command error: RESET ...;`；2026-07-19 已实际复现 `RESET ALL SERVERS;` 与 `RESET REQUEST CLIENTS;`。
- 对照：`sources/console.c:fb_reset_by_type()` 对 `servers`、`clients`、`pools`、
  `thread` 都有明确分支，`fb_reset_info()` 在其返回成功时生成 `RESET SUCCESS`。实际
  console 与这些分支声明的能力不一致。无目标 `RESET REQUEST;` 可作为临时回退，但
  不满足文档规定的定向清零行为。
- Git 依据：`git show 085f3b76 -- sources/console.c` 显示目标关键字、目标分支和
  console 入口在同一提交中一次性新增；`git log -S 'fb_reset_by_type'` 未发现更早实现。

## H-002: MMR read port 排除 promoted non-write-leader 并回退 write leader

- 引入提交：
  - Commit：`92836e06c738c31335152d0ac83089a521dc8af4`
  - Author：`xinkun <adi.xin@fexbase.com>`
  - AuthorDate：`2026-08-21T14:48:10+08:00`
  - Committer：`xinkun <adi.xin@fexbase.com>`
  - CommitDate：`2026-08-21T14:48:10+08:00`
  - 提交信息：`配置文件重构`
- 引入结论：该提交新增 route snapshot 候选选择和
  `fb_route_candidate_is_available()`，并首次把所有 replica candidate 统一限制为
  `node->state == FB_NODE_STATE_ACTIVE`。
- 文档位置：`fbasecman转测文档.md` 5.3 测试二。原文明确非 `write_port`
  应按权重从 replica 或 non-write-leader 中选择读节点，并要求命中
  non-write-leader 时可以执行读写。
- 稳定复现：`handover.mmr_port_read` 将两个 replica 配置为 `parted`，使
  `pg_230` 成为唯一剩余读候选。启动日志确认路由快照包含
  `active rule (pg_230... role:non-write-leader)`，但连接 read port 后实际命中
  `pg_220`（backend port `10011`，write leader），而不是 `pg_230`（`10021`）。
- 预期：read port 命中唯一可用的 non-write-leader `pg_230`，连接保持在
  `10021`，DML 成功。
- 实际：read port 回退 write leader `pg_220/10011`。这会使 read-port 流量进入
  写中心，违反文档的端口分流合同。
- 源码依据：`sources/fb_group.c:fb_set_rule_master_members()` 将 promoted
  non-write-leader 加入 `replica_candidates`；`sources/router.c` 的 port 分支随后
  调用 `pick_groupmember()`。但 `sources/fb_group.c:fb_route_candidate_is_available()`
  只接受 `node->state == FB_NODE_STATE_ACTIVE`，而 promoted non-write-leader 的
  正常状态是 `FB_NODE_STATE_PROMOTED`。因此该候选必然被过滤，
  `fb_pick_rule_for_port_methond()` 再回退 `write_rule`。
- Git 依据：父提交中的 `pick_groupmember()` 直接遍历 `rule_members`，仅排除
  `last_connect_failed`，没有 `state == ACTIVE` 限制；`git diff 92836e06^ 92836e06 --
  sources/fb_group.c sources/router.c` 显示上述统一过滤条件由 `92836e06` 新增。因此
  更早已有的 read-port 选路和空候选回退逻辑不是本次回归的直接引入点。
- 修复方向：候选可用性判断需要按候选角色接受合法状态；至少对
  non-write-leader/promoted 读候选接受 `FB_NODE_STATE_PROMOTED`，同时继续执行
  `last_connect_failed`、weight 和 cluster topology 校验。不能简单允许所有非
  `PARTED` 状态。

## [LONG-TIME] 用例

| 用例 | 文档章节 | 默认执行 | 原因 | 状态 |
|---|---|---|---|---|
| `handover.console_server_lifecycle_statistics` | 10.2.1.2 | 否 | 原文要求两条 1800 秒 workload，含长、短连接 | 已实现，待单独执行 |
| `handover.console_client_statistics` | 10.2.2.1 至 10.2.2.2 | 否 | 原文要求 5 client、1800 秒 workload | 已实现，待单独执行 |
| `handover.console_pgbench_mmr_hint_statistics` | 10.2.1.1/mmrhint | 否 | 原文要求一小时可靠性压测 | 已实现，待单独执行 |
| `handover.console_pgbench_mmr_port_statistics` | 10.2.1.1/mmrport | 否 | 原文要求一小时可靠性压测 | 已实现，待单独执行 |
| `handover.console_pgbench_rep_hint_statistics` | 10.2.1.1/rephint | 否 | 原文要求一小时可靠性压测 | 已实现，待单独执行 |
| `handover.console_pgbench_balance_statistics` | 10.2.1.1/balance | 否 | 原文要求一小时可靠性压测 | 已实现，待单独执行 |

环境不可用、前置配置缺失、测试脚本错误、无法复现的现象，以及仅凭日志猜测的结论均不记录在此。发现失败时先在 `HANDOVER_PROGRESS.md` 记录复核状态；只有确认是产品代码问题后才新增本文件条目，并写明：文档章节、复现 SQL/命令、预期、实际、相关日志和源码函数。
