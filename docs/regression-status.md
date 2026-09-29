# 回归平台迁移现状与待办

版本：2026-09-28 · 对照仓库：`postgresql_for_fbase_dev/fbase_regress`、`fbasecman_dev/fbasecman_regress_v2`

本文回答两件事：**平台回归能力已经具备什么**、**要达到"各用例判定=老代码结果"还缺什么**。事实以当前代码和实测结果为准；每条非 PASS 判定给出定性分类，不允许无定性遗留。

## 1. 总体状态速览

| 产品 | 用例数 | 执行路径 | 实测基线 | 未完成原因 |
| --- | --- | --- | --- | --- |
| fbase-database | 228（mac 58 + mmr 170） | 平台原生 `RegressionEngine`，声明式步骤 | mac 56 PASS + 2 保真 FAIL；mmr 161 PASS + 2 FAIL + 2 BLOCKED | 见 §4 定性；5 条 `default_enabled=False` 未入批 |
| fbasecman | 212（144 平台宿主 executor + 68 native） | 平台引擎 + 平台 SDK 原生用例；144 条经 `RuntimeExecutorCase`/`_GlobalCachePlatformCase` 宿主，`LegacySuiteCase`/`SuiteNativeCase` 已删除 | 见 §4.3 分套件 | **144 条 executor 已完成 `rt.*`→`ops.*`/`context` 形态改写**（`def case_x(context)` + `fbasecman_ops` PEP 562 转发 facade，`context_executor` 钩子）；runtime 构造注入 resolver `env`/`context_data`，不再自行 legacy 加载；四套件真机批跑 137 PASS / 1 flaky（core_19 复跑 PASS）；native 覆盖 common 4、sql_parse 4、ha_commands 16、tmp 1、outstanding 11、rw_toggle 14、guc 18 |

**唯一执行面**：`python -m platform_regress.cli --product-dir <产品> [--suite S | target | failed]`。vendored 诊断入口已物理删除：`run.sh`、`tools/cli.py`、各套件 `suite.py`/`plugin.py`/`run_case`、`suites/registry.py`、vendored `unit_tests/` 与 legacy 树内重复的 `products/fbasecman/` 副本全部移除；`products/fbasecman/cli/run.sh` 收敛为 `platform_case.py` 薄壳；`products/fbasecman/regression/run.py` 保留 `--check-profile`（target 存在性改查 `catalog.json`，不再依赖 registry）。

## 2. 平台已具备的回归能力（`backend/platform_regress/`）

| 子包 | 能力 | 状态 |
| --- | --- | --- |
| `engine.py` | `RegressionEngine`/`CaseContext`/`CaseResult`；setup→run→cleanup→fixture 清理生命周期；PASS/FAIL/BLOCKED/ERROR/CANCELLED 判定；清理失败独立计 `cleanup.status`；`result.json` 原子落盘 | 已上收 |
| `cli.py` | `--product-dir` 加载 `cases.py`；CASE_ORDER 保序；`default_enabled` 过滤；session 连续分组+共享 fixture；`--suite`；`failed`（含兄弟目录 last_failed 合并）；`suite-result.json` 聚合；`last_failed.json` 簿记 | 已上收 |
| `runtime.py` | `CaseRuntime` 基类：步骤记录/证据步骤/check/命令执行/report/summary/finish/stop | 已上收（cman 经 `FbasecmanCaseRuntime` 继承） |
| `configuration/` | 分层 YAML + deep merge + legacy shell 解析钩子 + validator/legacy_mapper 注入 + reload 原子安装 + profile 隔离校验 | 已上收 |
| `environment/` | EnvironmentProvider 契约、provider 注册表、`preflight_health_check`（provider_factory 注入保旧 patch 点）、sanitizer | 已上收 |
| `clients/psql.py` | argv 构建、标准/expanded 表解析、assert_table_rows | 已上收 |
| `clients/jdbc.py` | pgjdbc jar 解析、JDBC URL、javac/java argv、classpath 与源文件暂存、`JdbcError` | 已上收（14 单测） |
| `clients/pgwire.py` | PostgreSQL 前后端裸协议客户端：startup、报文帧、extended_execute 周期记录（SQLSTATE/CommandComplete/ReadyForQuery）、分片字节、ProtocolClient 会话 | 已上收（15 单测） |
| `execution/` | command、daemon（ManagedDaemon 守护进程内核：ready 探针/pid+port 清理/crash forensics）、phased_process（stdin 相位 JDBC 驱动）、polling、ports（动态挑口）、forensics（core 检测）、locking（ExclusiveFileLock） | 已上收 |
| `evidence/` | assertions、backup（备份目录快照断言原语）、jdbc、log_checks、log_window、step | 已上收 |
| `suites/` | contracts（CaseSpec/SuitePlugin/validate_cases）、registry（preflight 策略+quiet_env 挂点）、failed（last_failed/case_status/rerun）、executor（`RuntimeExecutorCase`/`RuntimeBinding`/`context_executor` 宿主）；原 `LegacySuiteCase`/`LegacyCaseBinding` 适配器已随 144 条迁移完成物理删除 | 已上收 |
| `requirements.py` | 依赖门注册表（clusters/commands/plugins/groups/nodes/node/system_time_control/roles/extensions 按旧序），产品 `register_requirement(before=)` 注入专属 evaluator，不满足统一 BLOCKED | 已上收（15 单测） |
| `reporting/` | model、renderer、junit、html、export（CaseResult 事实模型→双格式；BLOCKED/CANCELLED→SKIPPED） | 已上收 |
| `steps.py` | 声明式步骤执行器：sql/command/wait_sql/background_sql/wait_background_sql/node_action/cluster_action/system_time_shift | 已上收 |

平台侧测试：**229 passed**；vendored fbasecman 单测：**259 passed**；FBase vendored 单测：**149 passed**；前端生产构建通过（2026-09-28 本轮验证）。

## 3. 已完成的结构性工作

1. **framework 通用件上收**（configuration/environment/clients/suites 四子包入 `platform_regress`）；vendored `framework/*` 全变薄 shim，产品 schema/legacy key 映射/健康期望留产品侧。
2. **协议原语**：`pgwire.py` + 三个产品探针（outstanding 564→430 行、savepoint、heartbeat_bind）改薄壳，输出契约逐字保留。
3. **fbase-database 全量原生迁移**：228 条用例跑平台引擎——psql 执行器、12 种断言、8 种步骤类型、38 个共享 fixture、session 生命周期、`runtime_tokens`（解决导出时 uuid 冻结）、判定与 legacy 逐字对齐。
4. **fbasecman 引擎收敛**（本轮）：`LegacyCmanCase` 由"run.py 子进程"改为进程内 `LegacySuiteCase`——平台引擎持调度/判定归一/证据；套件 `run_case` 持 fixtures/断言/产物（仍写 `env.output_dir/runs/<suite>/<case>/`）。`_EXTRA_CONFIGS` 注入环境 override（等价 run.py 的 loader patch，call-time 求值）。`global_cache` 抽 `run_case`；handover 套件锁、`fbasecman_cases` 用例锁语义保留。provider `failed` 目标走平台 cli（兄弟 last_failed 合并）。212 目标与 suite spec 1:1。
5. **环境自愈**：pgcluster `cluster_ops`（promote 补槽、rebuild 剔 `primary_slot_name`）、`heal_cluster`、fixture join `'all'` + part/drop 重试、健康期望放宽到 `{JOIN_START, ACTIVE}`。

## 4. 用例实测结果与定性

### 4.1 fbase-database · mac（58）

- **56 PASS**（含复跑后 PASS 的 `audit.log_configuration`—全新审计目录首次切换的目录成熟度问题；`audit.role_audit_logs`—runtime_tokens 修复）
- **2 保真 FAIL**：`separation_of_duties.dba_user_management_separation_off`、`tde.dynamic_switch_document_requirement`——与 legacy 判定逐字一致（后者即已知问题 D-007，rc4→plain 不切）

### 4.2 fbase-database · mmr（批跑 165=170-5 disabled）

- **161 PASS**
- **FAIL ×2**：`streaming.default_publication_preparation`（`node[node135] two_phase enabled unless copy_data=false`——与 legacy 3/4 次逐字一致，产品行为）；`cluster_verification.connection_failure_priority`（恢复 20s 不收敛——legacy 同模式已知 flaky）
- **BLOCKED ×2**（批间节点占用暂态）：`cluster_verification.node_state`、`time_difference` — **待复跑定性**
- `mmr.node_management.*` 5 条 `default_enabled=False`（资源密集 join 类），批跑跳过、可单独跑——**待单独验证**

### 4.3 fbasecman（212）

| 套件 | 结果 | 定性 |
| --- | --- | --- |
| ha_commands 76 | 全 PASS | 16 条 native（含 cluster 域 10 条 `HaCommandsCase`/`HaRuntime`：conf transform、语义 diff 白名单、备份断言、monitor 轮询、ssh 远程节点停起逐语义对齐 legacy）+ 60 legacy |
| high_availability 10 | 全 PASS | core_16 failover→rebuild→自愈端到端 |
| outstanding 11 | 全 PASS | — |
| rw_toggle 14 | 全 PASS | — |
| common 4 | 全 PASS | 平台 cli 端到端验证 |
| sql_parse 4 | 3 PASS + 1 保真 FAIL | `savepoint_recovery_after_local_25p02`：代理 25P02 后内部 ROLLBACK，legacy 同构失败 |
| guc 18 | 14 PASS + 4 FAIL | `discard_all_{sql_parse,hint}`：DISCARD 路由后代理断连（产品缺陷，无 crash）；`report_param_timezone_{sql_parse,hint}`：新会话 ParameterStatus 未回放（产品缺陷，无 legacy 对照） |
| global_cache 18 | 17 PASS + 1 FAIL | `discard_all_clears_backend_cache`：**二进制版本漂移**（SHOW SERVER_PREP_STMTS 9 列 vs legacy 6 列；conf 逐字节一致），同版本下 legacy 同用例也 FAIL——不得掩盖 |
| handover 56 | 50 PASS + 6 未实现 | `console_*_statistics` 6 条 manifest 声明但 upstream dispatch 表同样无 executor、无历史运行——声明式未实现用例，不是迁移回归 |
| tmp 1 | 1 FAIL | `reload_disable_monitor_route_loss`：reload 后 monitor_enabled=no 丢业务路由——用例正是为此缺陷写的，产品真实缺陷面 |

**迁移保真度结论**：可运行用例无迁移性回归；FAIL 全部落在四类——①legacy 同构失败 ②产品真实缺陷 ③上游未实现 ④二进制版本漂移——每类有证据。

## 5. 已知非平台问题登记（判定基线，不得为凑绿而修改断言）

| 用例 | 现象 | 定性 |
| --- | --- | --- |
| `guc.report_param_timezone_*` | 新会话时区不回放（America/New_York ≠ Asia/Shanghai） | fbasecman ParameterStatus 回放缺失，产品缺陷 |
| `guc.discard_all_*` | DISCARD ALL 后代理断开后端连接 | fbasecman 会话重置实现缺陷 |
| `tmp.reload_disable_monitor_route_loss` | reload 后 `route for 'mmr_group.postgres' is not found` | fbasecman 监控开关与路由联动缺陷 |
| `global_cache.discard_all_clears_backend_cache` | `SHOW SERVER_PREP_STMTS` 9 列（legacy 6 列） | fbasecman 二进制 9-21 重编后行为变更，版本漂移 |
| `handover.console_*_statistics` ×6 | "no executor found" | 上游 manifest 未实现，保持 FAIL 不粉饰 |
| `sql_parse.savepoint_recovery_after_local_25p02` | 25P02 后内部回滚破坏 savepoint | 保真失败 |
| `mmr.streaming.default_publication_preparation` | `two_phase enabled unless copy_data=false` | 保真失败/产品行为 |
| `mac.separation_of_duties.dba_user_management_separation_off` | DBA 改 SSO CREATEROLE 未被拒 | 保真失败 |
| `mac.tde.dynamic_switch_document_requirement` | reload 后仍 rc4 | 已知问题 D-007 |

## 6. 待办清单

### A. 平台能力缺口（按价值序）

1. ~~**`clients/jdbc`**~~（2026-09-28 完成）——`platform_regress/clients/jdbc.py` 已提供 jar 解析/URL/argv/暂存；`drivers.py`、`ha_commands`、`handover`、`native.py` 全部改调平台 helper，`.java` 驱动与相位动作表留产品包。
2. ~~**`execution/daemon`（ManagedDaemon）**~~（2026-09-28 完成）——`platform_regress/execution/daemon.py` 提供通用内核（ready 探针注入、pid/port 两级清理、crash forensics、stop 竞态容忍）；`FbasecmanProcess` 降为 51 行薄 adapter（conf 渲染器 + console psql 探针），构造签名与日志文件名/报错文案逐字兼容，`FbasecmanProcessError` 别名保留。
3. ~~**`requirements` 门框架**~~（2026-09-28 完成）——`platform_regress/requirements.py` 提供有序 evaluator 注册表（`evaluate_requirements`/`register_requirement(before=)`），内建 evaluator 按旧门逐字顺序（clusters→commands→plugins→groups→nodes→node→system_time_control→roles→extensions），不满足统一 `Blocked`；roles/extensions 走平台 `context.sql` 通道；fbase-database 以 `register_requirement("writable_node", before="system_time_control")` 注入产品专属门，`check_requirements` 降为委托壳。
4. ~~**`FbasecmanCaseRuntime` 通用件上收**~~（2026-09-28 完成）——`CaseRuntime.asserted_command`（轮询证据步内核）+`file_diff`、`clients/psql.py::parse_expanded_rows`、`evidence/backup.py` 快照原语已上收；产品 runtime 811 行，剩余为 conf 模板渲染、`_semantic_config_diff`/`_command_scope` 语义 diff、console/业务 psql 薄壳与报告钩子。
5. ~~**报告统一**~~（2026-09-28 完成）——`platform_regress/reporting/export.py` 从 CaseResult 事实模型直接渲染 JUnit/HTML；`platform_regress.cli` 新增 `--junit`/`--html`/`--report-title`/`--suite-name`；`collect_results_from_run` 按 suite-result.json→result.json→legacy runs 布局收集，verdict 不再从展示文本猜测；渲染器默认参数去产品名，vendored CLI 显式传产品名保持旧报告。

### B. 保真验收缺口（每条判定=老代码）

1. ~~**全套件逐条 verdict diff**~~（2026-09-28 完成）——`tests.fbasecman target=all` 经 Web 任务入口跑完全量：206/212 条执行（6 条 `handover.console_*_statistics` 为 `long_time` 默认排除，符合 suite 批跑语义）。首轮 182 PASS / 7 FAIL / 17 ERROR，逐条定性：
   - **16 条 global_cache ERROR**＝`run_root` 硬编码 `root/"output"` 不吃 `env.output_dir` 重定向，`summary.json` 落到 vendored 树——已修 `global_cache/runtime.py` 改走 `env.output_dir`，复跑 `reuse_single_and_cross_client` PASS。
   - **2 条 native ha_commands FAIL**＝`native.render_config` 只渲 `mmr_group`，而 `HaConsoleCommands.java` 访问 `single_group`/`rep_group` → `route not found`；且 native `start_process` 的 TCP-ready 探针不覆盖 group_checker 的 mmr_role 收敛窗口 → `SHOW GROUP_ROUTING` 读出 `UNKNOWN`（历史输出为 `write-leader`）。已修：conf 补齐 rep/balance/single group + `group_names` 全量，新增 `_wait_mmr_routing` 收敛等待接入 4 处 console 断言用例；`jdbc_console_ha_commands`/`set_node_write_idempotent` 复跑 PASS。
   - **3 条同因复现**：`guc.discard_all_hint`/`discard_all_sql_parse`（DISCARD ALL rc=2 与历史逐字一致）、`tmp.reload_disable_monitor_route_loss`（步骤 5 rc=2 与历史一致）。
   - **`global_cache.discard_all_clears_backend_cache`** 对应历史已知缺陷 F-001。
   - **`sql_parse.savepoint_recovery_after_local_25p02`**（新增用例无历史基线）首轮 FAIL 亦是 ready 窗口误报——`render_config` 补全 group 后 group_checker 首轮收敛变慢，协议探针撞上 `route not found`/未知路由态；`_wait_mmr_routing` 覆盖全部 7 处 `start_process` 调用点后复跑 PASS。
   - 注：`rw_split_method` 非 none 的用例（sql_parse 等）`group_names` 须收缩为 `mmr_group`——fbasecman 校验 single/balance group 仅接受 `rw_split_method "none"`，与 legacy `_sql_parse_transform`/`_route_user_scope` 语义对齐。
2. ~~**mmr 3 条 BLOCKED 复跑**~~（已完成）——`mmr.replication_set.synchronous_removal`/`mmr.default_publication.schema_filtering`/`mmr.cluster_verification.check_node_conf_table_exclusion` 平台 PASS vs 历史 BLOCKED（two_phase 前提不满足）；差异根因是用例演进为隔离 fixture 自建 two_phase=false 双节点（initdb/create_node/create_group 证据齐全），非平台失真。
3. ~~**`mmr.node_management` 5 条 disabled 用例**~~（已完成）——`join_group`/`multi_database_active_join`/`online_join_all_retry` PASS；`multi_database_three_node_join`（步骤 35 超时 rc=124）与 `online_join_data_retry`（订阅映射冲突，历史已知缺陷 D-017）同因复现历史失败。
4. **handover 6 条未实现用例**——维持现状：`default_enabled=False`（long_time 统计），不进入批跑；平台判定语义正确。
5. ~~**`failed`/`all` 全链路经 Web 任务入口验收**~~（已完成）——`failed` 精确重跑 last_failed 并入库（含平台树 `result.json` 收录修复）；`all` 经 provider `validate_target` 放行后由 `platform_regress.cli` 驱动，native+legacy 混合执行全程留证。

### C. 环境事项

- cman-lab MMR 环境已归位（复制家族全绿）；`qa_case.orders` 等套件自建表在套件生命周期内管理，不算环境基线。
- `regress.local.yaml` 符号链接到 cman-lab override——vendored `run.sh` 已删除，该链接仅为历史兼容残留；平台路径由 `_EXTRA_CONFIGS` 注入，不依赖该链接。
- ~~fbasecman 二进制版本固定问题~~（2026-09-28 完成）——`deployment/fixture.py` 生成 `test_context.yaml` 时写入 `binaries` 段：fbasecman 本地二进制与远端 PostgreSQL 二进制各记录 `path`/`sha256`/`size`/`mtime`（远端经 SSH `stat`+`sha256sum` 采集），与 `group_uuid`/`role_passwords`/`system_identifiers`/`ciphertexts` 并存。验证见 `tests/test_fixture_fingerprint.py`（4 项）。

### D. 收尾（design.md §12 P2 未完成项）

- **迁移完成口径以 design.md §5.0.1 为准**：平台 SDK 缺能力时直接补平台公共契约；产品只保留配置、协议、专属 fixture 与业务断言；行为保真但不保留旧结构。`SuiteNativeCase`、`LegacySuiteCase`、suite `run_case`、旧 Runtime/runner 依赖全部删除前，不得宣称 SDK 原生迁移完成。
- cman 用例逐步从 `run_case` 壳迁到声明式/半声明式：已完成 common 4 条的平台 SDK 原生宿主，步骤、列清单、并发规模、错误注入、quantiles 与 worker 生命周期判定按参考工程保留；`CaseContext.stop_processes()` 支持同一用例切换配置前停止旧实例。本轮自动验证通过，因环境切换后缺少 CLI 所需部署配置，尚待在真实 cman 环境逐条复跑。
- 剩余 144 条状态（本轮推进）：平台执行路径已**不再经过** suite `run_case`/`run_cases`/`run_runtime_case`——判定、teardown 顺序、锁与证据桥接全部由 `RuntimeExecutorCase`（ha_commands 60、high_availability 10、handover 56）和 `_GlobalCachePlatformCase`（global_cache 18，复刻 `_run_case` 两段式判定含 core 检测）承载；套件失败类型基类上收为平台 `CaseFailure`→FAIL，verdict 与 legacy 逐条等价；handover 套件锁改为逐用例 `__enter__/__exit__`；runtime 初始化失败保留 legacy init `report.txt`；`run_root` 继续落在 `env.output_dir`（Web UI 契约不变），`_finalize_run` 把 report/summary/steps/日志镜像进平台证据面。修复一处真实缺陷：`set_legacy_config_loader` 此前绑定未包装 loader，runtime `__init__` 内部 env 加载不吃环境 override。**本轮续推（dispatch 去耦）**：三套件的 `EXECUTORS` 注册表与 global_cache 的组合执行层（`_execute_started_case`/`_case_phase`/`_collect_phase_checks`/`_execute_*` 场景编排/`STARTED_CASE_EXECUTORS`/`SPECIAL_CASE_EXECUTORS`/`_execute_case`）迁入各套件独立 `dispatch.py`；`runtime_cases.py` resolver 改为直读 `suites.X.manifest`/`dispatch`/`executors`/`runtime` 模块，`cases.py` catalog 构建绕过 registry/plugin——平台路径（`platform_regress.cli` + `products.fbasecman.cases` + resolver）经子进程 sys.modules 守卫测试证明不加载 `suites.*.suite`、`platform_regress.suites.runner`、`suites.registry`、`suites.*.plugin`。vendored `suite.py`/`run_case`/`run()`/`plugin.py`/`registry.py` 已随诊断入口退役物理删除（2026-09-29 清理批次）。**executor 形态改写已完成（2026-09-29 收尾）**：144 个 executor 已从 `def case_x(rt)` 批量改写为 `def case_x(context)` + `ops.*` 调用（`fbasecman_ops` PEP 562 转发 facade），`RuntimeBinding.context_executor` 钩子承载 `(context, runtime)` 分发，runtime 构造注入 resolver `env`/`context_data` 不再自行 legacy 加载；vendored runtime 方法库（record_step/write_report/journal/进程编排的产品语义）按 §5.0.1.3 属产品知识原地保留，其内部通用原语已全部走平台件。四套件真机批跑 137 PASS / 1 flaky FAIL（core_19 收敛竞争，单跑 PASS）。
- `run.py`/`tools/cli.py`/`run.sh` 退役进展（2026-09-28 收缩，2026-09-29 物理删除）：
  - `products/fbasecman/regression/run.py` 保留 `--check-profile` 校验（target 存在性改查 `catalog.json`，不再 import `suites.registry`）。
  - `legacy/run.sh`、`tools/cli.py`、各套件 `suite.py`/`plugin.py`/`case.py`、`suites/registry.py`、vendored `unit_tests/`（259 项）、legacy 树内重复 `products/fbasecman/` 包（2713 行）、零引用的 `env/`（1204 行，依赖早已不存在的 `framework.*`）、`tests/`、`output/`、一次性迁移脚本 `export_legacy_catalog.py` 全部删除；`tools/` 仅留 `web_reports.py`（平台报告解析仍经它）与 stable 工具链；`cli/run.sh` 收敛为 `platform_case.py` 薄壳。
  - `products/fbase-database/provider.py` 的 `all`/子 suite 前缀 target 改走 `platform_regress.cli --suite`，平台链路不再直接调用 `run.sh`。
  - **`LegacyFbaseCase` 已删除**（D1，228 条 CASES 全为原生类型，兜底差集为空，提交 45f08f9）；`fbase-database/regression/legacy/` vendored 树（framework/suites/unit_tests/run.sh/templates）已物理删除，`legacy/` 层级随后随产品回归树收敛移除，`regress.yaml` 现位于 `regression/` 集群配置源与问题单文档；`cli/run.sh` 收敛为 `platform-case` 薄壳。
  - **fbasecman `framework/` shim 已物理删除**（提交 772e9b3）：全部 `framework.*` import retarget 到 `platform_regress.*`；产品私有胶水并入 `legacy/cmanconf.py`；平台 `runtime.py` 改用 `set_legacy_config_loader()` 注入契约；`tools/architecture.py` 改守 cmanconf 边界。
  - `fbase-database/regression/framework/`（原 `regression/legacy/framework/`）已随 vendored 单测一并物理删除（见上条）。

## 7. 验收标准与红线

1. 每条用例判定 = 老代码在等价环境/二进制下的判定；不一致必须有 §5 四类之一的证据支撑。
2. 不得把 FAIL/BLOCKED/ERROR 改成 PASS 来凑绿；不得删步骤、松断言、跳过 setup/teardown。
3. 平台核心不出现产品名分支；产品专属 evaluator/探针/schema 走注册挂点。
4. 每个结论有证据：result.json + artifacts + events.jsonl 必须能回溯判定依据。
5. 全量验证门：`pytest tests/`（当前 249）+ 前端 build + 真实套件抽测。两套 vendored `unit_tests/` 均已删除（patch 面针对已删编排层/导出后失修）。
6. “已注册到 RegressionEngine”不等于“SDK 原生迁移完成”；覆盖测试必须同时证明无 `SuiteNativeCase`、`LegacySuiteCase`、suite `run_case` 和旧 runner 运行依赖。
7. 平台缺少通用能力时必须补平台契约，不得为赶进度把通用生命周期、并发、配置、证据或报告逻辑继续堆入产品临时宿主。

## 8. 快速复现入口

```bash
# 平台测试
.venv/bin/python -m pytest tests/
# 平台 cli 单用例/套件/failed
PYTHONPATH=<repo>:<repo>/backend .venv/bin/python -m platform_regress.cli \
  --product-dir products/fbasecman --output-dir <out> \
  --context-json '{"regress_source":..., "regress_override":..., "regress_report_root":...}' \
  common.console_commands | --suite common | failed
# 任务入口（provider 决定路径）
tests.fbasecman  →  platform_regress.cli（单用例/--suite/failed 全路径）
```
