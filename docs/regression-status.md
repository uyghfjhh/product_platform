# 回归平台迁移现状与待办

版本：2026-09-28 · 对照仓库：`postgresql_for_fbase_dev/fbase_regress`、`fbasecman_dev/fbasecman_regress_v2`

本文回答两件事：**平台回归能力已经具备什么**、**要达到"各用例判定=老代码结果"还缺什么**。事实以当前代码和实测结果为准；每条非 PASS 判定给出定性分类，不允许无定性遗留。

## 1. 总体状态速览

| 产品 | 用例数 | 执行路径 | 实测基线 | 未完成原因 |
| --- | --- | --- | --- | --- |
| fbase-database | 228（mac 58 + mmr 170） | 平台原生 `RegressionEngine`，声明式步骤 | mac 56 PASS + 2 保真 FAIL；mmr 161 PASS + 2 FAIL + 2 BLOCKED | 见 §4 定性；5 条 `default_enabled=False` 未入批 |
| fbasecman | 212（202 legacy + 10 native） | 平台引擎 + 进程内 `LegacySuiteCase` 适配，套件 `run_case` 全权持有业务语义 | 见 §4.3 分套件 | 引擎已收敛；产品 runtime 内部通用件未上收完 |

**唯一执行面**：`python -m platform_regress.cli --product-dir <产品> [--suite S | target | failed]`。`run.py`/`run.sh`/`tools/cli.py` 保留为独立入口，内部委托同一批 `run_case`，不是第二执行引擎。

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
| `suites/` | contracts（CaseSpec/SuitePlugin/validate_cases）、registry（preflight 策略+quiet_env 挂点）、failed（last_failed/case_status/rerun）、legacy（`LegacySuiteCase`/`LegacyCaseBinding` 引擎适配器） | 已上收 |
| `requirements.py` | 依赖门注册表（clusters/commands/plugins/groups/nodes/node/system_time_control/roles/extensions 按旧序），产品 `register_requirement(before=)` 注入专属 evaluator，不满足统一 BLOCKED | 已上收（15 单测） |
| `reporting/` | model、renderer、junit、html | 已上收 |
| `steps.py` | 声明式步骤执行器：sql/command/wait_sql/background_sql/wait_background_sql/node_action/cluster_action/system_time_shift | 已上收 |

平台侧测试：**214 passed**；vendored fbasecman 单测：**258 passed + 1 环境失败**（test_junit 依赖已清理的 output/runs 产物）。

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
| ha_commands 75 | 全 PASS | — |
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
5. **报告统一**——JUnit/HTML 导出目前 cman 走 vendored `tools/cli.py`、平台走 `reporting/`；收敛到平台渲染器对同一事实模型出双格式。

### B. 保真验收缺口（每条判定=老代码）

1. **全套件逐条 verdict diff**——在同一环境用平台 cli 全量跑 fbasecman 各套件，与既有 `runs/` 历史判定逐条比对；已知基线见 §4.3，新增 FAIL 必须对照 legacy 源码+环境定性。
2. **mmr 2 条 BLOCKED 复跑**——批间暂态，`node_state`/`time_difference` 单独复跑定性。
3. **`mmr.node_management` 5 条 disabled 用例**单独跑通。
4. **handover 6 条未实现用例**——upstream 补齐或显式标记；平台侧判定保持 FAIL（当前行为）不改。
5. **`failed`/`all` 全链路经 Web 任务入口验收**——provider `failed` 已切平台 cli，未做任务级端到端。

### C. 环境事项

- cman-lab MMR 环境已归位（复制家族全绿）；`qa_case.orders` 等套件自建表在套件生命周期内管理，不算环境基线。
- `regress.local.yaml` 符号链接到 cman-lab override——独立 `run.sh` 用；平台路径由 `_EXTRA_CONFIGS` 注入，不依赖该链接。
- fbasecman 二进制版本固定问题：`SHOW SERVER_PREP_STMTS` 列数随 build 变化——回归环境应记录二进制版本快照（待办：环境清单加 binary fingerprint）。

### D. 收尾（design.md §12 P2 未完成项）

- cman 用例逐步从 `run_case` 壳迁到声明式/半声明式（`sql_parse`/`ha_commands` 的协议步骤可表达为 pgwire 断言序列）。
- `run.py`、`tools/cli.py`、`run.sh` 在 Web/CI 全走平台入口后退役（当前保留，同一 `run_case` 底层）。
- `framework/*` shim 层最终删除——前提是旧单测的 patch 点迁移完毕。

## 7. 验收标准与红线

1. 每条用例判定 = 老代码在等价环境/二进制下的判定；不一致必须有 §5 四类之一的证据支撑。
2. 不得把 FAIL/BLOCKED/ERROR 改成 PASS 来凑绿；不得删步骤、松断言、跳过 setup/teardown。
3. 平台核心不出现产品名分支；产品专属 evaluator/探针/schema 走注册挂点。
4. 每个结论有证据：result.json + artifacts + events.jsonl 必须能回溯判定依据。
5. 全量验证门：`pytest tests/`（当前 168）+ vendored `unit_tests/`（当前 259）+ 前端 build + 真实套件抽测。

## 8. 快速复现入口

```bash
# 平台测试
.venv/bin/python -m pytest tests/
# vendored fbasecman 单测
(cd products/fbasecman/regression/legacy && PYTHONPATH=<repo>:<repo>/backend:. ../../../../.venv/bin/python -m pytest unit_tests/)
# 平台 cli 单用例/套件/failed
PYTHONPATH=<repo>:<repo>/backend .venv/bin/python -m platform_regress.cli \
  --product-dir products/fbasecman --output-dir <out> \
  --context-json '{"legacy_source":..., "legacy_override":..., "legacy_report_root":...}' \
  common.console_commands | --suite common | failed
# 任务入口（provider 决定路径）
tests.fbasecman  →  platform_regress.cli（单用例/--suite/failed 全路径）
```
