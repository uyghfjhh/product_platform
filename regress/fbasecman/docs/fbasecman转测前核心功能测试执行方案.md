# fbasecman 转测前 3 天核心功能测试执行方案

编制日期：2026-09-17。本文是在每名测试人员独占 5 台机器、总测试时间 3 天的条件下，从《fbasecman 转测前全功能复测执行方案》中裁剪出的核心执行集。完整方案仍作为后续补测和覆盖检查依据。

## 一、测试目标与范围

本轮只验证转测前最关键的能力：

1. replication、MMR、balance、single 四种组的配置及路由。
2. hint、port、sql_parse 三种请求分类方式。
3. Simple Query、Extended Query、事务、GUC 和 Prepared Statement。
4. monitor 故障识别、主备故障、MMR 写中心切换与恢复。
5. SET NODE、SET NODE WRITE/PROMOTED、REFRESH CLUSTER、SHOW、Reload 和配置持久化。
6. 并发建连、故障节点连接、基础性能与关键历史缺陷回归。

本轮暂不展开 LDAP、PAM、TLS 证书组合、License、压缩、Prometheus、7×24 长稳、在线无损重启和 SET AUTH USER/在线改密。未测内容记为“本轮未覆盖”，不能记为通过。

测试结果使用 PASS、FAIL、BLOCKED、N/A。每个 FAIL 必须包含输入、实际输出、预期、日志时间点和恢复结果。

## 二、每人 5 台机器的统一环境

### 2.1 机器和数据库部署

每名测试人员使用自己独占的 5 台机器，不共享环境，也不需要预约公共故障窗口：

| 机器 | 部署内容 | 逻辑名称 | 用途 |
|---|---|---|---|
| M1 | fbasecman、psql、JDBC/协议工具、监控采集脚本 | CM | 代理、控制台、客户端和采集端 |
| M2 | PostgreSQL/MMR 主节点 | A0，cluster `site_a` | 正常写中心；replication/single 的主库 |
| M3 | M2 的流复制备节点 | A1，cluster `site_a` | replication 读节点；A 中心备机 |
| M4 | PostgreSQL/MMR 主节点 | B0，cluster `site_b` | MMR promoted 中心；balance 第二主库 |
| M5 | M4 的流复制备节点 | B1，cluster `site_b` | B 中心备机；跨中心只读候选 |

注意：

- M2/M3 是同一物理主备 cluster，M4/M5 是另一 cluster；不能把四台数据库都配置成四个独立 cluster。
- A0、B0 是两个 MMR 中心节点；A1、B1 是各自流复制备机，不是独立多活中心。
- 主机故障测试会同时停止该机上全部进程。本方案每机只部署一个数据库实例，便于判断故障影响。
- M1 同时运行代理和客户端，因此性能结果只用于回归比较，不能直接作为生产容量指标。

### 2.2 一套后端复用四种组

在同一份配置中建立以下逻辑组，并为不同分类方式建立独立用户，避免反复修改共享用户配置：

| 组 | 配置要点 | 核心用途 |
|---|---|---|
| `qa_rep` | replication；backend_clusters=`site_a`；check=auto | 一主一备读写分离及无主读 |
| `qa_mmr` | mmr；backend_clusters=`site_a,site_b`；write=`site_a`；promoted=`site_b`；check=auto | 唯一写中心、中心切换、MMR 门禁 |
| `qa_bal_rw` | balance；A+B；access_mode=read_write | 两个健康 primary 加权访问 |
| `qa_bal_ro` | balance；A+B；access_mode=read_only | 全组备机优先及主库回退 |
| `qa_single_rw` | single；site_a；access_mode=read_write | 固定使用 A 当前主库 |
| `qa_single_ro` | single；site_a；access_mode=read_only | 固定使用 A 最高权重健康备库 |

为 `qa_rep`、`qa_mmr` 分别准备 hint、port、sql_parse 用户；sql_parse 核心用户使用 transaction 池、开启 `pool_reserve_prepared_statement` 和 `enable_guc_sync`。balance/single 使用 `rw_split_method none`。

port 用户使用两个明确的监听端口，例如普通端口 6432、写端口 6433；实际端口按个人环境替换。四种组使用相同业务库和测试表即可，前提是 MMR/流复制已确保数据一致。

### 2.3 环境变量和测试数据

以下名称是文档占位符，不要求直接复制。每人先建立自己的环境记录：

```text
CM_HOST=<M1地址>
CM_PORT=<普通端口>
CM_WRITE_PORT=<写端口>
CONSOLE_USER=<控制台用户>
APP_USER_HINT=<hint用户>
APP_USER_PORT=<port用户>
APP_USER_SQL=<sql_parse用户>
TEST_SCHEMA=qa_<姓名或编号>_<日期>
```

在 M2/A0 上创建测试对象，并确认 M3、M4、M5 均可看到。DDL 同步方式按实际 MMR 环境执行：

```sql
CREATE SCHEMA qa_case;
CREATE TABLE qa_case.orders (
    id bigint PRIMARY KEY,
    value integer NOT NULL,
    note text,
    updated_at timestamp DEFAULT clock_timestamp()
);
INSERT INTO qa_case.orders VALUES (10001, 1, 'baseline', clock_timestamp());
```

如果 8 人环境会使用相同外部 MMR 集群，则必须把 schema 改成每人唯一名称；本次前提是各自独占五台机器，但仍建议保留唯一 schema，便于定位日志。

### 2.4 统一观测方法

每个涉及路由的用例，至少保留三类证据：

1. fbasecman 控制台：

```sql
SHOW CLUSTERS;
SHOW GROUP_MEMBERS;
SHOW GROUP_ROUTING;
SHOW RULES;
```

2. fbasecman 日志：记录请求时间、group、选中的 datasource/后端地址和错误。
3. 后端证据：通过 PostgreSQL 日志或 `pg_stat_activity.application_name` 确认实际落点。用于判定普通 SQL 路由的 SQL 本身不要加入会改变分类的节点探测函数。

涉及状态变化时记录五个时间点：故障注入、首次探测失败、确认故障并发布、第一笔业务按新路由成功、恢复完成。

## 三、配置、路由和 SQL 核心用例

### CORE-01 候选包与环境基线

**步骤**

1. 记录 fbasecman commit/tag、二进制 SHA256、编译选项、配置文件 SHA256、PostgreSQL/MMR 和 JDBC 版本。
2. 直连 M2～M5，执行 `SELECT pg_is_in_recovery();`，确认 A0/B0 为 primary、A1/B1 为 replica。
3. 在 A0 写入一条唯一记录，等待并确认 A1、B0、B1 均可读取。
4. 检查 MMR group/node 视图在实际 storage_db 中存在、返回唯一非 NULL 结果，后端探测用户有权限。
5. 启动 fbasecman，保存启动日志和 SHOW CLUSTERS 快照。

**预期**

- 候选包和配置可追溯；两套物理 cluster 均为可信单主。
- MMR 数据和视图正常。任何后端基线失败先记 BLOCKED，不把数据库故障归因给 fbasecman。

### CORE-02 四种组启动和最小访问

**步骤**

1. 使用控制台检查四种组、成员和候选：`SHOW GROUP_MEMBERS`，分别执行 `SHOW GROUP_ROUTING <group>`。
2. 依次连接 `qa_rep`、`qa_mmr`、`qa_bal_rw`、`qa_single_rw`。
3. 每个组执行 `SELECT`、一条使用独立主键的 INSERT、COMMIT，再查询结果。
4. 检查后端实际落点及数据最终一致性。

**预期**

- replication/MMR 写只到有效写目标；balance/single 使用各自策略。
- group 名正确映射到实际 storage_db；无串库、串用户或错误成员。

### CORE-03 replication 与 single 路由

**步骤**

1. 在 `qa_rep` 连续执行 20 个独立只读事务和 5 个写事务。
2. 在 `qa_single_rw` 连续执行读写事务；在 `qa_single_ro` 执行 20 个只读事务。
3. 临时将 A1 设置为 PARTED，重复上述请求；再 ACTIVE＋REFRESH 恢复。
4. 每阶段保存 GROUP_ROUTING 和实际后端证据。

**预期**

- qa_rep 读优先 A1、写使用 A0；A1 不可用时读回退健康 A0。
- single_rw 固定 A0；single_ro 固定 A1，A1 不可用时回退 A0，不做随机负载均衡。
- ACTIVE 只解除人工隔离，必须经探测确认后才重新路由。

### CORE-04 MMR 与 balance 路由

**步骤**

1. `qa_mmr` 执行 20 个独立读事务和 5 个写事务。
2. `qa_bal_rw` 执行至少 100 个独立短事务，统计 A0/B0 落点。
3. `qa_bal_ro` 执行至少 100 个独立只读事务，统计 A1/B1 落点。
4. 将 A1 PARTED，但保持 B1 健康，重新测 `qa_bal_ro`；再将 B1 PARTED 后重测。

**预期**

- qa_mmr 写唯一使用 A0；读使用符合门禁的非写目标，必要时才回退写目标。
- balance read_write 使用健康 primary；read_only 在全组仍有健康备机时不得提前回退任一主库，所有备机均不可用时才回退健康 primary。
- 100 次只用于发现明显偏斜/错误候选，不要求精确等于理论权重。

执行完恢复 A1/B1 ACTIVE 并 REFRESH，确认四个节点全部处于预期状态。

### CORE-05 hint 与 port 分类

**步骤**

1. hint 用户分别发送文档规定的读 hint、写 hint、无 hint和错误 hint；每条单独提交。
2. port 用户在普通端口执行 SELECT/INSERT，在写端口执行 SELECT/INSERT。
3. 在复制组和 MMR 组各执行一次。
4. 核对请求落点、返回值和数据结果。

**预期**

- hint 行为保持当前兼容契约；错误 hint 不造成崩溃或串路由。
- 写端口始终按写方向处理，不因 SQL 是 SELECT 或主库故障自动变成读端口。

### CORE-06 sql_parse 基本分类

**步骤**

逐条执行并核对落点：

```sql
SELECT id, value FROM qa_case.orders WHERE id = 10001;
INSERT INTO qa_case.orders VALUES (10002, 2, 'insert', clock_timestamp());
UPDATE qa_case.orders SET value = value + 1 WHERE id = 10002;
SELECT id FROM qa_case.orders WHERE id = 10001 FOR UPDATE;
WITH u AS (UPDATE qa_case.orders SET value=value+1 WHERE id=10002 RETURNING id)
SELECT * FROM u;
CREATE TEMP TABLE qa_tmp(id integer);
```

再执行大小写、空白和普通注释变体；执行一个未被明确识别为安全的自定义函数 SELECT。

**预期**

- 普通安全读走读方向；写、锁定读、写 CTE、DDL/临时对象及不能证明安全的函数保守走写。
- 不能只因为语句以 SELECT 开头就分配到备库。

### CORE-07 普通事务读转写

**步骤**

在同一连接逐条发送并等待响应：

```sql
BEGIN;
SELECT id, value FROM qa_case.orders WHERE id = 10001;
UPDATE qa_case.orders SET value = value + 1 WHERE id = 10001;
SELECT id, value FROM qa_case.orders WHERE id = 10001;
COMMIT;
```

1. 记录第一条读和 UPDATE 的实际落点。
2. 检查内部读事务收口、写阶段开启及最终数据。
3. 再执行 `BEGIN READ WRITE`＋SELECT＋UPDATE＋COMMIT。
4. 执行 `BEGIN READ ONLY`＋SELECT＋UPDATE＋ROLLBACK。

**预期**

- 普通 BEGIN 可先读后写，写阶段建立后直到事务结束保持写方向；最终只提交一次预期更新。
- READ WRITE 从首条开始用写目标；READ ONLY 的写请求报错，不自动切到写节点。
- 读转写是分段事务，不要求跨阶段保持同一快照。

### CORE-08 事务禁止切换状态

**步骤**

分别建立以下状态后尝试从读方向执行 UPDATE：SAVEPOINT、SET LOCAL、活动游标/Portal、临时对象、COPY。每类单独新建连接或完整回滚后再测下一类。

示例：

```sql
BEGIN;
SELECT id FROM qa_case.orders WHERE id=10001;
SAVEPOINT s1;
UPDATE qa_case.orders SET value=value+1 WHERE id=10001;
ROLLBACK;
```

**预期**

- 不能安全迁移时明确拒绝，不静默另开后端事务、不部分提交。
- ROLLBACK 后连接状态恢复，下一事务可正常使用。

### CORE-09 保存点和失败事务

**步骤**

1. 正常执行 BEGIN、SAVEPOINT、ROLLBACK TO、SELECT、COMMIT。
2. 执行 BEGIN、SAVEPOINT、`SELECT 1/0`、ROLLBACK TO、SELECT、COMMIT。
3. 失败状态下执行普通 SQL，检查 25P02；再用 ROLLBACK 结束。
4. Extended 协议再执行一次错误后的 Sync 和下一事务。

**预期**

- 正常 ROLLBACK TO 不被代理错误拦截；错误后可以按 PostgreSQL 语义恢复到保存点。
- 失败事务中普通 SQL 不假成功，Sync/ROLLBACK 后协议和事务状态恢复。

### CORE-10 GUC 隔离、提交和回滚

**步骤**

1. 将 transaction 池大小设为 1；客户端甲 `SET statement_timeout='5s'`，乙设为 `10s`，交替执行事务并 SHOW。
2. 事务内 SET 后分别 COMMIT 和 ROLLBACK，再换后端/换事务 SHOW。
3. 执行：

```sql
BEGIN;
SET statement_timeout='5s';
SAVEPOINT s1;
SET statement_timeout='10s';
ROLLBACK TO SAVEPOINT s1;
SHOW statement_timeout;
COMMIT;
SHOW statement_timeout;
```

4. 在 SAVEPOINT 后加 `SELECT 1/0` 再 ROLLBACK TO，重复验证。

**预期**

- 两客户端参数不串值；ROLLBACK 不把被撤销值写入代理缓存。
- 两个 SHOW 最终均对应 5 秒，后端复用后仍一致。

### CORE-11 Simple Query 多语句边界

**步骤**

使用能明确发送单个 Q 报文的驱动/协议工具，分别发送：

```sql
BEGIN; SET statement_timeout='5s'; COMMIT;
BEGIN; SET statement_timeout='10s'; ROLLBACK;
```

再发送一个 Q：先 SET，后跟必然失败的 SQL。最后换后端并 SHOW 参数。

**预期**

- 一个 Q 内多个事务边界不串代；最终回滚的 10 秒不成为正式会话值。
- 后续语句失败时，数据库回滚的参数不能被代理提前提交到缓存。

### CORE-12 Extended Query、全局 PS 和 LRU

**步骤**

1. JDBC 配置为能触发服务端 Prepared Statement，重复执行带参数 SELECT 和 UPDATE，验证 P/B/D/E/S。
2. 两个客户端执行相同 SQL 和不同参数类型，检查全局 PS/服务端 PS SHOW 及结果。
3. 同名同 SQL、同名不同 SQL、未知 statement、未知 portal 分别执行，错误后 Sync，再执行合法请求。
4. 将后端 PS 上限设为 1：先成功部署 A，再 Parse 一个会失败的 B，Sync 后重新执行 A。
5. 测试 62、63、64、128 字节 statement 名，Parse 失败后以同名正确 SQL 重试。

**预期**

- 响应顺序与 PostgreSQL 一致，错误只影响当前 Extended 周期，Sync 后恢复。
- LRU 前序错误后无同名残留/duplicate_prepared_statement；长名称清理不截断或误删。
- PS 计数和引用无持续增长，内部 CloseComplete 不发送给客户端。

## 四、监控、高可用、控制台和 Reload

### CORE-13 monitor 故障和恢复确认

**步骤**

1. 记录 monitor_period、monitor_retry_period_ms、失败/恢复次数及 timeout。
2. 停止 A1 数据库，持续轮询 SHOW CLUSTERS/GROUP_ROUTING，记录状态变化时间。
3. 在确认故障前恢复一次，验证连续失败计数是否重置；再持续故障直到确认。
4. 启动 A1，先制造一次成功后再次失败，再持续成功直到确认恢复。

**预期**

- 故障/恢复必须达到连续次数，快速复探和稳定周期符合配置。
- 节点未确认恢复前不重新进入读候选；单节点故障不阻塞其他 cluster 探测。

### CORE-14 复制组备机故障

**步骤**

1. qa_rep 正常读写并留基线。
2. 停止 A1；在故障确认前、确认后分别持续发送读写。
3. 恢复 A1，等待确认恢复，再发送读请求。

**预期**

- A1 故障后写持续使用 A0，读回退 A0；原绑定 A1 的请求可以失败但不假成功。
- A1 恢复并满足门禁后重新承担读。

### CORE-15 复制组主故障但暂不升主

**步骤**

1. 确认 A0/A1 关系此前已可信。
2. 停止 A0，不执行 PARTED、不升主。
3. 持续发送新读请求和新写请求，检查 SHOW CLUSTERS/GROUP_ROUTING。
4. 启动原 A0，确认仍为主并恢复复制后 REFRESH。

**预期**

- 写请求失败；符合“健康 replica＋保留已确认上游关系”的 A1 可在 NO_PRIMARY/UNRESOLVED 下继续读。
- 不保证 A1 已包含旧主最后提交；不得把该例外套用到 MULTI_PRIMARY/MMR。

### CORE-16 复制组主备切换

**步骤**

1. 停止/隔离 A0，并从数据库层禁止旧主继续写。
2. 控制台执行 `SET NODE PARTED A0;`，核对配置文件已持久化。
3. 用已有 HA 流程将 A1 升主。
4. 执行 `REFRESH CLUSTER site_a;`，轮询 SHOW，直到唯一主和路由可信。
5. 发送新写和新读；之后按标准流程将 A0 重建为 A1 的备机，再 ACTIVE＋REFRESH。

**预期**

- 新主未确认前不写；确认后新写使用 A1。
- PARTED 是代理人工隔离，不替代数据库 fencing；旧主不能直接以第二主身份重新上线。
- 恢复后物理角色、有效组角色、数据均正确。

完成该用例后，如果个人后续用例仍以 A0 为主，可按标准流程切回或直接更新个人环境基线为“A1 主、A0 备”，但必须同步修改后续记录中的实际节点，不能靠名称推断角色。

### CORE-17 MMR 写中心切换

**步骤**

1. 确认 qa_mmr 当前写到 site_a，读候选正常。
2. 停止/隔离 site_a 当前 primary，等待 monitor 发布故障。
3. 观察是否按 promoted_cluster 选择 site_b 当前 primary 承担新写。
4. 执行一笔写和多笔读，检查唯一写目标、数据同步和候选。
5. 恢复 site_a 后观察正常优先级重算；另执行一次 `SET NODE WRITE <site_b当前主节点> IN GROUP qa_mmr`，验证人工切换及持久化。

**预期**

- 任一时刻组只有一个有效写目标；写中心选择按 write→promoted 顺序，不按权重随机。
- 故障中的旧事务不自动重放；新事务按新目标执行。
- 指定 IN GROUP 只修改 qa_mmr，不影响 balance/single/replication 组。

### CORE-18 balance/single 节点故障

**步骤**

1. 记录 qa_bal_rw、qa_bal_ro、qa_single_rw、qa_single_ro 正常落点。
2. 停止 B0，验证 balance read_write 重选；恢复后再停止 B1，验证 balance read_only 仍使用 A1。
3. 停止 A1，验证 single_ro 回退 A 当前健康 primary。
4. 恢复所有节点并确认重新进入候选。

**预期**

- balance/single 按各自候选策略切换，无候选时明确报错。
- read_only 是读候选策略，不是数据库写权限；不得据此声称写 SQL必然被拒绝。

### CORE-19 SET NODE 与批量原子性

**步骤**

1. `SET NODE PARTED A1;`，检查 SHOW、业务落点及主配置文件。
2. `SET NODE ACTIVE A1;` 后确认先处于待探测/恢复状态，再 REFRESH 至 READY。
3. 执行合法的批量 PARTED/ACTIVE，并使用重复节点名验证去重。
4. 执行“一个合法节点＋一个不存在节点”的批量命令。
5. 重启 fbasecman，重新核对状态。

**预期**

- 合法命令运行态与磁盘一致，重启后保持。
- 含非法节点的批次整体拒绝，不允许合法节点被部分修改。
- ACTIVE 不直接等于健康，未复探成功前不承担业务。

### CORE-20 WRITE/PROMOTED、REFRESH 和 SHOW

**步骤**

1. 对 qa_mmr 执行合法 `SET NODE PROMOTED` 和 `SET NODE WRITE ... IN GROUP qa_mmr`。
2. 尝试把 replica 设为 WRITE、对 balance/single 执行 WRITE、指定不存在组。
3. 执行 REFRESH CLUSTER，检查返回后实际探测和状态变化。
4. 每阶段核对 SHOW DATASOURCES、CLUSTERS、GROUP_MEMBERS、GROUP_ROUTING、CONFIG_STATUS/MONITOR_CONFIG（以 SHOW HELP 实际支持项为准）。

**预期**

- 合法命令只作用目标 MMR group；非法目标整体拒绝且不落盘。
- REFRESH 成功只表示复探已登记/完成调用，不等于节点一定健康。
- SHOW 能区分物理角色、人工状态、有效组角色和路由候选。

### CORE-21 Reload：无变化、运行参数和结构变化

**步骤**

1. 持续运行短事务；执行无变化 Reload，记录连接、PID、路由和 CONFIG_STATUS。
2. 单独修改日志级别/监控周期，Reload 后验证真实运行行为。
3. 单独修改 `smart_discard` 或 `application_name_add_host`，验证新连接是否按新配置工作。
4. 增加一个测试用户或组后 Reload，新连接使用新规则；保持一个旧长事务，验证其不崩溃、不串组。
5. 删除刚新增对象并 Reload，旧连接退出后检查资源回收。

**预期**

- 无变化不无故断连；运行参数真实生效。
- 单字段变化不能因比较遗漏而继续沿用旧规则。
- 结构变化中新旧连接边界明确，无半发布、悬空引用和串库。

### CORE-22 Reload 和持久化失败保护

**步骤**

仅使用个人专属配置副本和目录：

1. 保存原配置、文件权限、运行态和读写冒烟结果。
2. 制造配置语法错误/不存在 cluster，执行 Reload。
3. 恢复配置，再对控制台持久化目录制造只读或无空间模拟条件；执行 SET NODE。
4. 恢复目录，重新执行合法命令和 Reload。

**预期**

- 解析/校验/落盘失败均返回明确错误；旧配置继续服务。
- 不产生半文件、部分运行态或死锁；恢复条件后下一次操作可成功。
- 操作结束必须恢复权限和磁盘条件，并执行四组冒烟。

## 五、并发、故障饱和和关键回归

### CORE-23 连接池容量、复用和等待

**步骤**

1. 分别使用 session、transaction、statement 用户执行两客户端交错事务，记录后端 PID 复用。
2. 将 pool_size 设为 1；客户端甲占住事务，乙发起请求，验证等待。
3. 分别测试有限 pool_timeout 和兼容的无限等待；释放甲后观察乙。
4. 测试 client_max 边界：达到上限、超过上限、释放后重连。

**预期**

- 池模式复用边界正确，不串事务/结果。
- 有限等待总耗时有界，容量释放后等待者继续；限额不泄漏，错误码明确。

### CORE-24 故障节点建连饱和与 CPU

**步骤**

1. 空载和健康负载各采集 5 分钟 M1 总 CPU、fbasecman 进程/线程 CPU、RSS、fd、吞吐和延迟。
2. 让一个仍可能被选中的备节点发生“端口立即拒绝”，持续并发请求 10 分钟。
3. 恢复后改为网络黑洞/丢包，再持续 10 分钟；记录并发连接、错误批次、CPU 和延迟。
4. 将节点 PARTED 或等待 monitor 屏蔽，观察资源恢复。

**预期**

- 连接超时、建连名额和等待请求有界，无持续 0～1ms 忙轮询导致 CPU 长期异常。
- 屏蔽故障节点后错误和资源占用恢复；不只用日志失败次数推断瞬时并发。
- M1 同机客户端会影响主机 CPU，必须同时看 fbasecman 进程/线程口径。

### CORE-25 客户端断开、Cancel 和 reset

**步骤**

1. 启动大结果/慢查询，在返回过程中断开客户端。
2. 分别让后端静默、持续返回 DataRow但延迟 ReadyForQuery，观察清理总时长。
3. 对另一个长查询发送 Cancel，确认只取消目标请求。
4. 用新客户端复用池中连接并执行简单事务。

**预期**

- 清理有总期限，状态不可信的后端被关闭而不是回池。
- Cancel 不误杀其他连接；新客户端不收到前一客户端残余结果/事务状态。

### CORE-26 协议分片和异常包关键回归

**步骤**

使用专用协议工具，不直接对正式共享环境发危险包：

1. 将合法 P/B/D/E/C/S 报文按报文头后、名称中间和最后一字节前分片发送。
2. 将多个完整报文一次合包发送。
3. Execute 分别构造缺少最大行数、多一个尾字节；构造 Close 缺名称终止符。
4. 发送超长长度头但不继续发送大 body，观察连接和代理进程。
5. 异常连接结束后由正常客户端执行 Simple/Extended 冒烟。

**预期**

- 合法分片与一次发送完整报文结果一致，不因 TCP 分片断开。
- 非法报文安全拒绝、正确 Sync/断连，不崩溃、不零进度忙循环、不影响其他客户端。

### CORE-27 已知高风险缺陷定向回归

在前述用例基础上，集中复验以下未解决或近期修复的核心路径：

| 缺陷方向 | 复验方法 | 通过标准 |
|---|---|---|
| MMR 状态多行/NULL | 使用个人隔离测试视图返回多行、NULL | 不采首行误解除屏蔽，组保持不可路由或明确失败 |
| 保存点/GUC | 重跑 CORE-09～11 | 数据库实际值、代理缓存和下次部署一致 |
| 建连等待和 reset | 重跑 CORE-24～25 | CPU/等待有界，连接所有权和池状态正确 |
| PS 长名/LRU | 重跑 CORE-12 | 错误后无残留、重复名和引用泄漏 |
| Reload 差异/半发布 | 重跑 CORE-21～22 | 单字段生效，失败保持旧运行态 |
| relay 长度及 C/E 分片 | 重跑 CORE-26 | 合法包兼容、非法包安全失败 |

如果当前代码尚未修复某条已知缺陷，预期不能写成“保持当前错误行为”。应记录 FAIL 或经转测评审明确 WAIVED，并列出影响范围和规避条件。

### CORE-28 最终核心回归与恢复验收

**步骤**

1. 恢复所有数据库：site_a/site_b 各为唯一主＋健康备，复制无异常，MMR 状态正常。
2. 恢复所有 datasource ACTIVE、预期权重、qa_mmr write/promoted 和正式候选配置，执行 REFRESH。
3. 重新执行 CORE-02、CORE-05～07、CORE-10、CORE-12、CORE-19～21 的最小冒烟。
4. 运行当前项目已有自动化总入口；逐套件核对执行数、通过、失败和跳过，不能只看总脚本退出码。
5. 汇总 FAIL/BLOCKED/N/A、修复包、复测范围和遗留风险；由另一名人员交叉检查证据。

**预期**

- 最终环境干净、配置可重启、四组核心业务可用。
- 所有核心 FAIL 有结论；补丁合入后重跑关联用例和 CORE-28，而不是沿用旧包结果。
