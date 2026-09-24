# fbasecman 第 4 章高可用自动化测试设计与执行方案

**编制日期**：2026-09-17  
**对应基线**：《fbasecman转测前核心功能测试执行方案.md》第四章（四、监控、高可用、控制台和 Reload，CORE-13 至 CORE-22）  
**执行环境**：单机部署 5 个数据库实例（方案 A：复用既有测试框架 MMR/流复制拓扑）  
**源码对照**：`sources/console.c`, `sources/fb_group_node_show.c`, `sources/fb_group.c`, `sources/monitor/`  
**承载套件**：`fbasecman_regress_v2/suites/high_availability/`

---

## 一、方案背景与测试目标

本方案用于在转测前 3 天核心功能复测阶段，为第 4 章的高可用、故障切换、Monitor 探测、控制台运维指令与配置 Reload 建立全自动化、可审计的测试体系。

### 核心覆盖范围（10 个核心用例）
1. **CORE-13**：monitor 故障与恢复确认的防抖周期与连续重试次数判断。
2. **CORE-14**：复制组备机故障时读回退主库，备机恢复重新分流，在途请求不假成功。
3. **CORE-15**：复制组主库故障但暂不升主时，备库在 `NO_PRIMARY` 下安全提供只读。
4. **CORE-16**：复制组主备切换（旧主 PARTED 隔离 $\rightarrow$ 新主升主 $\rightarrow$ REFRESH $\rightarrow$ 新写路由 $\rightarrow$ 旧主从库重建回归）。
5. **CORE-17**：MMR 故障自动漂移至 `promoted_cluster`、唯一写目标断言、人工命令 `SET NODE WRITE` 切换与持久化。
6. **CORE-18**：balance/single 节点故障与只读回退策略（全组备机优先，备机全灭才回退主库）。
7. **CORE-19**：SET NODE 单节点/批量操作原子性（非法节点整体拒绝，严禁部分写入）与重启持久化。
8. **CORE-20**：WRITE/PROMOTED、REFRESH CLUSTER 合法与非法指令边界，以及 SHOW 全量字段审计。
9. **CORE-21**：Reload 无变化测试、运行参数修改热生效、动态增删用户组结构变更隔离。
10. **CORE-22**：配置文件语法错误保护、持久化目录只读时的失败回滚保护。

---

## 二、方案 A：单机 5 数据库实例拓扑映射

根据评审确定的**方案 A**，直接复用测试框架原生支持的多实例拓扑，通过端口与独立目录完成隔离，100% 映射文档 2.1 节的角色定义：

| 逻辑节点 | 文档角色 | 对应框架实例 | 端口 | PGDATA 路径 | 核心特征与物理关系 |
|---|---|---|---|---|---|
| **CM** | M1: 代理与控制台 | `fbasecman` 进程 | `17432` (普通读) / `17433` (写) | `/tmp/fbasecman_ha` | 业务接入代理、控制台管理与日志采集端 |
| **A0** | M2: `site_a` MMR 主库 | `test_mmr1` | `10011` | `..._mmr/test_mmr1` | 初始写中心；`site_a` 主库；与 B0 双向 MMR 互通 |
| **A1** | M3: `site_a` 流复制备库 | `test_mmr1_s1` | `10012` | `..._mmr/test_mmr1_s1` | A0 的物理流复制备库；只读分流第一候选 |
| **B0** | M4: `site_b` MMR 主库 | `test_mmr2` | `10021` | `..._mmr/test_mmr2` | MMR promoted 备用中心；`site_b` 主库；第二主库 |
| **B1** | M5: `site_b` 流复制备库 | `test_mmr2_s1` | `10022` | `..._mmr/test_mmr2_s1` | B0 的物理流复制备库；跨中心只读备选 |

> [!NOTE]
> - **拓扑完备性**：A0 与 A1 之间存在持续流复制；B0 与 B1 之间存在持续流复制；A0 与 B0 之间配置 MMR 逻辑复制。
> - **故障注入方法**：调用 `pg_ctl -D <pgdata> stop -m immediate` 模拟进程断电/宕机；调用 `start` 恢复；真实触发 monitor 的网络拒绝与超时探测。

---

## 三、4 种逻辑组配置模型（文档 2.2 节）

在 `suites/high_availability/assets/config/fbasecman_ha.conf` 中声明标准组：

```text
# 1. 复制组（一主一备读写分离与主挂备机只读）
group "qa_rep" {
    type "replication"
    backend_clusters "site_a"
    check "auto"
}

# 2. MMR 组（双活中心、唯一写中心与 Promoted 漂移）
group "qa_mmr" {
    type "mmr"
    backend_clusters "site_a,site_b"
    write "site_a"
    promoted "site_b"
    check "auto"
}

# 3. Balance 读写组（双主加权负载均衡）
group "qa_bal_rw" {
    type "balance"
    backend_clusters "site_a,site_b"
    access_mode "read_write"
}

# 4. Balance 只读组（全组备机优先，备机全挂才回退主库）
group "qa_bal_ro" {
    type "balance"
    backend_clusters "site_a,site_b"
    access_mode "read_only"
}

# 5. Single 读写组（固定 A 中心当前主库）
group "qa_single_rw" {
    type "single"
    backend_clusters "site_a"
    access_mode "read_write"
}

# 6. Single 只读组（固定 A 中心最高权重备库）
group "qa_single_ro" {
    type "single"
    backend_clusters "site_a"
    access_mode "read_only"
}
```

测试用户规划：
- 控制台管理用户：`qa_admin`（权限等同 console 管理员）
- 业务测试用户：`qa_app_user`（支持事务与读写 Hint）
- 基础测试表：
  ```sql
  CREATE SCHEMA IF NOT EXISTS qa_case;
  CREATE TABLE IF NOT EXISTS qa_case.orders (
      id bigint PRIMARY KEY,
      value integer NOT NULL,
      note text,
      updated_at timestamp DEFAULT clock_timestamp()
  );
  ```

---

## 四、控制台结构化解析与 C 源码级字段断言体系（核心重点）

> [!IMPORTANT]
> **测试断言原则**：绝不仅将控制台输出当成文本日志查看。框架通过专属解析模块 `console_parser.py` 将控制台输出解析为结构化字典，**直接对照 `fbasecman` C 源码定义的列名与枚举值**，在用例各阶段对关键字段进行严格业务逻辑校验。字段值不符立即判定 FAIL！

### 1. `SHOW GROUP_ROUTING <group>`（14 列，对照 `sources/console.c:3208-3220`）

C 语言输出定义：
`kiwi_be_write_row_descriptionf(stream, "ssssssssssssss", "group_name", "group_mode", "user_name", "cluster_name", "current_primary", "candidate_node", "candidate_type", "effective_grouprole", "effective_state", "is_write_target", "write_source", "fallback_reason", "route_status", "unavailable_reason")`

| 字段名称 | 源码对应取值 | 业务断言检查标准 |
|---|---|---|
| `group_name` | `group->name` | 严格匹配被查组名（如 `qa_rep`, `qa_mmr`） |
| `group_mode` | `"replication"`, `"mmr"`, `"balance"`, `"single"` | 匹配配置的组拓扑模式 |
| `cluster_name` | `"site_a"`, `"site_b"` | 匹配当前行候选所在的 cluster |
| `current_primary` | `cluster->current_primary` | **主库精准识别**：正常为 `test_mmr1` (A0)；升主后必须变为 `test_mmr1_s1` (A1)；MMR 切换后为 `test_mmr2` (B0) |
| `candidate_node` | 规则关联的节点名 | **候选名单增删断言**：健康备机必须在列；**节点宕机或 PARTED 后，必须从只读候选列表中彻底消失**！ |
| `candidate_type` | `"WRITE"`, `"READ"`, `"ROUTE"` | 拆分组区分 WRITE/READ；balance/single 显示 ROUTE |
| `effective_grouprole`| `sources/fb_group.c:3546`: `"write-leader"`, `"non-write-leader"`, `"primary"`, `"replica"`, `"UNKNOWN"` | 准确反映角色状态：写中心为 `write-leader`，备用中心在未接管时为 `non-write-leader`，只读从库为 `replica` |
| `effective_state` | `sources/fb_group_node_show.c:234`: `"active"`, `"parted"`, `"promoted"` | 节点有效状态：执行 `SET NODE PARTED` 变为 `parted`；MMR 漂移接管后显示 `promoted` |
| `is_write_target` | `"true"`, `"false"` | **唯一性断言**：全组**只能且必须有 1 行**为 `"true"`，其余全为 `"false"`！ |
| `write_source` | `sources/console.c:3069`: `"WRITE_CLUSTER"`, `"PROMOTED_CLUSTER"`, `"WEIGHT_FALLBACK"`, `NULL` | 正常为 `WRITE_CLUSTER`；A 故障漂移到 B 后**断言变为 `PROMOTED_CLUSTER`**；人工锁定切换后变回 `WRITE_CLUSTER` |
| `fallback_reason` | `sources/console.c:3072`: `"WRITE_CLUSTER_UNAVAILABLE"`, `"PROMOTED_CLUSTER_UNAVAILABLE"`, `NULL` | 漂移后**断言必为 `WRITE_CLUSTER_UNAVAILABLE`** |
| `route_status` | `sources/console.c:3049`: `"AVAILABLE"`, `"UNAVAILABLE"` | 正常为 `AVAILABLE`；主库宕机无写目标时**写候选断言为 `UNAVAILABLE`** |
| `unavailable_reason` | `sources/console.c:3140`: `"NO_VERIFIED_WRITE_TARGET"`, `"NO_VERIFIED_READ_TARGET"`, `"NO_VERIFIED_ROUTE_TARGET"`, `NULL` | 无可用候选时，断言输出精确的不可用原因代码 |

---

### 2. `SHOW CLUSTERS`（13 列，对照 `sources/console.c:2822-2832`）

C 语言输出定义：
`kiwi_be_write_row_descriptionf(stream, "ssdsdddslssss", "cluster_name", "nodes", "monitor_enabled", "probe_features", "monitor_max_retries", "monitor_recovery_max_retries", "delay_threshold", "topology_state", "topology_seq", "current_primary", "last_trusted_primary", "route_publish_state", "route_publish_error")`

| 字段名称 | 源码对应取值 | 业务断言检查标准 |
|---|---|---|
| `cluster_name` | `"site_a"`, `"site_b"` | 集群名称 |
| `topology_state` | `"ONLINE"`, `"CONNECT_FAILED"`, `"NO_PRIMARY"`, `"PARTED"` | 健康必须为 `ONLINE`；主库宕机后必须迁移为 `CONNECT_FAILED` 或 `NO_PRIMARY`（**严禁已宕机仍显示 ONLINE 假在线**） |
| `current_primary` | 节点名或 `NULL` | 必须与实际存活的主库精准匹配，拓扑刷新后必须即时更新 |
| `last_trusted_primary`| 节点名或 `NULL` | 主库宕机后，断言从库依然记录了上一次可信主库，支撑从库安全提供只读 |
| `route_publish_state` | `"PUBLISHED"`, `"FAILED"`, `NULL` | 状态变更完成发布后断言为 `PUBLISHED`，无发布错误 |

---

### 3. `SHOW GROUP_MEMBERS`（17 列，对照 `sources/console.c:3443-3454`）

C 语言输出定义：
`kiwi_be_write_row_descriptionf(stream, "sssssssssddssssss", "group_name", "database", "user", "group_mode", "rw_split_method", "node_name", "cluster_name", "storage_db", "storage_host", "storage_port", "weight", "effective_check", "config_status", "state", "group_role", "effective_grouprole", "primary")`

- 核对所有配置节点全部在列，无丢失、无幽灵节点。
- `state`：执行 `SET NODE PARTED` 后，断言目标节点 `state` 严格变为 `parted`；`SET NODE ACTIVE` 后恢复为 `active`。
- `group_role` 与 `effective_grouprole`：断言物理配置角色与当前有效角色一致。

---

### 4. `SHOW CONFIG_STATUS`（8 列，对照 `sources/console.c:3318-3325`）

C 语言输出定义：
`kiwi_be_write_row_descriptionf(stream, "stssssss", "config_generation", "last_config_publish_time", "last_reload_kind", "last_reload_result", "main_config_digest", "userlist_digest", "monitor_registry_generation", "parser_policy_version")`

| 字段名称 | 源码取值 | 业务断言检查标准 |
|---|---|---|
| `config_generation` | `uint64` 整数 | 每次配置热重载或写入生效后，**断言版本代号严格单调递增**；无变化 Reload 保持不变 |
| `last_reload_kind` | `"MAIN_CONFIG"`, `"USERLIST"`, `"CONFIG_WRITE_COMMAND"`, `"NONE"` | 配置文件 Reload 时断言为 `MAIN_CONFIG`；控制台写命令时断言为 `CONFIG_WRITE_COMMAND` |
| `last_reload_result` | `"SUCCESS"`, `"FAILURE"`, `"NONE"` | 正常重载断言为 `SUCCESS`；语法错误重载**严格断言为 `FAILURE`** |
| `main_config_digest` | 64 位 SHA256 哈希字符串 | 校验主配置文件真实内容哈希，证明磁盘文件更新 |

---

## 五、三层证据链与五大状态时间戳标准（文档 2.4 节）

每个用例产出的 `output/runs/high_availability/<case>/report.txt` 必须包含：
1. **控制台结构化字段断言表**：输出实际执行的 SHOW 命令、解析的字段实际值 vs 预期值判定结果。
2. **`fbasecman` 运行日志窗口**：精准截取请求进入时间、选定 datasource、错误日志。
3. **PostgreSQL 物理落点验证**：通过 `SELECT inet_server_port();` 验证物理连接落点端口，确保未发生路由漂移错误。
4. **五大状态时间戳记录**（涉及状态变化的用例）：
   - $T_1$：故障注入时间点（如 `pg_ctl stop` 执行瞬间）
   - $T_2$：Monitor 首次探测到连接失败时间点（日志首次记录 connection failed）
   - $T_3$：重试达到阈值，Monitor 确认故障并发布新路由时间点
   - $T_4$：首笔业务 SQL 按新路由成功执行时间点
   - $T_5$：节点恢复启动，状态就绪并重新入围时间点

---

## 六、CORE-13 至 CORE-22 用例字段级断言落地细则

### 阶段一：控制台指令、原子性与 Reload 保护（非破坏性）

#### 1. [CORE-19] `core_19_set_node_atomicity`（SET NODE 与批量原子性）
- **操作步骤与断言**：
  1. 单节点隔离：执行 `SET NODE PARTED test_mmr1_s1;`。
     - 断言 `SHOW GROUP_ROUTING` 中 A1 的 `effective_state="parted"`；
     - 比对主配置文件，断言 `parted` 配置项已持久化落盘。
  2. 单节点恢复：执行 `SET NODE ACTIVE test_mmr1_s1;`，执行 `REFRESH CLUSTER site_a;` 后恢复 `effective_state="active"`。
  3. 批量去重验证：执行 `SET NODE PARTED test_mmr1, test_mmr1_s1, test_mmr1;`，核对去重执行成功。
  4. **批量原子性（核心断言）**：执行 `SET NODE PARTED test_mmr1_s1, NON_EXISTENT_NODE;`。
     - 断言控制台返回错误拒绝；
     - **核对 SHOW 表中 A1 的 `effective_state` 依然保持 `active`，绝对未被部分修改为 `parted`**；
     - 比对主配置文件内容，断言未发生任何半写入。
  5. 重启持久化：合法修改后重启 `fbasecman`，断言 SHOW 输出与磁盘配置一致。

#### 2. [CORE-20] `core_20_write_promoted_refresh_show`（角色命令与 SHOW 校验）
- **操作步骤与断言**：
  1. 对 `qa_mmr` 执行合法 `SET NODE PROMOTED` 与 `SET NODE WRITE test_mmr2 IN GROUP qa_mmr;`。
  2. 非法命令测试：
     - 尝试将 replica 备库设为 WRITE；
     - 尝试对 balance/single 组执行 SET NODE WRITE；
     - 指定不存在的组名；
     - **断言全部直接报错拒绝，且配置文件无任何写入**。
  3. 执行 `REFRESH CLUSTER site_a;`，验证触发后台重新探测。
  4. 全量审计 `SHOW DATASOURCES`、`SHOW CLUSTERS`、`SHOW GROUP_MEMBERS`、`SHOW GROUP_ROUTING` 的全部输出列名和格式。

#### 3. [CORE-21] `core_21_reload_parameters_and_structure`（参数与结构热重载）
- **操作步骤与断言**：
  1. 客户端持续运行短事务，并发执行 `RELOAD` 命令，断言连接中断数=0，PID 不变。
  2. 修改 `log_min_messages` 为 `debug`，执行 `RELOAD`，验证日志输出级别即时变更。
  3. 动态在配置文件中追加新增组 `qa_temp_group`，`RELOAD` 后：
     - `SHOW CONFIG_STATUS` 中 `config_generation` 自增，`last_reload_result="SUCCESS"`；
     - `SHOW GROUP_ROUTING qa_temp_group` 能查到新组，旧长事务连接不受干扰。
  4. 删除新增组后 `RELOAD`，连接退出后验证内部路由资源完全回收。

#### 4. [CORE-22] `core_22_reload_failure_protection`（Reload 异常回滚保护）
- **操作步骤与断言**：
  1. 制造配置文件语法错误（如缺少闭合括号），执行 `RELOAD`：
     - 断言控制台报错拒绝；
     - 断言 `SHOW CONFIG_STATUS` 中 `last_reload_result="FAILURE"`；
     - `fbasecman` 继续沿用旧配置服务。
  2. 将配置备份目录权限设置为只读（`chmod 555`），执行 `SET NODE PARTED test_mmr1_s1;`：
     - 断言命令报错拒绝，且不残留半写入临时文件。
  3. 恢复目录写权限，重新执行命令成功。

---

### 阶段二：节点故障感知与主从回退（高可用探活与降级）

#### 5. [CORE-13] `core_13_monitor_confirm`（Monitor 探测与防抖确认）
- **操作步骤与断言**：
  1. 记录配置的 `monitor_period` (2s)、`monitor_retry_period_ms` (1000ms)、连续失败阈值 (3次)、连续恢复阈值 (3次)。
  2. 故障防抖测试：停止 A1，在失败计数达到 2 次时启动 A1，轮询断言 A1 的 `effective_state` 仍为 `active`，连续失败计数清零。
  3. 确认故障：持续停机 A1 满 3 次重试：
     - **断言 `SHOW GROUP_ROUTING qa_rep` 中 A1 从只读候选列表中完全消失**；
     - **断言 `SHOW CLUSTERS` 中 `site_a` 拓扑状态感知到备库不可用**。
  4. 恢复防抖测试：启动 A1，成功 1 次后再次制造不可达，断言 A1 保持在屏蔽状态，连续成功计数清零。
  5. 确认恢复：保持 A1 健康，连续成功满 3 次，**断言 A1 恢复 `active` 并重新进入只读候选**。
  6. 检查单节点 A1 故障探测期间，B 组节点探测正常，未被阻塞。

#### 6. [CORE-14] `core_14_rep_standby_failure`（复制组备机故障）
- **操作步骤与断言**：
  1. 初始基线：`qa_rep` 写落点为 A0 (10011)，读落点为 A1 (10012)。
  2. 停止 A1：
     - 在途执行中的请求：捕获连接断开异常，断言绝对不返回假成功；
     - `SHOW GROUP_ROUTING qa_rep` 断言：写候选 A0 正常（`is_write_target="true"`, `route_status="AVAILABLE"`）；读候选仅剩 A0；
     - 物理落点验证：新读请求物理落点端口自动由 10012 回退至 10011；写请求持续为 10011。
  3. 恢复 A1：启动 A1，探测就绪后新读请求重新均衡分流至 A1 (10012)。

#### 7. [CORE-15] `core_15_rep_primary_failure`（主宕未升主备机只读）
- **操作步骤与断言**：
  1. 确认 A0 与 A1 流复制正常。
  2. 停止 A0 主库（**严禁执行 PARTED，严禁升主**）。
  3. 控制台字段断言：
     - `SHOW CLUSTERS`: `site_a` 的 `topology_state="NO_PRIMARY"`, `current_primary="NULL"`, `last_trusted_primary="test_mmr1"`；
     - `SHOW GROUP_ROUTING qa_rep`: WRITE 行的 `route_status="UNAVAILABLE"`, `unavailable_reason="NO_VERIFIED_WRITE_TARGET"`；READ 行的 A1 `route_status="AVAILABLE"`！
  4. 业务请求断言：
     - 新写请求：明确报错拒绝（无法连接主库）；
     - **新读请求：断言 A1 备库在 `NO_PRIMARY / UNRESOLVED` 状态下继续提供只读服务，物理端口为 10012**。
  5. 重启 A0 恢复流复制并执行 `REFRESH CLUSTER site_a;`，读写恢复正常。

#### 8. [CORE-18] `core_18_balance_single_failure`（多策略回退）
- **操作步骤与断言**：
  1. 初始基线：`qa_bal_rw` 分流 A0/B0；`qa_bal_ro` 分流 A1/B1；`qa_single_rw` 绑定 A0；`qa_single_ro` 绑定 A1。
  2. 停止 B0：断言 `qa_bal_rw` 自动重选存活的 primary A0 (10011)。
  3. 恢复 B0，停止 B1：
     - `SHOW GROUP_ROUTING qa_bal_ro` 断言：候选行中只包含存活备机 A1，**主库 A0/B0 绝对不进入当前只读候选**；
     - 业务读请求物理端口 100% 走 A1 (10012)。
  4. 再次停止 A1（全组备机全灭）：断言 `qa_bal_ro` 回退到健康主库。
  5. 停 A1 验证 `qa_single_ro`：候选回退主库 A0；若 A0 也停止，断言请求明确报错拒绝，禁止静默挂起。
  6. 恢复所有实例并 REFRESH。

---

### 阶段三：主备切换与 MMR 写中心漂移（强拓扑变更）

#### 9. [CORE-16] `core_16_rep_failover`（复制组主备切换与重建）
- **操作步骤与断言**：
  1. 停止 A0 并隔离旧主；控制台执行 `SET NODE PARTED test_mmr1;`，核对持久化生效。
  2. 数据库层对 A1 执行升主：`pg_ctl promote -D <a1_pgdata>`。
  3. 控制台刷新：执行 `REFRESH CLUSTER site_a;`，轮询 `SHOW CLUSTERS`，**断言 `current_primary` 字段变为 A1 (test_mmr1_s1)**。
  4. 控制台断言：`SHOW GROUP_ROUTING qa_rep` 中，A1 的 `is_write_target="true"`，`effective_grouprole="write-leader"` 或 `"primary"`。
  5. 发送业务写请求，断言物理落点为 A1 (10012)，数据写入成功。
  6. 使用 `pg_basebackup` 从 A1 重建 A0 为备库并启动；控制台执行 `SET NODE ACTIVE test_mmr1;` 并 `REFRESH`，验证 A0 恢复为备库提供只读。

#### 10. [CORE-17] `core_17_mmr_write_center_failover`（MMR 故障漂移与人工锁定）
- **操作步骤与断言**：
  1. 初始基线：`SHOW GROUP_ROUTING qa_mmr` 中 A0 行 `is_write_target="true"`, `write_source="WRITE_CLUSTER"`。
  2. 停止 A0 主库：
     - Monitor 探活确认 `site_a` 故障；
     - 依据配置的 `promoted_cluster = "site_b"`，**自动切换 `site_b` 的 Primary B0 (10021) 承担唯一写中心**；
     - **断言 `SHOW GROUP_ROUTING qa_mmr` 中全表依然仅有 B0 所在行 `is_write_target="true"`**；
     - **断言 B0 所在行 `write_source="PROMOTED_CLUSTER"`，`fallback_reason="WRITE_CLUSTER_UNAVAILABLE"`，`effective_grouprole="write-leader"`，`effective_state="promoted"`**！
     - 验证故障中在途旧事务不自动重放，新写请求打向 B0 成功提交。
  3. 恢复 A0 并启动数据库。
  4. 人工锁定切换：执行 `SET NODE WRITE test_mmr2 IN GROUP qa_mmr;`：
     - 核对配置文件中 `qa_mmr` 写中心持久化到磁盘；
     - `SHOW GROUP_ROUTING qa_mmr` 中 B0 的 `write_source` 恢复为 `"WRITE_CLUSTER"`，`fallback_reason` 为 `NULL`；
     - 审计其他组（`qa_rep` 等），断言未受任何影响。

---

## 七、工程代码结构设计

```text
suites/high_availability/
├── __init__.py               # 包声明
├── case.py                   # HighAvailabilityCase 数据模型
├── manifest.py               # 10 个用例清单元数据
├── suite.py                  # 测试套件总调度入口 (show, run, run_case)
├── runtime.py                # 执行上下文、三层证据收集器、五大时间戳记录器
├── console_parser.py         # [核心] 控制台表格/管道输出结构化解析与字段语义断言器
├── cluster_ops.py            # 单机 5 实例的启停、探活、升主、重建工具
├── executors/                # 用例具体执行体
│   ├── __init__.py
│   ├── phase1_console.py     # CORE-19 ~ CORE-22 (控制台原子性与 Reload 保护)
│   ├── phase2_failure.py     # CORE-13 ~ CORE-15, CORE-18 (故障感知与只读回退)
│   └── phase3_failover.py    # CORE-16, CORE-17 (主备切换与 MMR 漂移)
└── assets/
    └── config/
        └── fbasecman_ha.conf # 4 种组与 5 节点标准配置模板
```

---

## 八、运行与验收标准

```bash
# 1. 查看本套件所有用例
./run.sh show high_availability

# 2. 运行单条用例验证（如原子性测试）
./run.sh run high_availability.core_19_set_node_atomicity

# 3. 运行完整第 4 章测试套件
./run.sh run high_availability

# 4. 单元测试与架构分层规则检查
python3 -m unittest discover -s unit_tests -t . -p 'test_*.py'
```

**用例 PASS 判定标准**：
1. 控制台解析后所有业务字段断言 100% 匹配预期。
2. 物理落点端口（`inet_server_port()`）符合读写分流及回退路径。
3. 产出完整的 `report.txt`，包含结构化断言表、实际 SQL/命令、fbasecman 日志窗口及时间戳记录。
4. 运行结束后环境拓扑与配置文件 100% 恢复初始健康基线。
