# global_cache suite

本 suite 验证 fbasecman global prepared statement cache 的正式产品契约。默认 gate
包含 18 条用例；普通 JDBC 压力、框架自测、COPY、大包和纯路由行为不属于本 suite。

## 代码结构

```text
suites/global_cache/
├── manifest.py             18 条正式用例和关键参数
├── suite.py                场景编排、executor registry 和部分行为断言
├── runtime.py              运行目录、产品进程、console/PG 日志窗口和产物策略
├── drivers.py              JDBC/libpq 资产编译、执行和源码 API/SQL 提取
├── state.py                三条 console SHOW 的统一快照模型
├── waits.py                entry 消失、ref_count 归零等等待能力
├── reporting.py            日志窗口和结构化报告入口
├── result.py               verification check 结果装配
├── domains/
│   ├── capacity*.py        容量、引用保护、淘汰和 reload shrink
│   └── guc*.py             GUC sequence、reload 和断言
├── reports/
│   ├── documents.py        通用及 heartbeat/reload 文档
│   ├── capacity.py         容量类特殊文档
│   ├── helpers.py          entry/stats record 格式
│   └── runtime.py          运行步骤过滤和 report 写入
└── assets/
    ├── config/             MMR/REP fbasecman 配置模板
    ├── jdbc/               外置 JDBC driver
    └── libpq/              外置 libpq driver
```

## 两层 prepared statement cache

### Global cache

global cache 属于 fbasecman 进程级缓存，多个客户端和多个 PostgreSQL backend 连接
可以引用同一个 global entry。

控制台入口：

```sql
SHOW GLOBAL_PREPARED_STATEMENTS;
SHOW GLOBAL_PREPARED_STATEMENTS_STATS;
```

entry 字段：

| 字段 | 含义 |
| --- | --- |
| `global_name` | fbasecman 分配的全局 statement 名称 |
| `description` | SQL 和参数类型共同形成的描述 |
| `sql_class` | `NORMAL`、`HEARTBEAT`、GUC 等分类 |
| `has_bypass_response` | 是否缓存了可直接返回客户端的响应 |
| `ref_count` | client、backend cache 等对该 global entry 的引用数 |

统计字段：

| 字段 | 含义 |
| --- | --- |
| `total_entries` | 当前 global entry 数量 |
| `referenced_entries` | 当前 `ref_count > 0` 的条目数 |
| `unreferenced_entries` | 当前 `ref_count = 0` 的条目数 |
| `bypass_entries` | 携带 bypass response 的条目数 |
| `capacity` | 当前配置生效的 global cache 容量上限 |
| `hits` / `misses` | global cache 命中/创建计数 |
| `evictions` | global cache 淘汰计数 |

`global_prepared_statements_limit=4` 表示配置容量上限是 4 条，不表示当前已经有
4 条；当前数量必须看 `total_entries`。

### Backend cache

每个 PostgreSQL backend 连接有自己的 server prepared statement cache。它记录当前
连接已向 PostgreSQL 部署的 prepared statement，控制台入口是：

```sql
SHOW SERVER_PREP_STMTS;
```

`backend_prepared_statements_limit=2` 表示每个 backend 连接最多保留 2 条 server
prepared statement，不表示整个 fbasecman 当前只有 2 条。

`SHOW SERVER_PREP_STMTS.refcount` 是 server statement 的复用计数，不是 global
entry 的 `ref_count`，两者不能混用。

## 引用生命周期

以下对象会持有 global entry 引用：

- 客户端 named/unnamed prepared statement 映射。
- PostgreSQL backend 连接上的 server prepared statement cache。
- 尚未完成的 Parse/Bind/Execute 处理链路。

引用释放规则：

- 客户端 `Close` 只释放该客户端持有的引用。
- 客户端断开释放该连接持有的引用。
- backend LRU 或 server_lifetime 清理释放 backend cache 持有的引用。
- Parse 错误必须清理本次未完成链路持有的引用。
- `ResultSet.close()` 关闭 Portal，不等于删除 prepared statement 或 global entry。

普通 entry 的 `ref_count` 降到 0 后仍可以留在 global cache，等待将来复用或容量
淘汰。因此“仍能在 console 看到”不等于“仍被引用”。

## 淘汰规则

### Backend LRU

backend cache 超过 `backend_prepared_statements_limit` 时，只在当前 PostgreSQL
backend 连接内按 LRU 回收 server prepared statement。该动作释放一个 backend 对
global entry 的引用，但不等于立即删除 global entry。

### Global eviction

插入新 global entry 或 reload 缩小 `global_prepared_statements_limit` 后，如果
`total_entries > capacity`，global cache 尝试淘汰：

1. 只选择普通 entry。
2. 只选择 `ref_count=0` 的 entry。
3. 携带 cached bypass response 的 entry 不进入普通 zero-ref 淘汰候选集合。
4. 候选不足时允许 `total_entries` 暂时大于 `capacity`，不能为了强行收敛而删除
   active 或 protected entry。

容量结论必须同时展示对应阶段的：

- `SHOW GLOBAL_PREPARED_STATEMENTS` 条目 record。
- `SHOW GLOBAL_PREPARED_STATEMENTS_STATS` 统计 record。
- 必要时补充 fbasecman 的 capacity/eviction 日志原文。

## 分类与 bypass

- 普通 SQL 分类为 `NORMAL`。
- SQL 命中 heartbeat 配置时分类为 `HEARTBEAT`。
- 用户级 heartbeat 配置优先于全局 heartbeat 配置。
- 相同 SQL 在不同用户规则下不能共享错误的分类或 bypass response。
- GUC SET/RESET/DISCARD 按产品分类进入相应 bypass 路径。
- `has_bypass_response=1` 表示 entry 持有可直接返回客户端的缓存响应。
- reload 改变 heartbeat 或 GUC 配置后，已有 entry 必须按新配置重新判断行为。

## 正式用例

### 复用与引用生命周期

| 用例 | 覆盖行为 |
| --- | --- |
| `reuse_single_and_cross_client` | 单连接重复执行和跨客户端复用同一 global entry |
| `close_and_disconnect_unref` | Close、disconnect 逐步释放 client/backend 引用 |
| `shared_global_entry_disconnect_one_client_other_client_reuse_still_ok` | 一个客户端断开后，另一个继续复用共享 entry |

### statement 映射和部署

| 用例 | 覆盖行为 |
| --- | --- |
| `statement_mapping_lifecycle` | unnamed 覆盖、named 冲突、Portal close 边界 |
| `prepare_before_bind_deploy` | global hit 但当前 backend 未部署时，Bind 前补 Parse |
| `parse_invalid_error_recovery_same_connection` | Parse 失败清理后，同连接再次执行恢复 |

### 容量和淘汰

| 用例 | 覆盖行为 |
| --- | --- |
| `capacity_eviction_zero_ref` | 容量压力只淘汰 `ref_count=0` 普通 entry |
| `capacity_mixed_bypass_response_and_zero_ref_shortage` | bypass-response 保护和可淘汰候选不足 |
| `ref_count_protects_active_entries` | active entry 可以使总数暂时超过容量 |
| `backend_global_split_eviction` | backend limit 与 global limit 的独立作用范围 |
| `global_capacity_reload_shrink` | reload 缩容只淘汰满足条件的 entry |

### protocol、DISCARD 和 GUC

| 用例 | 覆盖行为 |
| --- | --- |
| `bypass_prepare_protocol_sequence` | bypass 路径下 PBDE、Describe-only、PBDES 顺序 |
| `discard_all_clears_backend_cache` | DISCARD ALL 清 backend cache 后同 SQL 重新部署 |
| `guc_bypass_set_and_reset` | GUC SET report 与 RESET ALL 分类和响应 |
| `reload_enable_guc_sync_existing_entry` | reload 后已有 GUC entry 使用新同步语义 |

### heartbeat 和用户隔离

| 用例 | 覆盖行为 |
| --- | --- |
| `heartbeat_reload_reclassifies_existing_normal_entry` | reload 后 NORMAL entry 重分类为 HEARTBEAT |
| `heartbeat_rule_precedence_over_global` | 用户级 heartbeat 覆盖全局 heartbeat |
| `same_sql_different_users_isolation` | 同 SQL 在不同用户之间不污染分类和响应 |

查看 manifest：

```bash
./run.sh show global_cache
```

运行全部或单条：

```bash
./run.sh run global_cache
./run.sh run global_cache.<case_name>
```

## 用例实现流程

新增前先判断能否合并到现有 case。参数、SQL 语法和数量变化通常应成为已有 case 的
新步骤；只有现有 18 条无法表达的独立产品契约才新增 case。

### 1. 声明 case

在 `manifest.py` 添加 `GlobalCacheCase`，写清：

- 一句话产品契约，不能只写内部阶段名。
- driver 类型和拓扑。
- 影响结论的 fbasecman 配置。
- JDBC 版本/prepare threshold。
- reload 参数和断言参数。

### 2. 选择已有 driver 能力

优先级：

1. `GC_prepared_sql_sequence.java`：顺序执行多条 statement/prepared SQL。
2. `GC_phased_prepared.java`：连接保持期间采样 console。
3. 已有参数化 JDBC/libpq 资产。
4. 确实无法表达时再新增外置 driver。

driver 输出 marker 仅用于 Python 编排，不得直接进入 PASS 报告。

当检查依赖 JDBC 连接、事务或 PreparedStatement 仍存活时，driver 必须在关键 JDBC
调用后输出 marker、`flush()` 并阻塞 stdin。编排必须经过 `CaseRuntime` 的阶段进程接口：
driver 暂停后立即运行实际 console psql，记录
`SHOW GLOBAL_PREPARED_STATEMENTS`、`SHOW SERVER_PREP_STMTS` 或统计输出及本阶段
fbasecman/PG 日志，再发送 `continue`。不得以固定 `sleep` 替代阶段同步，也不得等
driver 退出后才补查中间缓存状态。日志不是每个阶段的固定输出，只有结论依赖代理内部
流程或 PostgreSQL 是否收到 SQL 时才显式开启。完整协议和 Java 最小模板见根 `README.md` 的
“JDBC 分阶段协作”。

单次观察使用 `CaseRuntime.observe_jdbc_phases()`；它完成“等待 marker -> console
取证 -> 写入步骤 -> continue”的完整循环。需要在同一 JDBC 连接保持期间连续执行多个
动作的场景使用 `CaseRuntime.start_jdbc_phase_process()`：在 `wait_for()` 成功后，先完成
所有依赖连接存活的业务 SQL、console 快照和日志采样，最后 `resume("continue")` 并
`finish()`。任何异常都必须 `terminate()`，确保运行中的 JDBC 步骤立即持久化为 FAIL。
凡是结论依赖连接仍存活、客户端映射或 backend cache 的 JDBC 用例都必须阶段化；只有
纯粹的独立启动/退出 JDBC 健康检查才可继续通过 `execute_jdbc()` 一次性运行。当前
`basic_reuse`、`cross_client_reuse`、Parse 失败恢复、双用户隔离、heartbeat 重分类、
heartbeat 优先级以及 `GC_prepared_sql_sequence` 均在关键 SQL 后暂停并完成中间取证。

### 3. 编排和采样

- 普通 case 在 executor registry 中组合 runner 和 assertion。
- 多场景合并 case 使用 `_case_phase`，每个子场景独立收集检测项。
- 需要中间状态时使用 `observe_jdbc_phases()`，在 marker 到达且 JDBC 连接仍存活时
  执行 console 采样；观察回调必须返回实际 psql 命令与输出。若本阶段直接作业务判定，
  同时返回 `expected`、`actual`、`passed`；否则该步骤只展示快照，由检测项判定。
- 要证明 SQL 未到 PostgreSQL，使用 PG 日志窗口，不能只看 fbasecman 单条日志。

### 4. 断言

每个检测项必须明确：

- 验证的是 client、global cache、backend cache 还是 PostgreSQL。
- 预期是什么。
- 实际观察来自哪个阶段。
- 使用统计结论时附对应阶段 stats record。
- 使用条目结论时附 global/server record。

### 5. 报告

- 概览写业务动作，例如“连续执行 5 条 SQL建立首批缓存”，不能写 `seed/trigger`。
- 步骤列出实际 SQL、参数、客户端 API、预期和业务结果。
- 报告顶部列出测试内容，并把每个实际步骤对应到具体测试内容；配置和数据准备标记为
  “前置条件”。
- 检测项只保留最小充分证据。
- 配置数值必须说明是“配置上限”还是“当前值”。
- 产品日志只在证明内部流程时保留原文，标题和预期要解释原文证明什么；普通 cache
  条目和统计优先使用完整 console 输出。

### 6. 测试和验收

```bash
python3 -m unittest discover -s unit_tests -t . -p 'test_*.py'
./run.sh run global_cache.<case_name>
./run.sh run global_cache
```

验收要求：

- manifest 与 executor registry 一致。
- driver 资产可编译。
- report 没有路径、编译步骤、内部 marker、裸 delta 或难懂布尔值。
- PASS 目录保留 `report.txt`、`steps.json`、`fbasecman.log`、`summary.json` 和动作级日志，
  便于在成功结果中复核每个已持久化步骤。
- FAIL 目录保留完整诊断材料。

## 排障顺序

1. 先看 `report.txt` 的失败步骤、预期和实际。
2. 看 `fbasecman.log` 中该 SQL tag、global name、sid 和淘汰日志。
3. FAIL 时查看 `logs/` 中 JDBC/libpq、console raw、PostgreSQL 窗口。
4. 用 `SHOW GLOBAL_PREPARED_STATEMENTS` 判断 entry 分类和 `ref_count`。
5. 用 `SHOW GLOBAL_PREPARED_STATEMENTS_STATS` 判断当前数量、容量和计数。
6. 用 `SHOW SERVER_PREP_STMTS` 判断具体 backend 是否部署。
7. 不要把 server `refcount` 当成 global `ref_count`。
8. 不要把 backend LRU 当成 global eviction。
