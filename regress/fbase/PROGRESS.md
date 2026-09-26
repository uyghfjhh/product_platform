# 等保转测用例进度

更新日期：2026-07-15。权威映射位于 `suites/mac/document_coverage.py`；空映射表示尚未实现，不能视为通过。已确认的文档与产品差异见 `问题记录.md`。

## 当前结论

当前 MAC suite 共注册 58 个用例，已映射 99 个转测点中的 92 个。剩余 7 个点不是遗漏：

| 文档 | 章节 | 不能实现为独立自动化用例的原因 |
| --- | --- | --- |
| 三权分立功能转测.md | 5.1.4、5.2.4、5.4.2、5.4.3、5.4.4、5.5 | 章节内容为空，或只写“见前文”而没有独立前置条件、SQL 步骤和预期结果；复用前文会重复已有用例，不能伪造新场景。 |
| 安可密码和验证失效需求(陈群友).md | 五、账户多重验证机制 | 文档仅列出“操作系统身份验证、LDAP 验证、KERBEROS 验证”，没有 HBA 规则、LDAP URI/BASE/用户或 Kerberos realm/KDC/主体。当前环境无 LDAP/Kerberos 服务端与对应 HBA 配置，不能猜测认证参数。 |

文档已覆盖的场景中，如产品实际结果与文档不一致，用例仍按文档 SQL 和期望执行，并以 FAILED 保存证据；当前差异见 `问题记录.md`。这些不是未实现用例。

## 已完成并验证

| 文档 | 已覆盖范围 | 当前结果 |
| --- | --- | --- |
| 三权分立 | 5.1.1-5.3.4.12、5.4.1.1-5.4.1.2 中有实际 SQL 流程的章节 | 已实现；`5.3.1.1` 保留 FAILED，见“产品问题” |
| 强制访问控制 | 2.2.1-2.2.15 | 已实现并目标运行验证 |
| 审计 | 5.1-5.6 | 已实现并目标运行验证 |
| 密码与认证 | 密码复杂度、密码更换周期、失败锁定/SSO 解锁、成功登录回显、日志密码隐藏、账户重命名 | 已实现并目标运行验证 |
| 商密 | 1.1.1 | 隔离实例验证复制 license 启动、`pg_ctl -L` 自动复制/后续重启、扩展查询和两种重载 |
| 商密 | 1.2 | 隔离实例完成 SM3 三种初始化路径、SM3 摘要存储和正反认证验证；FDD 构建禁用 `\password`，经确认以其服务端等价 SQL `ALTER USER` 验证存储结果 |
| 商密 | 1.3 | 隔离 SM4 集群验证密钥权限、初始化、三种启动方式、错误密钥拒绝与加密页存储 |
| 透明加密 | 1.1、1.3、1.4 | 隔离 RC4 集群通过 |
| GB18030 | 一、二、三 | 隔离 GB18030 数据库通过 |
| 故障转移槽 | 2.2-2.4、3.1-3.3 | 创建、查看、删除及主备 WAL 重放通过 |
| 故障转移槽 | 3.3.3 | 已目标验证：专属 failover 槽在备库延迟重放期间订阅端为 0，备库重放后订阅端为 1；不复用环境全表订阅 |
| TLCP | 2.3.1-2.3.3 | CA、服务端/客户端双证书生成和元数据检查通过，均事务回滚 |
| TLCP | 2.3.5-2.3.9 | 会话级临时 fbase_mac 实例验证续期、备份、撤销、归档和活动表清理；归档后恢复的文档预期错误 D-008 以产品实际失败及活动表为空断言，case 为 SUCCESS |
| TLCP | 2.3.4、2.3.10 | 全新 source/target 隔离集群完成 CA 和服务端双证书导出/SSO 导入，保留文件权限和元数据证据 |
| TLCP | 2.5 | 隔离集群验证 TLCP UDF 成功与失败均以 MAC/FUNCTION 写入审计记录 |
| TLCP | 2.4.1 | 隔离实例生成并导出 TLCP CA、服务端/客户端双证书，按文档配置 `enable_tlcp`、双证书和 `hostssl cert` 后验证 TLCP 双向认证 |
| TLCP | 2.4.2 | SSL 双向认证、TLSv1.3 cipher、SSO 吊销、CRL 导出和被吊销客户端证书拒绝均已通过 |

## 已验证产品问题

### 三权分立 5.3.1.1

`mac.separation_of_duties.dba_user_management_separation_off` 故意保持 FAILED。
文档要求除无密码时设置初始密码外，DBA 不能对 SSO/SAO 执行任何 `ALTER`；当前产品在
`fdb.separate_user=off` 且 SSO/SAO 无密码时放行 `ALTER USER ... CREATEROLE`。用例在
`BEGIN/ROLLBACK` 中执行，报告保留期望、实际和文档违反说明，不改变账户属性。

### 透明加密 1.2

`tde_encrypt` 和 `encryption_key_command` 是 `PGC_POSTMASTER` 参数。文档称 `ALTER SYSTEM`
后 reload 可动态切换；隔离 RC4 集群实测 reload 后 `SHOW tde_encrypt` 仍为 `rc4`。
`mac.tde.dynamic_switch_document_requirement` 保留 FAILED 作为文档符合性证据。

### TLCP 2.3.8、2.3.9

归档实现会删除 `_bak` 记录，而恢复 UDF 只能从 `_bak` 读取，导致文档规定的归档后恢复必然失败。
`mac.tlcp.certificate_lifecycle` 以临时实例复现该差异并按实际失败/活动表为空断言，结果为 SUCCESS；
共享 MAC 证书库不再作为前置条件，详见 `等保问题.md`。

### GB18030 4.2

已按 `Third-party/zhparser-2.2/README.md` 编译安装 `scws-1.2.3` 和 zhparser 2.2 到
`/usr/local/fbase15.15`；`CREATE EXTENSION zhparser` 及
`mac.gb18030.full_text_search`（`run_20260718_094659_13b7f4`）均为 SUCCESS。

## 当前环境问题

### TLCP 证书元数据

TLCP 生成 UDF 要求超级用户，`backup_certificate`、`abort_certificate`、`archive_certificate`
和 `restore_certificate` 要求 SSO，生命周期必须跨会话持久化。转测文档 2.3.6 至 2.3.9
规定备份和归档表保留历史记录；`certificate_store_empty` 因而只检查活动表
`certs_info`、`key_meta_data`。生命周期用例在恢复验证后按文档撤销 CA 并归档，最终断言
活动元数据已清空；不对受 ACL 保护的备份/归档表执行 `DELETE`。

### 逻辑订阅

`fbase_regress_sub` 的 apply worker 持续因 `fdb_mac.table_policy` 重复键失败，导致发布端
逻辑槽未激活。故障转移槽 3.3.3 的延迟 commit 需要活跃逻辑订阅、failover 槽确认 LSN 和同步
备库处理进度，当前共享环境不能有效验证。

### failover 槽状态更新入口

`fbase_mac.so` 仍导出 `fdb_set_slot_failover`；其 SQL 定义在 `fbase_mac--1.0.sql`，当前
2.0 安装的升级脚本未注册该函数，数据库目录也不存在该 SQL 入口。该入口缺失是产品安装/升级问题；
测试框架不得创建函数掩盖问题。状态更新分支应在产品修复注册后直接调用并验证。

## 剩余待点

| 文档 | 待点 | 需要条件 |
| --- | --- | --- |
| 三权分立 | 5.1.4、5.2.4、5.4.2-5.5 | 空章节或“见前文”引用，不重复伪造用例 |
| 密码与认证 | 五 | LDAP、Kerberos 或 OS 认证 |

## 验证要求

每个新增 case 必须：映射到 `suites/mac/document_coverage.py`，在目标环境运行，核对
`report.txt` 的 SQL、预期、实际、失败原因和日志证据，并执行：

```bash
python3 -m unittest discover -s unit_tests -v
git diff --check
```

---

# 多活转测用例进度

> 2026-07-20 审查更新：MMR suite 当前发现 170 个 case。`多活功能测试文档.md` 与
> `多活streaming冲突处理测试文档.md` 的 187 个登记点均已映射；功能文档的 `9.4.2` 是对后者的
> 交叉引用、`12` 为限制说明，均以有理由的显式豁免登记。新增 MMR 覆盖单元测试会校验 case、文档和
> 章节三者一致，不能再只依赖 MAC 的覆盖测试。
>
> 本轮按 `mmr-autotest` 增补了三个独立的 `insert_exists` 回归：SAVEPOINT 回滚不发出冲突行、10 个
> 并发冲突事务，以及 3 个 2PC 提交/3 个回滚/3 个普通提交/3 条正常行的混合流，均已实跑 SUCCESS。
> 审计现在覆盖 mmr-autotest 实际发现的全部 20 个测试模块，19 个有 case 映射，1 个仅测试其自身
> 框架辅助函数的 multi-node demo 有明确豁免。2.5.1/2.5.3/2.5.4 已改为验证 streaming 分片下的稳定
> 数据语义和真实冲突记录；文档固定要求 512 条历史不是产品契约，已登记为 D-044 文档问题。
> 文档第 1 节要求的发布端/订阅端 immediate、buffered 四种组合由
> `mmr.streaming_conflict.configuration_matrix` 实测覆盖，四组 513 行 delete_missing/skip 均为 `0|1`。

更新日期：2026-07-17。权威映射位于 `suites/mmr/document_coverage.py`。多活功能测试文档
已登记 100 个功能测试点：当前 75 个 case 已映射 93 个点，均已经目标运行。另有 7 个空映射
待办，不能视为已完成；streaming 冲突处理文档已开始迁移。

## 运行环境

`regress.yaml` 只保留一个常驻多活环境 `mmr`，当前可发现 81 个多活 case。用户入口固定为：

```bash
./run.sh run mmr
```

原 `mmr_forwarding`、`mmr_streaming`、`mmr_global_sequence` 不再是命令行环境。普通场景在
共享 `mmr` 上临时调整 GUC、streaming 或 failover 并恢复；需要特定 `two_phase` 拓扑的场景使用
case 自己的 `/tmp/fbase_regress_*` 临时节点，不新增 YAML 环境。

单环境全量验收已于 2026-07-17 完成：
`./run.sh run mmr` 的 run_id 为 `run_20260717_164527_c5f957`，生成 81/81 个 case
报告、`summary.json` 和 `junit.xml`，结果为 SUCCESS=59、FAILED=19、BLOCKED=3。FAILED/BLOCKED
均保留文档原步骤或产品限制的真实证据，不是额外环境缺失；运行结束后执行
`./run.sh env status mmr`，三 MMR 主节点和三物理备库均为 healthy。`streaming_toggle`
fixture 现会等待 fddoutput 订阅/槽收敛，避免后续用例因短暂恢复状态被连锁 BLOCKED。

Streaming 冲突文档已继续覆盖 `2.1.1`、`2.1.2`、`2.1.3`、`2.2.1`、`2.2.2`、`2.2.3`、
`2.2.4`：`delete_missing` 的 skip/error/skip_transaction，以及 `update_missing` 的
insert_or_skip/error/skip/insert_or_error，均在独立 two_phase 临时拓扑实际 SUCCESS。前置中的
建表和加表必须是两次独立 psql 命令；合并到同一 `psql -c` 会让 UDF 的本机连接看不到未提交
relation OID，已修正。

尚未将冲突策略制作成通用参数化 factory。文档的非 2PC 与 2PC 事务模型不同，且标准同构表、
列缺失、目标表缺失、三节点多唯一键和双端并发等章节的前态与依赖也不同。后续先按这些稳定
前态分组逐项验证，再仅复用共同的临时拓扑和前置，保留每个策略独立 case、SQL 和报告。

## 已完成并目标验证

| 文档 | 章节 | 用例 | 当前结果 |
| --- | --- | --- | --- |
| 多活功能测试文档.md | 1.1、1.2 | `mmr.installation.runtime_prerequisites` | SUCCESS；两成员实际验证 fdd_mmr/fb_license 扩展、强制启动参数、推荐复制/日志参数、loopback trust HBA 和集群健康；仅只读检查，隔离环境以 loopback 代替文档的通配监听 |
| 多活功能测试文档.md | 2.1 | `mmr.node_management.create_node` | SUCCESS；两套隔离实例覆盖默认 create_node 后的本地/节点元数据，以及 70 字符节点名截断为前 63 字符；自动清理后无临时目录残留 |
| 多活功能测试文档.md | 2.2 测试一、测试二、测试三 | `mmr.node_management.create_group` | SUCCESS；隔离实例验证创建组后的默认复制集与节点 ACTIVE 状态、第二次建组按文档报错、长组名及默认复制集截断；自动清理后无临时目录残留 |
| 多活功能测试文档.md | 2.3.1 | `[LONG-TIME] mmr.node_management.join_group` | FAILED [D-017]；`run_20260718_041904_677c80` 中单表 data-only 冲突 worker 每约 5 秒重复失败并重启，180 秒内不返回文档要求的 5 次失败；none 重入再超时。默认全量跳过，显式指定 case 手动执行 |
| 多活功能测试文档.md | 2.4 force=true 活跃节点、force=false 活跃节点 | `mmr.node_management.part_node_online` | SUCCESS；三节点双向复制确认后，在线成员以两种 force 参数分离均成功，发起者、观察成员和被分离节点均验证 PARTED；专属实例以正式 `fdd.part_catchup_timeout=60s` 允许槽确认完成 |
| 多活功能测试文档.md | 2.4 force=true 停机节点、force=false 停机节点 | `mmr.node_management.part_node_offline` | SUCCESS；force=true 远端分离后恢复节点先显示 ACTIVE、再由本地分离为 PARTED；force=false 返回连接失败且所有节点保持 ACTIVE；专属实例自动清理 |
| 多活功能测试文档.md | 2.5 | `mmr.node_management.drop_node` | SUCCESS；三端复制确认后先将 node1 分离，再调用 drop_node(true) 删除；两个保留成员均不再存在 node1 元数据且保持 ACTIVE |
| 多活功能测试文档.md | 2.6 | `mmr.node_management.wait_for_join_completion` | SUCCESS；真实异步 join 输出 CREATED、JOIN_START、DATASYNC、CATCHUP、ACTIVE 并返回 success；完成后再次调用返回 failed |
| 多活功能测试文档.md | 4.1.1 | `mmr.conflict.update_missing` | SUCCESS；隔离 schema-only join 使目标表缺失初始数据，源端更新后验证默认 `insert_or_skip` 插入远端行及冲突历史 |
| 多活功能测试文档.md | 4.1.2 | `mmr.conflict.delete_missing` | SUCCESS；独立重建 4.1.1 前置后删除目标缺失行，验证默认 `skip` 保留目标现有数据及冲突历史 |
| 多活功能测试文档.md | 4.1.3 | `mmr.conflict.insert_exists` | SUCCESS；隔离 schema-only 场景中目标端插入已存在主键，验证源端按默认 `update_if_newer` 更新并记录远端应用 |
| 多活功能测试文档.md | 4.1.4 | `mmr.conflict.update_pkey_exists` | SUCCESS；按文档使用 `run_on_all_nodes` 两端同时更新主键，验证默认 `update_if_newer`、冲突记录及最终 id=1000 数据 |
| 多活功能测试文档.md | 4.1.5、4.1.6 | `mmr.conflict.asymmetric_columns` | SUCCESS；异构列私有复制集验证 `target_column_missing/ignore_if_null` 和 `source_column_missing/use_default_value`，并按文档补齐目标端 city 列 |
| 多活功能测试文档.md | 4.1.7 | `mmr.conflict.target_table_missing` | SUCCESS；默认复制集表仅从源端删除，目标端写入后验证 `target_table_missing/skip_if_recently_dropped`，目标本地数据保持存在 |
| 多活功能测试文档.md | 4.1.8 | `mmr.conflict.multiple_unique_conflicts` | SUCCESS；三节点隔离环境中 schema-only node136 写入同时违反两个唯一约束，验证 `multiple_unique_conflicts` 记录及 node134/node135 数据不变 |
| 多活功能测试文档.md | 4.1.9 | `mmr.conflict.update_recently_deleted` | SUCCESS；按 `mmr-autotest` 固化 node135 DELETE 未提交、node134 UPDATE 完成、等待一秒后 node135 COMMIT 的因果顺序，验证 `update_recently_deleted/skip` 和目标端仅保留 id=2；原文时序缺失记为 D-037 文档错误 |
| 多活功能测试文档.md | 4.1.10 | `mmr.conflict.update_origin_change` | SUCCESS；三节点场景中 node136 先接收 node134 的 pp、再接收 node135 的 hh，验证 `update_origin_change/update_if_newer` 记录及最终远端值 |
| 多活功能测试文档.md | 4.1.11 | `mmr.conflict.delete_recently_updated` | SUCCESS；两独立 psql 受控并发令 node134 UPDATE 保持、node135 DELETE 先提交，验证 `delete_recently_updated/skip` 冲突记录 |
| 多活功能测试文档.md | 4.2 本地配置、指定节点配置、全节点配置 | `mmr.conflict.resolver_configuration` | SUCCESS；隔离两节点验证本地 `insert_exists=error` 的实际冲突、指定 node135 的 `update_origin_change=error`、全节点 `update_missing=skip` 及各自配置视图 |
| 多活功能测试文档.md | 4.3.1.1 | `mmr.conflict.update_insert_order` | SUCCESS；受控双事务验证 A 更新主键先提交、B 插入后提交，A/B 分别记录 `insert_exists`、`update_pkey_exists`，最终数据按文档不一致 |
| 多活功能测试文档.md | 4.3.1.2 | `mmr.conflict.insert_update_order` | SUCCESS；两个独立表覆盖初始 id=11 分别由 A、B 写入时的 A 插入先提交/B 更新后提交，按文档验证两端最终均收敛为 id=33,jone |
| 多活功能测试文档.md | 4.3.1.3 | `mmr.conflict.simultaneous_insert_update` | SUCCESS；四张隔离表完整覆盖四个初始归属和提交顺序场景；场景二、四中后提交 UPDATE 依 `update_if_newer` 收敛为 id=33,jone，冲突记录的 apply_tuple 已按源码和实跑更正，原文预期记为 D-039 文档错误 |
| 多活功能测试文档.md | 4.3.2.1 | `mmr.conflict.update_update_primary_key` | SUCCESS；三张隔离表覆盖主键改为相同值、主键改为不同值及非主键列并发更新，分别验证 update_pkey_exists、update_missing、update_origin_change 与两端最终数据 |
| 多活功能测试文档.md | 4.3.2.2 | `mmr.conflict.update_update_unique_identity` | SUCCESS；五张隔离表覆盖唯一索引复制标识下的无主键三种场景和有主键两种场景，验证 update_origin_change、update_missing、两端冲突记录及最终收敛数据 |
| 多活功能测试文档.md | 4.3.3.1 | `mmr.conflict.update_update_identity_full_no_pk` | SUCCESS；无主键 FULL 复制标识表的两条重复初始行并发更新后，两端各产生两条 update_missing/insert_or_skip，最终均保留 A_new、B 各两行 |
| 多活功能测试文档.md | 4.3.3.2 | `mmr.conflict.update_update_identity_full_pk` | SUCCESS；三张有主键 FULL 复制标识表覆盖同主键、不同主键和非主键并发更新，验证 update_pkey_exists、update_missing、update_origin_change 与最终数据 |
| 多活功能测试文档.md | 4.3.4.1、4.3.4.2 | `mmr.conflict.update_delete` | SUCCESS；四个 UPDATE-DELETE 场景均覆盖受控提交顺序；20ms 场景的 A/B 结果依 `delete_missing/skip`、`update_recently_deleted/skip` 收敛为 A=id=33、B=0 行，原文节点归属记为 D-040 文档错误 |
| 多活功能测试文档.md | 4.4 | `mmr.conflict.log_configuration` | SUCCESS；验证默认冲突记录、本地按类型和处理结果过滤、允许 update_missing 的历史表及服务端日志、远端 node135 配置同步与 NULL 全节点默认恢复 |
| 多活功能测试文档.md | 7.2、7.3 | `mmr.failover_slot.physical_failover_interface` | SUCCESS；隔离 node134 主库/物理备库/node135 拓扑实际执行 basebackup、旧主停止、新备 promote、两端 DSN 更新和新主 DML 复制，验证 fddoutput 故障转移槽保留 failover 标记 |
| 多活功能测试文档.md | 2.3.2.1 测试一 | `mmr.node_management.online_join_all` | SUCCESS；复用文档 31 表、超 8KB 字段和 63 字节长表，all join 期间持续 20 次业务写入，源端与 node135 最终一致 |
| 多活功能测试文档.md | 2.3.2.1 测试三 | `mmr.node_management.online_join_data_only` | SUCCESS；候选端预建文档 31 表后以 data-only 在线 join，持续业务写入后源端与 node137 的存量、长字段和更新值一致 |
| 多活功能测试文档.md | 2.3.2.1 测试四 | `[LONG-TIME] mmr.node_management.online_join_data_retry` | FAILED [D-017]；`run_20260718_022456_e29b2a` 在 64 槽容量下完成产品 5 次冲突检测并返回失败，但遗留 32 条 `d` 状态订阅关系映射；原文 DROP 被拒绝，data-only 重试超时。默认全量跳过，显式指定 case 手动执行 |
| 多活功能测试文档.md | 2.3.2.1 测试二 | `mmr.node_management.online_join_all_retry` | SUCCESS；`run_20260718_021052_da8dc7` 显示候选端仅有同构表时 all join 直接成功，在线负载结束后两端代表表一致；原文“必然中断、删表重入”是 D-043 文档错误 |
| 多活功能测试文档.md | 11.1、11.2、11.3.1、11.3.2 | `mmr.installation.license_lifecycle` | SUCCESS；临时实例验证 pg_ctl -L、默认 license 再启动、预警阈值 reload、get_license_info 与 sys_reload_license |
| 多活功能测试文档.md | 5.1.1 | `mmr.cluster_verification.basic` | SUCCESS |
| 多活功能测试文档.md | 5.1.2 | `mmr.cluster_verification.connection_failure_priority`、`mmr.cluster_verification.same_priority_errors`、`mmr.cluster_verification.subscription_existence_priority` | SUCCESS；覆盖同级状态/版本错误并列、连接失败阻断低优先级、订阅不存在阻断订阅属性检查；破坏性 DROP 场景在专属集群重建后 healthy |
| 多活功能测试文档.md | 5.1.3 | `mmr.cluster_verification.node_state` | SUCCESS；fixture 恢复节点元数据状态 |
| 多活功能测试文档.md | 5.1.4 | `mmr.cluster_verification.time_difference` | SUCCESS；stream2 以 faketime 偏移 5 秒，验证最小阈值 TIME_DIFF、`time_diff=-1` 禁用、恢复后重检及元数据错误优先级；真实时钟重启后订阅 worker/槽/集群均 healthy |
| 多活功能测试文档.md | 6 | `mmr.background.maintenance_lifecycle` | SUCCESS；隔离实例验证仅 supervisor、扩展创建后不立即启动 maintenance daemon、create_node 后启动及 part_node 后退出 |
| 多活功能测试文档.md | 5.2 测试二 | `mmr.cluster_verification.check_node_conf_failover_exclusion` | SUCCESS |
| 多活功能测试文档.md | 7.1 | `mmr.failover_slot.dynamic_failover` | SUCCESS；fixture 恢复 failover/槽状态 |
| 多活功能测试文档.md | 8.1、8.2 | `mmr.subscription_control.enable_disable` | SUCCESS；指定/全部订阅启停、幂等与元数据/运行时联动 |
| 多活功能测试文档.md | 3.2、3.3 | `mmr.replication_set.private_lifecycle` | SUCCESS；私有复制集创建、属性修改、删除 |
| 多活功能测试文档.md | 3.4 | `mmr.replication_set.subscription_sets` | SUCCESS；修改本节点 `sub_repsets`，并通过产品 UDF 恢复原订阅数组和异步状态 |
| 多活功能测试文档.md | 3.5 测试一、测试二 | `mmr.replication_set.auto_table_binding` | SUCCESS；已有表 `autoadd_existing`、新表 `autoadd_tables`、等待状态和不回溯绑定均已验证 |
| 多活功能测试文档.md | 3.5 测试四 | `mmr.replication_set.schema_table_binding` | SUCCESS；三成员同构 schema 的两张表批量绑定，映射状态为 `w` |
| 多活功能测试文档.md | 3.6 测试二、3.7 | `mmr.replication_set.synchronous_removal` | SUCCESS；`run_20260718_205249_65be64` 在专属 two_phase=false 双节点拓扑完成异步纳表、状态收敛和同步移除，临时订阅及复制槽已清理 |
| 多活功能测试文档.md | 9.7 | `mmr.default_publication.schema_filtering` | SUCCESS；`run_20260718_205302_b3dcdd` 在专属 two_phase=false 双节点拓扑完成默认复制集异步刷新、schema 排除和订阅端不再接收过滤表的新行 |
| 多活功能测试文档.md | 9.6.2.5 | `mmr.global_sequence.invalid_metadata_cleanup` | SUCCESS；手工删序列后的无效 OID 元数据清理、重建和全局删除均已验证 |
| 多活功能测试文档.md | 9.6.3.1 | `mmr.global_sequence.metadata_consistency` | SUCCESS；全局序列不一致检测、无效 OID 清理和 DSN 修改均已验证；文档 DSN 原字符串不同但解析键值相同，自动化以末尾空白正确复现该输入 |
| 多活功能测试文档.md | 9.5 测试一 | `mmr.node_function_control.streaming_toggle` | SUCCESS；覆盖本地、全局 streaming 及三个成员订阅 `substream` 联动 |
| 多活功能测试文档.md | 9.5 测试二 | `mmr.node_function_control.local_failover_toggle` | SUCCESS；覆盖 `alter_node_info` 本地元数据与目标节点槽联动 |
| 多活功能测试文档.md | 9.5 测试三 | `mmr.node_function_control.two_phase_change_unsupported` | SUCCESS |
| 多活功能测试文档.md | 9.6.1.1 | `mmr.global_sequence.snowflake_nextval` | SUCCESS |
| 多活功能测试文档.md | 9.6.1.2 | `mmr.global_sequence.snowflake_conversion` | SUCCESS；普通 `bigserial` 转换为雪花默认值并恢复 `nextval` |
| 多活功能测试文档.md | 9.6.2.2 | `mmr.global_sequence.increment_offset_deletion` | SUCCESS；删除元数据并验证三成员增量复位为 1 |
| 多活功能测试文档.md | 9.6.2.3 测试一、测试三 | `mmr.global_sequence.increment_offset_settings` | SUCCESS；覆盖小于 ACTIVE 节点数拒绝、预分配、误设更大 node_count 后恢复原步长，以及最终 `seq_state=d` |
| 多活功能测试文档.md | 9.6.2.3 测试二 | `mmr.global_sequence.increment_offset_node_id` | SUCCESS；指定普通 node_id 更新 node_maximum、强制分离/删除 gseq3 后记录 force_nodeid、再次指定该 node_id 更新后移除强制标记；专用三节点环境自动重建 |
| 多活功能测试文档.md | 9.6.3.2 | `mmr.global_sequence.join_behavior` | SUCCESS；隔离三实例完整覆盖 force_nodeid 非空时 join 拒绝、JOIN_START 清理、补充强制节点序列值、重新 join 和新 node_id 序列元数据分配 |
| 多活功能测试文档.md | 9.6.3.3 | `mmr.global_sequence.nonforce_part_metadata` | SUCCESS；隔离两节点环境验证非强制分离时，被分离节点五次取值后的实际 `last_value=10` 被存活节点写入对应 `node_maximum`；强制分离标记、增删改刷与清理分别由已验证的全局序列用例覆盖 |
| 多活功能测试文档.md | 9.6.2.4 测试一、测试二 | `mmr.global_sequence.increment_offset_refresh` | SUCCESS；覆盖异步转换、指定/全量刷新、node_count 设置、刷新前后元数据状态和三个成员真实序列步长；全量刷新前检查无既有全局序列 |
| 多活功能测试文档.md | 9.6.2.1 测试二 | `mmr.global_sequence.increment_offset_schema_names` | SUCCESS；不同 schema 同名序列分别转换、删除元数据并清理 |
| 多活功能测试文档.md | 9.3 | `mmr.two_phase.transaction_commit` | SUCCESS；严格按两端先建表、node134 建组并插入基线、node135 all 加入的时序验证。目标端 two-phase enabled 后可见转换后的 `pg_gid_<suboid>_<xid>` 预备事务，提交后两端均为 id=1、2；旧 D-011 为自动化漏纳表且错误比较源端 GID，已移除 |
| 多活功能测试文档.md | 9.1 | `mmr.remote_sql.command_and_all_nodes` | SUCCESS；指定节点和所有成员建表、插入、查询、删除已目标验证 |
| 多活功能测试文档.md | 9.2 | `mmr.forwarding.ordinary_logical` | SUCCESS；外部普通发布端 C 的数据默认不转发，配置 `alter_forward_subs` 后经 MMR 转发，清空配置后停止转发；22 个文档步骤均保留真实 psql 证据，专属环境清理后 healthy |
| 多活功能测试文档.md | 9.6.2.1 测试一 | `mmr.global_sequence.increment_offset_conversion` | SUCCESS；三成员创建、`add_global_seq(...,3,true)`、元数据、步长和初始偏移校验已目标验证 |
| 多活功能测试文档.md | 9.4.1 | `mmr.streaming.default_publication_preparation` | FAILED [D-012]；`run_20260718_014225_c793a9` 严格完成原文的建组前 stream_test、建组和 schema-only 加入后，再按 9.4 共同要求创建同构表并异步刷新；默认 g1 copy-data=false 仍被硬编码 copy-data 限制拒绝 |
| 多活功能测试文档.md | 9.4.1.1 | `mmr.streaming.buffered_native_parallel` | SUCCESS；原文已在进入 buffered 小节前将发布端设为 64kB，64 行 1KiB 事务真实跨阈值；普通提交和 PREPARE 前后均没有本轮订阅 OID 的 `changes.0`，提交后两批 64 行均到达。旧 D-013 扫描整个目录而误匹配其它 MMR 订阅文件，已移除 |
| 多活功能测试文档.md | 9.4.1.2 | `mmr.streaming.immediate_native_parallel` | SUCCESS；immediate 下普通 PG streaming=parallel 事务中产生 changes 临时文件，提交后数据到达且专属对象完成清理 |
| 多活功能测试文档.md | 10 | `mmr.installation.uninstall_guard` | SUCCESS；独立双节点环境验证运行中节点拒绝卸载、`part_node` 分离、`drop_node` 删除后允许卸载，并确认目标端扩展目录记录已消失 |

## 已确认差异与历史问题

- `5.2 测试一`、`3.6 测试二、3.7` 和 `9.7`：常驻三成员 `two_phase=true` 会触发 D-012；其中 `3.6 测试二、3.7` 与 `9.7` 的功能用例已移至专属 two_phase=false 拓扑，并于 2026-07-18 实测 SUCCESS。9.4.1 严格保留文档要求的 two_phase=true 场景，因此继续作为 D-012 的 FAIL 证据。
- `3.1`：默认全局复制集要求所有成员 `two_phase=false`；原文未声明此前提，已记录为 D-041 文档错误。`mmr.replication_set.global_default_document_requirement` 在隔离 two_phase=false 拓扑中 SUCCESS。
- `9.3`：`run_20260718_011422_53d198` 证明远端 two-phase apply 正常工作；源码以 `pg_gid_<suboid>_<xid>` 重写远端 GID，旧 D-011 由自动化未按建组时序纳表且直接比较源端 GID 造成，已排除且不记为文档错误。
- `9.4.1.1`：`run_20260718_012027_49bc9c` 以专属订阅 OID 检查临时文件后符合文档；原文在此小节前已设为 64kB，旧 D-013 未过滤订阅 OID，误把其它 MMR 订阅的文件计入，已排除且不记为文档错误。

## 后续范围

历史上未登记点为 9.4.2 和 12；2026-07-20 已分别登记为跨文档引用和非执行性限制说明的显式豁免。streaming

| 多活功能测试文档.md | 2.3.2.2 测试二 | `mmr.node_management.multi_database_three_node_join` | FAIL [D-046]；原文 C 长库名交叉 DSN、db2 的 `node2` 名称错误仍以 D-018、D-019 文档问题覆盖；修正后四库 join 与分离均成功，但 node135 分离后的 `show_node_info(true,false)` 会在 node134/node136 间递归远端检查并超时，产品问题见多活问题 D-046。 |

| 多活功能测试文档.md | 9.8.1 | `mmr.node_management.physical_to_logical_join` | SUCCESS；临时物理备库停止后真实执行 `fdd_mmr_join`，按文档重新启动，验证逻辑订阅、逻辑槽、提升和转换后业务复制。 |
| 多活功能测试文档.md | 9.8.2 | `mmr.node_management.physical_to_mmr_join` | SUCCESS；验证 `-M node37 -L <node137 DSN> -A` 自动加入。启动后先展示瞬时订阅/槽状态，再等待追增完成，三成员集群校验收敛为 OK 并验证业务复制。 |
| 多活功能测试文档.md | 9.8.3 | `mmr.node_management.multi_database_physical_to_mmr_join` | SUCCESS；`postgres,test` 两库真实传入两个 `-d/-M/-L` 参数，两个组各自收敛为三成员 ACTIVE/OK，两个数据库均验证转换后业务复制。 |
## 多活 streaming 冲突处理进度

按 `多活streaming冲突处理测试文档.md` 逐项迁移，所有用例使用独立临时节点，报告仅保留
节点/GUC 配置及文档业务 SQL 的 psql 输出、预期、实际和判定，避免上一场景的数据或冲突历史
影响结果。

| 章节 | 覆盖用例 | 当前结果 |
| --- | --- | --- |
| 2.1-2.11（非 2PC） | delete_missing、update_missing、insert_exists、update_pkey_exists、update_recently_deleted、delete_recently_updated、异构列/表、多唯一键、update_origin_change 的文档 resolver 组合 | 已实现并逐项运行；2.3.2 已按 error 回滚整笔 apply 事务的源码语义修正为 SUCCESS，2.10.2 的 skip 重试会写入 99 个非冲突行，`2|2` 预期已更正为 `101|2` 并记为 D-038 文档错误；2.5.1/2.5.3/2.5.4 的固定 512 条 update_recently_deleted 历史预期改为产品实际的分片语义，D-044 文档问题 |
| 3.1（2PC delete_missing） | `two_phase_delete_missing_{skip,error,skip_transaction}` | 全部 SUCCESS；3.1.2 的文档三条 error 历史已更正为源码定义的两次 streaming PREPARE 各一条，3.1.3 在该前态上也已复跑通过 |
| 3.2（2PC update_missing） | `two_phase_update_missing_{insert_or_skip,error,skip,insert_or_error,skip_transaction}` | 全部 SUCCESS；3.2.2 的文档 2/4 条 error 历史已更正为源码定义的每次 streaming PREPARE 一条，即 rollback=1、commit 累计=2 |
| 3.3（2PC insert_exists） | `two_phase_insert_exists_{update_if_newer,error,skip,update,skip_transaction}`、`two_phase_insert_exists_mixed_transactions` | 全部 SUCCESS；3.3.2 的 error 按源码回滚=1、提交后 `0|1|2`，D-024 已归为文档错误；3.3.5 补齐 PREPARE 同步后验证 skip_transaction 的 `0|1|2`；mmr-autotest 的 3+3+3+3 混合流实测 `6|3|6` |
| 3.4（2PC update_pkey_exists） | `two_phase_update_pkey_exists_{update_if_newer,error,skip,update,skip_transaction}` | 全部 SUCCESS；每种 resolver 均验证双端并发 PREPARE、rollback 后 `512|0|历史数` 以及 commit 后 `0|512|历史数`，其中 error/skip_transaction 使用 GID 前缀历史查询 |
| 3.5（2PC update_recently_deleted） | `two_phase_update_recently_deleted_{skip,error,insert_or_skip,insert_or_error,skip_transaction}` | 全部 SUCCESS；3.5.1、3.5.3、3.5.4 依照 `mmr-autotest` 固化为“node135 DELETE 未提交 -> node134 UPDATE 完成 -> 等待 1 秒 -> node135 完成 DELETE”的因果顺序后，分别验证 `skip=0|512`、`insert_or_skip=512|512`、`insert_or_error=512|512`，且均无 `update_missing` 分支 |
| 3.6（2PC delete_recently_updated） | `two_phase_delete_recently_updated_{skip,error,update}` | 全部 SUCCESS；双端并发 delete/update 后分别验证 skip=512 条历史、error=1 条 GID 历史、update=512 条历史且 node135 被删除清空 |
| 3.7（2PC target_column_missing） | `two_phase_target_column_missing_{ignore_if_null,skip,error,ignore}` | 全部 SUCCESS；3.7.1 与 3.7.3 已实际验证 rollback=`0|1`、commit 累计=`0|2`，文档将 streaming GID 与不存在的 `2pc_gid` 重复计数的问题已单列记录 |
| 3.8（2PC source_column_missing） | `two_phase_source_column_missing_{use_default_value,error,skip}` | 全部 SUCCESS；3.8.2 已实际验证 rollback=`0|1`、commit 累计=`0|2`，文档将 streaming GID 与不存在的 `2pc_gid` 重复计数的问题已单列记录 |
| 3.9（2PC target_table_missing） | `two_phase_target_table_missing_{skip_if_recently_dropped,error,skip}` | 全部 SUCCESS；3.9.1/3.9.3 的文档收尾 DROP 已作为 mapping 清理步骤缺失单列，3.9.2 已实际验证 error 历史 rollback=`1`、commit 累计=`2` |
| 3.10（2PC multiple_unique_conflicts） | `two_phase_multiple_unique_conflicts_{error,skip}` | 全部 SUCCESS；3.10.1 已实际验证 error rollback=`1`、commit 累计=`2`，3.10.2 在该正确前态下验证 skip rollback=`1`、commit=`2`；文档 GID 重复计数已单列记录 |
| 3.11（2PC update_origin_change） | `two_phase_update_origin_change_{update_if_newer,error,skip,update,skip_transaction}` | 全部 SUCCESS；按文档先在 node134/node135 以 `set1` 完成异步刷新，再参照 `mmr-autotest` 以 schema-only 加入 node136 并触发三节点冲突。3.11.1-3.11.5 分别在 `run_20260718_000302_f905d9`、`run_20260718_000340_2e20fc`、`run_20260718_000359_d3847b`、`run_20260718_000623_abc4a6`、`run_20260718_000558_eb539c` 通过；3.11.4 的 1024 条历史预期已确认应为 512 并记为 D-036 文档错误 |

2PC 共用的稳定能力已抽取到 `suites/mmr/two_phase_conflict_support.py`：仅共享拓扑校验、
复制集、512 行前置和两次分离的 `PREPARE TRANSACTION`/`ROLLBACK PREPARED` 或
`COMMIT PREPARED` 流程。各 case 仍独立声明文档章节、resolver、GID、SQL 文本与预期历史。

streaming 冲突处理文档 2.1-3.11 已逐项建立可执行用例；当前所有已复核的 streaming 冲突用例均应为 SUCCESS，文档预期/时序差异已单列在 `多活文档问题.md`。

## MMR 执行耗时优化

2026-07-18 以 `run_20260718_043432_2bdfc4` 为基线，157 条默认 MMR 用例报告内耗时合计
`1407.535s`；其中 82 条 streaming 冲突用例为 `840.296s`，平均 `10.248s/条`。主要成本不是
512 行业务数据，而是每条用例重复初始化两个 PostgreSQL 实例、安装扩展、建组和 join。

框架现支持 case 声明 `session.key` 和会话级 fixture：同一 `run` 中相同 key 的 case 只初始化
一次，最后统一停止并删除隔离集群；每条 case 仍使用 reset fixture 清理自己的表绑定、测试表、
冲突历史、resolver 和 apply worker 状态。显式 case 可单独运行，此时仍独立创建和回收会话。

首批迁移的是 `update_missing` 的 `insert_or_skip`、`insert_or_error`、`error`、
`skip_transaction` 四条。`run_20260718_061627_0daef3` 结果均为 SUCCESS：原独立运行报告耗时
合计 `29.507s`，共享会话同批墙钟为 `20.991s`，节省 `8.516s`（28.9%）；四条本体步骤合计由
`29.507s` 降至 `11.910s`，其余为一次性会话初始化与清理。

随后把同样可复用的 `insert_exists` 的 `update_if_newer`、`update`、`error`、
`skip_transaction` 迁入该会话。八条跨两族连续回归
`run_20260718_062711_bfad41` 均为 SUCCESS：原独立报告耗时合计 `55.156s`，共享会话总墙钟
`30.706s`，节省 `24.450s`（44.3%）。`insert_exists_skip` 与 `update_missing_skip` 一样依赖
error worker 的原始重试状态，保留独立运行；`run_20260718_062628_9b6565` 为 SUCCESS、`11.584s`。

`delete_missing_skip/error` 也通过共享会话连续回归；`delete_missing_skip_transaction` 依赖
error 回放前态，保留独立。十条跨三族回归 `run_20260718_063115_d271d1` 全部 SUCCESS：原独立
报告基线合计 `70.426s`，共享会话总墙钟 `40.246s`，节省 `30.180s`（42.9%）。

`update_pkey_exists` 的 `update_if_newer`、`update`、`error`、`skip_transaction` 也已通过
共享回归 `run_20260718_063405_37afdd`，耗时分别为 `2.754s`、`2.897s`、`2.930s`、`2.924s`，
同批墙钟 `21.058s`；独立基线约 `27.991s`。`update_pkey_exists_skip` 依赖 error worker
重试，保留独立，`run_20260718_063454_b18096` 为 SUCCESS、`13.188s`。

十四条共享 case 的跨族回归 `run_20260718_064016_d7bab2` 全部 SUCCESS，墙钟 `53.999s`；
对应独立报告基线 `98.417s`，节省 `44.418s`（45.1%）。该回归曾暴露 reset 只恢复当前 resolver
会使上一族的 `error` 策略污染后续 worker；现已在 reset 中统一恢复所有共享策略族的默认 resolver，
再重启订阅 worker，复跑稳定通过。

后续量化使用“同一批 case 的独立报告耗时合计”和“显式连续共享回归的总墙钟”比较；后者包含一次
隔离集群初始化和最终回收，不能只比较控制台的 case 主体耗时。

| 策略族 | case | 独立基线 | 共享连续墙钟 | 结果与边界 |
| --- | --- | ---: | ---: | --- |
| `update_missing`、`insert_exists`、`delete_missing`、`update_pkey_exists` | 14 条无 error 回放依赖的 resolver case | `98.417s` | `53.999s` | SUCCESS，节省 `44.418s`（45.1%） |
| `delete_recently_updated` | `skip`、`error` | `18.294s` | `16.538s` | SUCCESS，节省 `1.756s`（9.6%）；两条业务步骤本身含 1/2 秒的受控并发等待，收益低于无固定等待的策略族 |
| 上述全部 | 16 条两节点共享 case | `116.711s` | `70.048s` | `run_20260718_072836_432314`，16/16 SUCCESS，节省 `46.663s`（40.0%） |
| `update_recently_deleted` | `skip`、`insert_or_skip`、`insert_or_error` | 独立实测 SUCCESS | 不迁移 | FIFO/advisory-lock 因果时序确认 DELETE 已执行、UPDATE 已提交、最后 DELETE 提交；fixed 512 历史并非 streaming 分片语义，改为验证数据结果和对应历史非零（D-044） |
| `update_origin_change` | 5 条 3 节点策略 case | `61.778s` | 未迁移 | node136 的 schema-only 元数据和 error 回放状态不能由当前两节点 reset 无损恢复，需先设计并验证专用三节点 session/reset |

三节点第一批可迁移候选 `update_if_newer`、`update`、`skip_transaction` 已在
`run_20260718_074736_4b7ab5` 以独立 topology 连续回归：3/3 SUCCESS，真实墙钟 `38.249s`，
报告内 case 耗时合计 `34.406s`。后续三节点共享 session 必须以 `38.249s` 为优化前基线；`skip`
仍依赖 error worker 的回放前态，`error` 将只在确认其位于共享组末尾且不会污染任何后续 case 后再评估。

三节点 session 已在 `run_20260718_080209_5e5bf3` 实测：`update_if_newer`、`update`、
`skip_transaction` 3/3 SUCCESS，完整墙钟 `24.148s`，较独立基线节省 `14.101s`（36.9%）。会话级
步骤一次性创建原有 node134/node135/node136 topology；每条 case 前只对三个节点的
`immediate_parallel_conflict` 与冲突历史执行 TRUNCATE，并恢复 node135 的
`update_origin_change=update_if_newer`，不删除 g1 或 node136 schema-only 映射。

默认回归原本按 case ID 排序，共享 case 会被拆成多个不连续段，反复初始化隔离集群而无法获得
上述收益。框架现仅在无 target 的默认回归中将相同 `session.key` 合并为连续块；显式 target 继续保持
用户输入顺序，并在切换到不同 key 或独立 case 前立即回收旧会话。共享 case 可通过 `session.order`
声明已验证的同组顺序，避免 error resolver 的 worker 状态污染后续策略；未声明时保持发现顺序。共享 -> 独立 -> 共享的真实回归
`run_20260718_065803_483a8b` 三条均 SUCCESS（`2.728s`、`13.057s`、`0.717s`），结束后临时目录及
15651/15652 监听均不存在。14 条原始共享组的 45.1% 与当前 16 条完整共享组的 40.0% 均为已验证收益；
默认全量 MMR 收益尚未重新全跑量化。

`mmr.streaming_conflict.update_missing_skip` 不能复用该会话：它依赖 error resolver 导致 apply
worker 退出后，对原始事务的重试语义。共享 `set1` 时该断言从预期 1 条 skip 历史变为 514 条，
改变了受测行为；已保留独立集群，`run_20260718_061555_db4443` 为 SUCCESS、`11.658s`。后续仅把
reset 后语义不变的同拓扑 resolver 组合迁入会话；data-only/all join、error 重试和需要重建元数据的
场景继续独立运行，必要时标记 `[LONG-TIME]`。
