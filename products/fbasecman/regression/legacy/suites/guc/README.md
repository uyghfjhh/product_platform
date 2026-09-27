# GUC suite

本 suite 专门验证 fbasecman 的 GUC 参数感知、同步、`search_path` 复用及 `DISCARD` 行为。
包含 18 条正式用例，完整覆盖 `SQL_PARSE` 和 `HINT` 两种路由解析模式。

## 目录结构

```text
suites/guc/
├── case.py              用例数据模型（定义模式与关键参数）
├── manifest.py          18 条用例清单及元数据
├── suite.py             套件编排与 runner 注册
├── runtime.py           运行期配置、生命周期与环境上下文
├── executors.py         用例执行体与断言逻辑（含步骤 1 配置作证）
└── README.md            本说明文档
```

## 测试模式划分

为保证双模式等价覆盖，核心场景均在 `SQL_PARSE` 和 `HINT` 下分别建立对应测试：

| 测试场景 | SQL_PARSE 模式用例 | HINT 模式用例 | 核心验证点 |
|---|---|---|---|
| search_path 事务内复用 | `search_path_reuse_sql_parse` | `search_path_reuse_hint` | 验证多事务交替执行时，相同 `search_path` 复用后端连接，不同路径时触发正确同步 |
| search_path 重复设置 | `search_path_duplicate_sql_parse` | `search_path_duplicate_hint` | 连续 `SET search_path` 同值时不产生冗余下发 |
| search_path 跨事务隔离 | `search_path_isolation_sql_parse` | `search_path_isolation_hint` | 验证不同客户端会话间的 GUC 互不污染与脏数据隔离 |
| search_path 重置 | `search_path_reset_sql_parse` | `search_path_reset_hint` | 执行 `RESET search_path` 恢复默认搜索路径 |
| 混合 GUC 变更 | `mixed_guc_change_sql_parse` | `mixed_guc_change_hint` | 同时变更 `DateStyle`、`TimeZone`、`search_path` 的组合下发 |
| 事务回滚状态保持 | `transaction_rollback_sql_parse` | `transaction_rollback_hint` | 事务 `ROLLBACK` 后客户端与服务端 GUC 状态的一致性 |
| DISCARD ALL 行为 | `discard_all_sql_parse` | `discard_all_hint` | `DISCARD ALL` 清理缓存及后续连接健康度 |
| enable_guc_sync 禁用 | `guc_sync_disabled_sql_parse` | `guc_sync_disabled_hint` | 显式配置 `enable_guc_sync no` 时的穿透行为 |
| 驱动级参数覆盖 | `driver_level_guc_sql_parse` | `driver_level_guc_hint` | 客户端连接参数与 SQL 动态设置的优先级与覆盖 |

## 报告规范与配置作证

根据回归框架要求，GUC 套件的所有测试报告（`report.txt`）均满足：

1. **步骤 1 显式配置作证**：
   每个用例的第一步均从实际生成的 `fbasecman.conf` 中截取关键配置片段（如 `parse_mode`、`enable_guc_sync`、`pool_discard` 等），
   作为测试模式的权威依据。
2. **测试模式明确标注**：
   在测试用例的 `验证目的`、`测试内容及步骤对应关系` 中明确声明当前运行模式（`[SQL_PARSE 模式]` 或 `[HINT 模式]`）。
3. **关键状态与日志断言**：
   严格断言客户端连接状态、`fbasecman.log` 跟踪日志和 PostgreSQL 后端查询日志。

## 执行命令

```bash
# 查看 GUC 套件所有用例
./run.sh show guc

# 运行整个 GUC 套件
./run.sh run guc

# 运行单条用例
./run.sh run guc.search_path_reuse_sql_parse
./run.sh run guc.search_path_reuse_hint
```
