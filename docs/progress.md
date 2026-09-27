# 实现进度

更新:2026-09-26。当前目标架构和实施路线以 [设计文档 v3](design.md) 为准。本文保留历史讨论和实现快照；其中出现的旧 `regress/`、`product_adapters/` 路径仅是历史记录，不代表当前目录结构。

## 2026-09-26:目标架构重定

- 用户确认:数据库集群部署、回归测试框架和 License 签发是平台公共能力；产品代码以 `products/<id>/` 为唯一接入单元。
- 首期部署只支持数据库集群，不做非数据库服务部署 demo。多活、等保、FBase 和 fbasecman 共用平台部署与回归内核。
- License 以 `fd_licenser` 的跨产品格式为参考:一份文件可授权多个产品，平台统一管理密钥、签名和格式兼容，产品仅声明授权编码和规则。
- v3 设计摆脱旧框架和旧目录的长期兼容约束；旧源码只作为业务知识和迁移材料。当前代码尚未按新设计实现，不能把本次文档重写视为阶段完成。

## 2026-09-26:产品包接入第一阶段

- 新增 manifest 解析与产品目录发现；fbasecman、FBase 已有产品 manifest，fbasecman 提供回归、常稳和 fixture 的产品包入口。现有实现仍依赖旧回归资产，尚未完成代码归位。
- 产品列表和环境动作已以已安装 manifest 为唯一来源；移除产品 manifest 后不再允许新建环境或任务，已有环境保留可读。删除行为有 API 契约测试。
- 动作执行 Provider、产品专属 API、结果发布和 License 产品选项仍有硬编码/旧配置，离“新增一个产品包、核心零修改”还有明显差距。CLI 产品包入口当前仍是过渡包装，不代表迁移完成。
- License 新签发选项已改为从已安装产品 manifest 汇总，并在签名前核对授权编码、版本和重复项；移除产品包后不能继续签发该产品。旧 License 的格式兼容测试仍使用 C 校验器。Provider 执行和产品专属 API 尚未完成迁移。
- Provider 固定产品映射已改为加载 `products/<id>/adapter.py`，用第三个临时产品验证仅新增产品目录即可进入执行分发，删除 manifest 后执行被拒。现有 fbasecman/FBase Provider 类仍位于平台 `providers.py` 并由产品 adapter 过渡绑定；业务实现归位和 API 产品端点归位尚未完成。
- fbasecman/FBase Provider 类与各自的用例发现脚本现已迁入产品 `adapter.py`；平台 `providers.py` 只保留通用契约、隔离发现工具、pgcluster 命令和产品包分发。fbasecman 观测、部署 profile、fixture 和报告产物解析已移入产品目录；PostgreSQL 复制观测抽到平台公共模块。旧适配器路径保留薄兼容导出。全量回归资产、产品专属 API 与结果发布解耦仍未完成。
- fbasecman 的 profile、报告、用例状态和日志 HTTP 端点已迁入产品 `router.py`；平台扫描已安装产品包并装配可选路由。临时第三产品验证只加产品目录即可提供端点，移除 manifest 后重建应用端点消失。平台 API 的测试目标校验、任务结果发布、全量回归资产和前端产品组件仍需解耦。
- 测试目标校验由 manifest 的 `validate_target` 声明控制；`ACTIONS` 只保留平台公共动作，产品动作从 manifest 动态解析。第三个临时产品验证了新增动作通过 HTTP 提交并由 Worker 真实执行，无需修改核心动作表。fbasecman/FBase 的进度观察、额外观测和当前结果发布已下沉到 Provider 可选钩子，任务终态仍由平台控制。旧回归框架和部分场景观测尚未平台化。
- 新增 `backend/platform_regress/` 的公共用例目录契约并接入 `/cases`：产品发现结果统一校验套件归属、重复目标和字段类型。真实 fbasecman 211 个、FBase 228 个用例均通过。当前只完成目录契约，fixture、执行、判定和报告仍由两套旧框架负责。
- 公共 `RegressionEngine` 已提供 setup/run/cleanup 生命周期、PASS/FAIL/BLOCKED/ERROR/CANCELLED 判定、独立清理结论、步骤事件、附件和原子 `result.json`；`platform_regress.cli` 可在隔离进程运行产品 `cases.py`。临时第三产品已通过 Web 提交、Worker 执行、Provider 发布当前结果的完整路径。现有 fbasecman/FBase 211/228 个真实用例尚未迁入新 SDK，旧框架仍承载它们。
- FBase 149 项框架单测改用其真实入口 `python -m unittest discover` 验证；MMR 自动化参考源码可通过 `FBASE_MMR_AUTOTEST_SOURCE` 指定，默认读取相邻 `postgresql_for_fbase_dev/mmr-autotest`。参考源码不存在时该项明确 skip，不伪称覆盖核对通过。
- 公共引擎的重复运行会生成新的 `execution_id`、重置事件序列并只引用本次附件；Worker 将任务 ID 传给 CLI，结果发布必须匹配本次任务，缺失/陈旧结果写 ERROR 覆盖旧 PASS。首条真实 FBase 用例 `mmr.installation.runtime_prerequisites` 已迁入产品 `cases.py` 和公共 SQL SDK：从 pgcluster 拓扑选择 mmr1/mmr2 主节点，执行原有只读配置、扩展和成员断言；无拓扑时通过 Web/Worker 路径发布 BLOCKED。其余 227 项 FBase 用例和 fbasecman 211 项仍走旧框架。
- 真实 `fbase-mmr` 专用环境验收该迁移用例：任务 `7293e1ae-ab47-486a-a943-173a6cf14695` 判定 FAIL，mmr1 的 `log_destination` 实测为 `stderr`，旧用例要求 `stderr,csvlog`。本次 23 条事件序列连续、5 个 SQL 附件存在、清理 PASS；这是环境配置与既有预期不符，未放宽断言或修改集群。
- 第二条真实 FBase 用例 `mmr.cluster_verification.basic` 已迁入公共引擎，覆盖 mmr1/mmr2/mmr3 本地状态、全集群状态和差异为空的原有 9 个步骤。`fbase-mmr` 专用三节点环境执行任务 `33b819cc-831c-4c7d-9893-1df066fb6dd6` 判定 PASS；9 个业务步骤、12 次 SQL、12 个附件和事件序列均已核对。FBase 目录仍为 228 个唯一目标，其中 2 个由新引擎执行，其他 226 个仍由旧框架执行。
- 用户指出顶层 `products/` 与 `regress/` 并存违背“一个产品一个目录”。已把两套回归工程机械移动到 `products/fbase-database/regression/legacy/`、`products/fbasecman/regression/legacy/`，保留原有源码、文档、CLI、测试和运行产物；平台默认路径、子进程搜索路径和产品 CLI 均指向新位置。两套 `show` 与用例发现已验证，fbasecman `env status` 能读取 14 节点拓扑；`doctor` 的远端 192.168.1.24 SSH 当前不可达。旧 `framework` 和嵌套业务包仍需抽取/去重，目录搬迁不等于架构迁移完成。
- 控制面数据已从数据库实例资源中分离：`data/platform` 保存 SQLite/队列/锁/操作日志，`data/environments` 保存 profile 和回归证据；SQLite 内环境配置路径已事务迁移。现有 `data/*-pgdata` 不搬动、不删除，作为目标数据库资源由 pgcluster 配置管理；新 profile 拒绝项目目录作为数据库数据根。
- `cman-lab` 已停止，数据库目录从项目内 `data/cman-lab-pgdata` 迁到 `/home/postgres/fbasecman_regress_v2_mmr_cman-lab`，profile/override 同步更新，CLI status 验证通过。`fbase-mmr`、`fbase-mac` 配置本来已指向 `/home/postgres/pgdata`；项目内同名旧目录未确认所有权，保留待后续逐实例核对，禁止盲删。
- 已确认项目内 `data/fbase-mmr-pgdata`、`data/fbase-mac-pgdata`、`data/smoke` 均无运行进程且不再被环境 profile 引用，迁移到 `/home/postgres/fbase-platform-legacy-pgdata/`；smoke YAML 已同步到外部路径。项目 `data/` 现在只保留平台控制面和环境证据。
- 前端产品组件已从 `frontend/src/product-adapters` 移到 `frontend/src/products/fbasecman`，测试导航改为按产品 manifest 动态生成；当前公共 App/页面仍需进一步拆分为 platform shell、platform views 和产品前端包。
- 前端平台壳已拆到 `frontend/src/platform/PlatformShell.tsx`，API 客户端已拆到 `frontend/src/platform/api.ts`，根文件只保留兼容导出；产品组件位于 `frontend/src/products/<id>/`。TypeScript/Vite 构建已通过。
- `TestsPage` 的产品差异已开始下沉到 `frontend/src/products/testRegistry.ts`：测试动作、套件筛选、报告、旧产物状态和终端能力由产品适配器声明，公共页面不再直接判断 fbasecman 字符串。剩余旧页面样式和部分 suite 展示逻辑仍待继续拆分。
- `DeploymentPage` 的 profile、fixture、产品工作区样式和远端数据根默认值已下沉到 `frontend/src/products/deploymentRegistry.ts`；公共部署页只消费适配器和数据库集群动作。前端构建已通过。

## 2026-09-25 下午:文档整合为三份

- 全部文档合并为 README.md(定位/快速开始/使用/会话规则)、docs/design.md(设计文档)、docs/progress.md(本文)三份:architecture/capability-roadmap/decisions/contracts/frontend-and-animation/dependencies 六份合并为 design.md 六个部分;usage.md 与 AGENTS.md 并入 README;implementation-plan.md(历史,已被路线图取代)直接删除。
- design.md 内部交叉引用已改为"第X部分"表述;各部分内部章节编号保持原样。
- 清理散落冗余文档:删除 `regress/fbasecman/` 根目录与 `docs/` 完全相同的 5 份 ISSUE 副本(保留 docs/ 下作知识库语料)及旧交接文档 `docs/框架重构会话交接_2026-09-23.md`(与参考工程原件一致,平台副本无独立信息)。`regress/fbasecman/` 其余文档与 `regress/fbase/` 全部文档为回归工程资产,保留。
- 方案新增两大平台能力方向(用户确认,已写入 design.md 第二部分):**2.10 回归测试框架平台化**——平台内现存两套互不兼容框架(fbasecman 包结构/对象注册、fbase 平铺/字典注册),以 fbasecman 框架为基座建立 `backend/platform_regress/`,等保/多活/fbasecman 三产品共用,产品工程只留用例与业务包(仓库根 `products/` 归位);**2.11 通用部署能力**——pgcluster 领域实现分两步迁入平台(先 DeploymentEngine 接口化、后实现迁入),图形化部署成为平台公共能力,支持非数据库服务。2.9 补充目录级产品生命周期(接入=新增目录,删除=删目录+孤儿数据只读+显式清理);第三部分"部署归属"决策已出 v2 修订;P1/P7 阶段内容相应更新;README 增加目录结构图。
- 能力缺口分析后新增五个能力域方案(用户确认不做用户身份与操作审计):**2.12 流水线与定时调度**(Huey periodic,步骤=普通任务事件串接,不建运行历史)、**2.13 通知与告警**(webhook/邮件,旁路不影响任务结果,零新增后端依赖)、**2.14 产品版本与产物管理**(诊断 running_binary_revision 闭环)、**2.15 交互终端**(@xterm/xterm 6.0.0 + addon-fit 0.11.0,WebSocket,本机受控 shell,命令留痕)、**2.16 数据库管理补全**(会话/锁/复制/参数视图 + 取消会话);新增 P8 阶段与 schema V7(pipelines/notifications/product_versions);xterm 版本已实查 npm 合规(2025-12-22 发布)。

## 2026-09-25 下午:路线图与依赖方案落盘

- 产出能力升级路线图(现 design.md 第二部分):九个能力域(代码覆盖率、压测、稳定性平台化、AI 网关与自动诊断、知识库、代码分析与问答、图形化部署、报告模板、架构收敛)的完整方案,P1–P7 实施阶段与真实验收标准;P1 为"架构深化"(动作注册倒置、结果发布归 provider、api.py 与 Store 领域拆分、TanStack Query、真实路由、OpenAPI 类型生成、mypy 起步)。
- 产出依赖清单(现 design.md 第六部分):分阶段依赖清单,版本号当日从 PyPI/npm 实查(后端 coverage/tree-sitter 系/jieba/mypy/pytest-cov;前端 echarts/react-query/mermaid/@dagrejs/dagre/openapi-typescript);明确不引入清单(Redis、LangChain 系、Locust、psutil 等)。
- 严格 review 后修订 12 处文档问题:失败聚类改为 `failure_stats` 聚合表(不违反无历史中心原则)、schema 改为 V3–V6 随阶段递增、metrics API 改为 `/operations/{task_id}/metrics` 任务级路径、docx 后置、AI 配置统一为 `data/ai_config.json`(不建表)、能力域数量对齐为九个等。
- 核实并放弃两条过时记录:`frontend/vite.config.ts` 开发代理已指向 8080(可用 `PRODUCT_PLATFORM_API_ORIGIN` 覆盖);`regress/fbasecman/tools/web_server.py` 已删除,报告解析走 `web_reports.py`。

## 2026-09-25 上午:回归工程完整迁入与报告解析切换

- 用户明确:`fbasecman_regress_v2` 的全部命令行功能和非 Web 回归实现进入平台;唯独部署执行由 `pgcluster` 承担。回归 Web 与图形化部署 Web 均以 8081 实际体验为基准在平台 React 中实现。
- 参考工程非 Web 源码与运行资源同步到 `regress/fbasecman`,保留此前已修改的 8 个文件;补齐 `products/`、共享 suite runner/runtime、SQL_PARSE 执行器、`web_reports.py` 等缺件。`run.sh show`、`run.sh run` 清单、`stable.sh show` 已能从平台副本执行;迁入的 259 项单测通过。
- `run.sh env` 与 `stable.sh env` 的部署子命令改经 pgcluster;平台真实配置的 `env status` 已读到 14 个节点。平台报告解析改用迁入的 `web_reports.py`,旧 `web_server.py` 及对应单测已删除。
- 平台新增 pgcluster `doctor` 与基于 health/restart/health 的 `heal` 操作;部署页已有与参考站同序的操作栏。回归页新增底部终端,日志来自增量任务事件,支持停止、复制、清屏和打开详情。
- 报告弹窗按参考站调整为深色步骤卡片、参考标签顺序、详情折叠与全屏入口;真实 `handover.ha_rep_promote` 报告显示 13 步、5 个检测项。移动端部署页横向溢出已修复。
- 新增 fbasecman 专属 React/GSAP 2D 报告拓扑(客户端、代理、双站点节点、探活与路由快照),步骤播放控件移到画布顶部;真实 `CORE-13` 报告浏览器验证 4 个节点、步骤 1→2 状态切换、无页面异常。
- 检查:平台 `45 passed`,迁入回归 `259 tests OK`,前端构建通过,`git diff --check` 通过;独立 8766 API + 5174 Vite 浏览器检查无页面异常,临时服务已停止。

## 目标边界(含用户最终要求)

- 建设跨产品公共平台。产品、环境、任务、证据和 API 契约属于平台;具体产品的命令、用例、报告解析与展示细节属于产品适配器。
- fbasecman 部署由平台调用 `/home/postgres/fly_dev/pgcluster`;图形化部署界面参考 8081 工作台。旧回归工程的 `env setup/start/stop/heal` 不作为平台部署入口。
- fbasecman 回归的**非 Web 逻辑全部复用** `/home/postgres/fly_dev/fbasecman_dev/fbasecman_regress_v2`:SuiteRegistry、用例、fixture、执行器、断言、报告和日志。先核对工作区 `regress/fbasecman` 副本与参考工程的差异,不在平台重写回归内核。
- Web 层在当前平台内实现;回归清单、统计筛选、执行反馈、底部终端、报告大弹窗及 3D/2D/节点卡片和步骤播放,要与 `http://192.168.0.12:8081/` 的交互和视觉效果对齐。**不能 iframe 嵌入 8081,也不能依赖其 Web 进程**。
- 产品事实、自动化判定、AI 推断分别表达;AI 不修改确定性测试结论。

## 当前实现

- 平台已有 FastAPI、React/TypeScript、SQLite/Huey、产品/环境登记、任务、结果、日志、License、数据库 SQL 和 pgcluster 部署入口(含 doctor/heal)。
- pgcluster 配置生成、拓扑、状态与生命周期操作已打通;部署页面仍与 8081 参考界面差距明显,需重做图形交互但保持 pgcluster 后端。
- fbasecman 旧回归用例通过隔离进程运行,平台同步当前结果和原始产物;报告解析复用迁入的 `tools/web_reports.py`。
- 测试清单为独立 React 实现,结构部分接近参考站;运行中反馈、终端和报告交互未完成对照验收。
- 报告查看器为大幅 Modal(3D/2D/节点卡片切换,复用 `ThreeTopologyView`),属过渡实现,3D 效果与 8081 不一致;报告快照含步骤描述推演,不能冒充实测状态。
- 产品适配器迁移进行中(2026-09-25 12:38 快照):`cman_artifacts`/`fbasecman_profile`/`fbasecman_fixture`/`legacy_cman_runner` 实现已迁入 `product_adapters/fbasecman/`,核心目录留兼容垫片;剩余耦合清单与目标结构见设计文档第二部分 2.9(`providers.py` 类、`api.py` 产品端点、`product_registry`、`config` 产品设置、`actions` 分支、前端页面类型)。

## 未完成项

- 2026-09-26：前端产品扩展配置已下沉到 `products/<id>/frontend.ts`；开发启动和构建扫描同时存在 `product.yaml`、`frontend.ts` 的产品包，生成 `frontend/src/products/generated.ts`。公共测试入口不再把未知产品误映射为 FBase，平台壳不再按产品 ID 选择测试模式。`npm run build` 通过。产品专属报告、终端和部署画布仍由公共页面直接导入，页面拆分及旧回归框架迁移尚未完成。
- 2026-09-26：fbasecman 的报告、终端、部署画布、场景详情及其资源已整体迁到 `products/fbasecman/frontend/`，公共页面通过生成的产品注册表取组件。前端依赖解析已覆盖包外产品源码，生产构建和类型检查通过。Vite 的 Lightning CSS 压缩器对迁移后的合并样式报 `Unknown at rule: @keyframes`，暂关闭 CSS 压缩；需定位并恢复。公共测试页和部署页仍包含较多旧布局与流程代码，尚需重构。
- 2026-09-26：核对回归调用链：产品 CLI 仍直接转发旧 `regression/legacy/run.sh`；Web 的 fbasecman 用例仍由旧框架运行；平台 `RegressionEngine` 当前仅执行两条多活用例，等保尚未迁入。公共 `CaseContext.command` 已增加无 shell 的参数数组执行、超时/取消进程组终止、输出证据和退出码返回；定向测试 10 项通过。接口是后续用例迁移基础，不代表旧用例已经平台化。
- 2026-09-26：等保 `mac.separation_of_duties.dba_metadata_access_restrictions` 已将旧四步业务断言迁入产品 `cases.py`，通过公共 `RegressionEngine` 执行；Provider 按 pgcluster 拓扑选择唯一主节点，SDK 保存预期 SQL 错误的 SQLSTATE 和文本。当前 15432 实例经平台 CLI 真实执行 PASS，证据在 `/tmp/product-platform-mac-check.XIp3Ox`，两次读表拒绝 SQLSTATE 42501，两次重命名被产品钩子拒绝。旧框架输出中未找到该目标历史报告，因此仅确认旧断言与当前实测一致，未完成历史结果逐文件对照。本阶段平台测试 77 passed，旧 FBase 149 OK、旧 fbasecman 259 OK。
- 2026-09-26：重启 8080 平台服务后，通过 Web 任务 `28de299a-b844-4ffd-8561-1c36e4bb8791` 真实执行同一等保目标，任务 SUCCEEDED，当前结果 PASS；`data/environments/regression/fbase-mac/mac.separation_of_duties.dba_metadata_access_restrictions/result.json` 的 operation_id 与任务一致并引用五份 SQL 证据。该目标的 CLI、Web、结果发布闭环已验证；其他等保与 fbasecman 用例仍在旧框架。
- 2026-09-26：按批量迁移方向，FBase 228 条旧用例（含完整步骤、断言和 fixture 声明）导出到产品 `regression/cases.json`；fbasecman 211 条旧用例目录元数据导出到产品 `regression/catalog.json`。平台发现已改为直接读取这两份产品数据，不再为用例清单启动旧框架。FBase 当前步骤分布：command 3837、sql 685、cluster_action 17、wait_sql 15、node_action 5、background_sql 3、wait_background_sql 3、system_time_shift 1；118 条使用 isolated_mmr_node_creation，25 条使用共享 session。fbasecman 是 Python executor 模型，不能按 FBase 的声明式步骤直接解释。批量导出尚未替代旧执行器，判定与清理协议仍需迁移验证。
- 2026-09-26：FBase 产品 CLI 新增 `platform-case`，可指定 `--node NAME=HOST:PORT`、`--output-dir` 直接运行已迁入公共引擎的目标；当前等保目标在 15432 实测 PASS。原 `run mac|mmr ...` 保留未迁移用例行为，不能把新子命令视为旧 CLI 全量替换。
- 2026-09-26：重启服务后 API 返回 FBase 228/228、fbasecman 211/211 个唯一目标，健康接口正常；平台测试 78 passed。按现有 FBase 定义筛选，只有 10 条同时满足仅 cluster fixture、仅 SQL/命令步骤且无共享 session；其余目标需要先实现隔离集群、专用 fixture、后台步骤或共享 session 的平台协议。导出清单覆盖全部目标，但执行器迁移尚未覆盖全部目标。
- 2026-09-26：FBase 四条事务内元数据拒绝用例批量接入平台执行器，在当前 MAC 主节点 CLI 实测全部 PASS，分别保存三份 SQL 证据。新增平台声明式 SQL 步骤断言器；多活 `mmr.cluster_verification.same_priority_errors` 复用导出的五步定义，在 mmr2 主节点 CLI 实测 PASS，保存六份证据。`ALTER SYSTEM` 目标未纳入无状态批次，避免意外放行时留下配置修改。
- 2026-09-26：按快速批量接线，FBase 228 个、fbasecman 211 个单目标均注册到公共 `RegressionEngine`。原生目标执行平台步骤；未重写的目标由产品包内过渡用例调用原有 fixture/步骤执行器，再由平台核对本次结构化报告、记录事件和证据并发布 PASS/FAIL/BLOCKED/ERROR。FBase 使用唯一 run ID 防止旧报告误判；fbasecman 使用报告更新时标及退出码交叉核对。FBase 旧 FAIL/BLOCKED 和陈旧/错目标报告、fbasecman 旧 FAIL/陈旧报告已用受控测试验证。**这只是批量接入平台生命周期，旧执行器尚未删除，不等于全部业务步骤已原生迁移。**
- 2026-09-26：FBase 非原生 `mac.audit.log_access_restrictions` 经 Web 任务 `272135b9-0d1c-4d7a-b973-58e6997a1704` 实测 SUCCEEDED/PASS，平台结果绑定任务并保存命令与本次旧 summary。fbasecman `guc.search_path_reuse_sql_parse` 经产品 CLI 进入平台后取得本次 FAIL 报告，原因是当前测试环境 127.0.0.1:15011 拒绝连接；此前历史 PASS 不能覆盖当前失败。平台测试 88 passed；当前 fbasecman 环境需恢复后再作业务对照。
- 2026-09-26：平台 `CaseContext.attach_file` 已增加本次文件证据归档；两产品过渡层把旧报告与日志复制进平台 execution 目录。FBase `mac.audit.log_access_restrictions` 本次 PASS 归档 summary、report、execution.log、postgresql.log、postgresql.csv；fbasecman `guc.search_path_reuse_sql_parse` 当前 FAIL 归档 summary、report 和日志。文件丢失或路径不属于本次 FBase run 会导致 ERROR，不再只保留旧绝对路径。平台现有 cman-lab profile 的 PGDATA 均在项目外 `/home/postgres/fbasecman_regress_v2_mmr_cman-lab/`，但 127.0.0.1:15011 当前无响应；尚未恢复环境验证业务 PASS/FAIL 对照。
- 2026-09-26：确认环境绑定模型改为“回归 profile -> 一套环境”，而不是“产品 -> 一套环境”。新增 `regression_bindings(product_id, profile_id, environment_id)` 表和 API；环境仍有唯一产品所有权，但同一产品可有多套环境。FBase manifest 声明 `mmr`（多活）和 `mac`（等保）profile，fbasecman 声明 `cman` profile；平台提交测试任务必须命中对应绑定和部署目标。部署页提供 profile 绑定选择，测试导航按 profile 生成独立入口。平台测试 91 passed，前端 build 通过。
- 2026-09-26：按用户要求选择 fbasecman 现有框架的通用内核作为平台迁移基础；已将其 `execution`、`evidence`、`persistence`、`reporting` 四组公共模块搬到 `backend/platform_regress/`，产品运行时导入改为平台路径；fbasecman 旧环境搭建器没有迁移，数据库环境继续统一由 pgcluster 管理。旧框架 259 单测此前通过，迁移后正在修正系统 Python 3.8 兼容入口和遗漏导入。
- 2026-09-26：继续迁移 fbasecman 公共内核：`CaseRuntime`、suite contracts、suite runner 已进入 `backend/platform_regress/`；旧 `framework/suites/{runtime,contracts,runner}.py` 仅保留 re-export 兼容层，避免旧 CLI 和单测断裂。平台 Runtime 延迟导入旧环境配置，不把 fbasecman 环境搭建器带入平台。验证：平台测试 91 passed，fbasecman 旧单测 259 OK。
- 2026-09-26：数据库环境能力正式收口为平台 `PgclusterDatabaseProvider`，提供统一 `validate/plan/apply/inspect/lifecycle` 契约；动作层通过该 Provider 生成 pgcluster 计划，产品不再实现底层集群生命周期。Provider 契约测试已加入，平台全套测试 93 passed，前端 build 通过。
- 2026-09-26：按快速批量迁移要求，`platform_regress.cli` 新增 suite 批量执行模式（`--suite`），逐目标输出独立 result/evidence，并写入聚合 `suite-result.json`；不再要求人工逐条调用和验收。单条目标入口保持兼容，批内 FAIL/BLOCKED/CANCELLED 维持原判定。
- 2026-09-26：Web/CLI 的整组 suite 目标已改为统一调用 `platform_regress --suite`；FBase 和 fbasecman Provider 不再直接把 suite 目标交给旧聚合 runner。平台负责逐目标生命周期和批量聚合，旧执行器只作为尚未原生改写的产品实现。Provider/API/Runtime 定向测试 32 passed。
- 2026-09-26：按“先整批迁移代码、后调试”的要求完成本阶段结构切换：产品 provider 入口统一为 `provider.py`，单条和 suite 目标统一进入平台批处理器，suite 结果逐条发布；平台 Runtime 公共模块和 pgcluster Provider 已进入平台目录，旧框架仅保留产品专属实现/兼容入口。一次性验证：平台 94 passed，fbasecman 259 OK，FBase 149 OK，前端 build 通过。

- 路线图 P1–P6 未开始(见 [设计文档](design.md) 第二部分第 4 节)。
- P7 相关遗留:报告 Modal 的 3D canvas 像素/取景/步骤播放/节点点击/手机截图未验收;部署画布需重做(深色完整画布、节点操作);2D 拓扑需继续按参考截图校准布局、连线、3D 视角和动效;`stable.sh env` 需要独立 stable pgcluster 配置才能实际运行;真实执行/停止及部署流程端到端验证未做。
- 参考基线材料:`fbasecman_regress_v2/tools/web/{index.html,style.css,app.js}`、`tools/web_reports.py`、`tools/web_tasks.py` 与运行中的 8081 页面;参考截图 `/tmp/reference-core13-2d-real.png`、`/tmp/platform-core13-2d.png`(临时文件,丢失则以 8081 实际页面为准)。
- 当前服务:`http://192.168.0.12:8080` 运行中(2026-09-25 核实 health 200);8081 仅为参考,不是运行依赖。

## 验证现状

- 最近平台测试 `45 passed`;前端 `npm run build` 通过;回归 259 单测通过。
- 上述验证只证明当前代码能运行,**不证明回归/部署页面与 8081 一致**;报告 Modal 的 3D canvas、节点交互、步骤联动和移动端尚未独立验收。
- AI 在线调用、全套件执行、正式稳定性时长、故障切换全量验收仍未完成。

## 历史教训(不得重复)

- 曾把 fbasecman 回归交互做成另一套通用 React 测试页,把产品字段逐步塞进公共动画层——方向和效果均被用户否定。
- 曾短暂加入 8081 iframe 入口,已撤回;不要恢复,也不让平台依赖 8081 服务。
- 曾持续扩展 `SHOW` 字段解析和简化 React Flow 画布,并把测试通过当作动画完成——不能证明与参考站一致;报告快照按步骤描述推演不能当作实测状态。
- 文档曾出现重复编号、过时测试数和互相矛盾的结论;新文档必须每条可落地、与代码事实核对。

## 工作区注意

- 工作树有大量未提交修改与未跟踪产物(回归源码、前端、平台代码、core dump 与锁文件)。不要 `git reset`、批量清理或删除 core/锁文件;只编辑明确涉及的文件,先核对当前内容。
- 追溯历史结果:读取 Git diff、测试产物及 `data/legacy_cman/` 原始报告;`data/platform.sqlite3` 为当前数据。
- 每阶段收尾更新本文件,并跑全量验证(平台测试 + 回归 259 单测 + 前端 build)。
