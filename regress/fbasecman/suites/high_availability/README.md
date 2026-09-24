# high_availability suite

本套件严格对齐《fbasecman转测前核心功能测试执行方案.md》第 4 章（四、监控、高可用、控制台和 Reload），共覆盖 CORE-13 至 CORE-22 十大核心测试场景。

详细完整的技术设计与执行规范见仓库根目录：
[`../../fbasecman第4章高可用自动化测试设计方案.md`](../../fbasecman第4章高可用自动化测试设计方案.md)。

## 代码结构

```text
suites/high_availability/
├── __init__.py               # 套件包声明
├── README.md                 # 说明文档
├── case.py                   # HighAvailabilityCase 数据模型
├── manifest.py               # 10 个用例清单与元数据声明
├── suite.py                  # CLI 入口与调度器 (show, run, run_case)
├── runtime.py                # 执行上下文、三层证据与五大时间戳记录器
├── console_parser.py         # 控制台表格输出结构化解析与字段语义断言器
├── cluster_ops.py            # 单机 5 实例的启停、探活、升主、重建工具
├── executors/                # 各阶段具体执行逻辑
│   ├── __init__.py
│   ├── phase1_console.py     # CORE-19 ~ CORE-22 (控制台原子性与 Reload 保护)
│   ├── phase2_failure.py     # CORE-13 ~ CORE-15, CORE-18 (故障感知与只读回退)
│   └── phase3_failover.py    # CORE-16, CORE-17 (主备切换与 MMR 漂移)
└── assets/
    └── config/
        └── fbasecman_ha.conf # 4 种组与 5 节点标准配置模板
```

## 用例清单 (CORE-13 ~ CORE-22)

| 用例名 | 对应文档编号 | 核心验证目的与检查点 |
|---|---|---|
| `core_13_monitor_confirm` | CORE-13 | monitor 故障和恢复确认的双向防抖及连续探测周期验证 |
| `core_14_rep_standby_failure` | CORE-14 | 复制组备机故障读回退主库，备机恢复重新分流，在途请求不假成功 |
| `core_15_rep_primary_failure` | CORE-15 | 复制组主故障但暂不升主时，备库在 `NO_PRIMARY` 下安全提供只读 |
| `core_16_rep_failover` | CORE-16 | 复制组主备切换：旧主 PARTED 隔离 $\rightarrow$ 新主升主 $\rightarrow$ REFRESH $\rightarrow$ 新写路由 $\rightarrow$ 从库重建 |
| `core_17_mmr_write_center_failover` | CORE-17 | MMR 故障自动漂移至 `promoted_cluster`，在途旧事务不自动重放，人工命令切换持久化 |
| `core_18_balance_single_failure` | CORE-18 | balance 备机优先（备机存活严禁回退主库），全组备机全灭才回退主库；single 回退 |
| `core_19_set_node_atomicity` | CORE-19 | SET NODE 批量原子性：含非法节点时整批直接拒绝，绝对不发生部分修改与半写入 |
| `core_20_write_promoted_refresh_show` | CORE-20 | 角色指令合法与非法拒绝边界校验，SHOW 全表字段严格审计 |
| `core_21_reload_parameters_and_structure` | CORE-21 | 持续短事务下 Reload 连接 0 中断；运行参数修改热生效；动态增删用户组隔离 |
| `core_22_reload_failure_protection` | CORE-22 | 配置文件语法错误 Reload 保护回退；持久化目录只读（`chmod 555`）写入失败保护 |

## 控制台字段语义级断言核心要求

本套件拒绝仅截取控制台纯文本日志。所有用例均由 `console_parser.py` 解析为结构化字典，严格断言：
1. `SHOW GROUP_ROUTING`：
   - `current_primary`（主库识别精准性）
   - `candidate_node`（备机宕机/PARTED 后**必须彻底从只读候选列表消失**）
   - `effective_state`（`active` $\rightarrow$ `connect_failed` / `parted` 状态迁移）
   - `is_write_target`（**全组有且仅有 1 行**为 `true`，漂移后即时切换）
   - `route_status`（`VALID` vs `NO_PRIMARY`）
2. `SHOW CLUSTERS`：
   - `topology_state`（健康 `ONLINE`，故障 `CONNECT_FAILED`/`NO_PRIMARY`，严禁假在线）
3. 批量命令原子性：
   - 验证失败后立刻反查控制台，断言合法节点状态未变（0 污染）。

## 运行方式

```bash
# 查看所有用例
./run.sh show high_availability

# 运行单条用例
./run.sh run high_availability.core_19_set_node_atomicity

# 运行完整套件
./run.sh run high_availability
```
