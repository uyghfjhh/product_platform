# Fbasecman 回归确认问题

本文件记录整套回归中经实际复现、日志和源码共同确认的 fbasecman 产品缺陷。
测试代码或环境问题修复后不在此保留。

## 汇总

- 已确认产品代码问题：2

## F-001: `DISCARD ALL` 与 GUC 同步 SQL 合并下发导致 `25001`

- 引入提交：
  - Commit：`bd66946063f5d6807aff9d38bf91dd7ad45b2e31`
  - Author：`xinkun <adi.xin@fexbase.com>`
  - AuthorDate：`2025-10-29T10:23:09-06:00`
  - Committer：`xinkun <adi.xin@fexbase.com>`
  - CommitDate：`2025-10-29T10:32:39+08:00`
  - 提交信息：`优化GUC同步功能：1.宏全部用FB开头，删除重复的宏；2.修复生成RESET报文的BUG；3.清除缓存时，判断数量，避免多余的遍历；4.优化转义逻辑；5.增加enable_guc_sync配置项，控制是否启用GUC同步功能。默认开启。可以全局和用户级别配置，优先用户级别生效`
- 引入结论：该提交把 `fb_guc_cache_sync()` 在生成 `RESET ALL` 或
  `DISCARD ALL` 后的直接 `return` 改为继续执行前后端缓存差异同步，使后续 `SET` 能够
  追加到同一个 buffer；`od_deploy()` 随后仍通过一次 `kiwi_fe_write_query()` 下发整个
  buffer，由此直接形成当前失败所需的合并 SQL。
- 复现用例：`global_cache.discard_all_clears_backend_cache`。
- 配置：事务池，`enable_guc_sync yes`，`pool_discard no`，保留后端
  PreparedStatement cache。
- 步骤：JDBC PreparedStatement 首次执行成功；执行 `DISCARD ALL`；再次执行
  同一 PreparedStatement 触发后端 attach 和重新部署。
- 预期：`DISCARD ALL` 独立下发并清空后端 GUC/PreparedStatement cache；随后
  GUC `SET` 同步和 PreparedStatement 重新部署成功。
- 实际：fbasecman 将以下内容作为一条 Simple Query 下发：

  ```sql
  DISCARD ALL;
  SET client_encoding=E'UTF8';
  SET session_authorization=E'postgres';
  SET application_name=E'PostgreSQL JDBC Driver';
  ...
  ```

  PostgreSQL 返回 `ERROR 25001: DISCARD ALL cannot run inside a transaction block`；
  后续重新部署又返回 `ERROR 42P05: prepared statement "__fbasecman_1"
  already exists`。
- 日志证据：
  `output/runs/global_cache/discard_all_clears_backend_cache/fbasecman.log` 中先出现
  `responded DISCARD ALL command to client`，随后出现合并后的 `deploy:
  DISCARD ALL;SET ...`、`ERROR 25001` 和 `ERROR 42P05`。
- 源码依据：`sources/fb_guc_cache.c` 的 `fb_guc_cache_sync()` 先调用
  `fb_guc_sync_generate_discard_all()` 将 `DISCARD ALL;` 写入同步 buffer，随后
  继续由 `fb_guc_sync_process_frontend()` 追加 `SET`；`sources/deploy.c` 的
  `od_deploy()` 将整个 buffer 通过一次 `kiwi_fe_write_query()` 发给后端。
- Git 依据：`git show bd669460 -- sources/fb_guc_cache.c` 明确显示
  `return fb_guc_sync_generate_discard_all(...)` 被改为先更新 `pos`、再继续执行
  `fb_guc_sync_process_frontend()`；更早的 `f27a9912` 首次实现 GUC 同步和
  `DISCARD ALL`，但当时清理命令生成后立即返回，不具备本问题的合并路径。
- 修复方向：`DISCARD ALL` 必须作为独立后端 Query 执行并等待成功，再发送 GUC
  同步 SQL；不能与其他语句放在同一个 PostgreSQL Simple Query 字符串中。

## F-002: Simple Query `DISCARD ALL` 未入队追踪队列导致连接异常断开

- 引入提交：
  - Commit：`ba92e26bb1d6a7ce32f8eb4f92be32fdbd21e8a6`
  - Author：`cong.xie <cong.xie@fexbase.com>`
  - AuthorDate：`2026-08-20`
  - 提交信息：`feat: 补充sql解析日志...`
- 引入结论：该提交在 `sources/router.c` 的 `fb_sync_route()` 中处理 Simple Query 的
  `DISCARD ALL` 时，清空了缓存但未将该请求加入客户端的 `outstanding_queue`。
  随后在 `fb_route_backend_query()` 处理后端响应时，执行
  `fb_outstanding_queue_dequeue(&client->outstanding_queue)` 返回 `NULL`，直接触发
  `fb_trace_error("failed to get client query")` 并主动关闭客户端 Socket 连接。
- 复现用例：`guc.discard_all_sql_parse`、`guc.discard_all_hint`。
- 配置：事务池，分别在 `sql_parse` 和 `hint` 模式下启用 GUC 同步。
- 步骤：客户端发送 Simple Query `DISCARD ALL`。
- 预期：fbasecman 清理客户端 GUC 缓存，正确处理请求流并维持客户端连接可用。
- 实际：客户端收到断连，测试返回 `psql: error: connection to server was lost`；
  fbasecman 日志报错：`fb_route_backend_query(): failed to get client query`。
- 源码依据：`sources/router.c` 中 `fb_sync_route()` 分支处理 `DISCARD ALL` 时未调用
  `fb_outstanding_queue_enqueue()` 将查询上下文入队。
- 修复方向：在 Simple Query 识别并处理 `DISCARD ALL` 的路由分支中，确保将查询节点正确
  推入 `client->outstanding_queue`，保证后端响应处理时队列状态对称。

