# 实现进度

更新:2026-09-25(下午修订)。当前实施目标以本文件与 [设计文档](design.md) 为准(P1–P7 阶段与能力方案见其第二部分;新增依赖版本与阶段见其第六部分)。原会话交接文档 HANDOFF.md 的持久内容已并入本文(目标边界、历史教训、未完成项、工作区约束),原文件已删除。

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
