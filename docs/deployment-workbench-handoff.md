# 部署工作台交接记录（2026-09-30）

## 已实现

- 方案文档：docs/deployment-workbench-plan.md。当前为 A 批次实现，尚未完成全部真机闭环。
- 平台公共 API：模板目录、草稿保存／恢复、自动环境 ID、已有环境导入、YAML 目标解析、布局生成、安装探测、检查和生成计划、文件预览／下载、环境自动关联、提交计划任务。
- 产品编译器：等保 3 节点、多活 6 节点、fbasecman 14 节点；核心不按产品分支。FBase 测试上下文读取生成的 regress.override.yaml，避免新端口／目录下仍执行旧环境；逻辑订阅场景正确选择发布端主库。
- 计划不可变文件摘要、草稿 revision、环境基线检查；新建拒绝非空数据目录和端口冲突，接管／导入只提交健康检查，不初始化；worker 验证快照并重新探测。按计划创建后增加健康验收。
- 新 DeploymentWizard 四步页面、生成 YAML 预览、草稿选择和自动环境关联；替换旧三字段向导，部署按钮转入方案流程。旧 API 继续保留。
- 探测支持当前用户的本地／SSH 配置和 agent；目标端 Python 3，PGHOME、工具版本、pg_config 布局、扩展、License、端口／目录检查。显式多 SSH 用户、端口和密钥资源未实现。

## 验证结果

- pytest 全量 372 passed / 45.53s（当时新增工作台测试为 10 项）；随后增加 worker 健康验收失败与排队后出现数据的两项测试，工作台专项 12 passed / 19.63s。
- 前端 TypeScript／Vite build 通过。
- 隔离 Playwright 验收通过：新建草稿、安装发现、实例配置、保存／恢复、检查计划、YAML 预览、未确认部署按钮禁用、仅关联环境后自动出现三节点图。
- 浏览器夹具使用模拟数据库工具，身份握手防止误连正式 API。未执行真实初始化、部署、重启或清理；运行平台后端未为本批重启。
- 工作台专项测试依赖 sibling pgcluster checkout，缺失时标注 skip；实际配置校验已在当前 checkout 执行。

## 下次优先处理（不能当作已完成）

1. ~~补齐 cman 既有集群导入时的辅助回归配置重新派生、旧 test_context 失效规则；确认逐节点数据目录自定义与 cman runtime 的固定根目录约定一致。~~ **已完成**（见下"后续批次进展"）。
2. ~~为 Cman 模板明确限制暂不支持的节点目录布局；参数校验再覆盖共享预加载库实际文件存在性。~~ **已完成**：目录布局显式拒绝（约定 `<root>/<name>`），预加载库 `.so` 存在性已纳入探测。
3. ~~符号链接进入平台项目目录阻断规则的专门测试。~~ **已完成**：两条专项用例（data_root 符号链接入平台目录、两节点目录经符号链接解析到同一实际目录）。
4. ~~再跑最终全量与更新后的 browser-smoke~~。**已完成**：全量 385 项通过；browser-smoke 修正节点点击落点（卡片中心命中 SQL 快捷行是既有设计，详情抽屉须点标题区）；部署工作台隔离浏览器验收通过。远程失败与安装工具混版本路径仍未覆盖。
5. ~~重启运行平台加载新 API，核验既有环境。~~ **已完成**：机器重启后的空闲窗口启动服务，模板目录（mac/mmr/cman）与三环境拓扑核验正常（fbase-mmr 6、fbase-mac 3、cman-lab 14）。真机部署仍需用户审阅方案。
6. ~~B 批次：现有集群修改的真实差异计划、显式主机／凭据资源 + 多主机安装分配、可安全恢复的分阶段 checkpoint、React Flow 自由拓扑编辑~~（四项均已完成，见下）。

## 后续批次进展（2026-09-30 续）

- **cman 导入辅助配置派生**（`products/fbasecman/deployment/templates.py::import_files`）：导入 `mmr.fbasecman_regress` 时按实测 facts 重新派生 `regress.override.yaml` 与合并版 `regress.yaml`——端口（含 `mmr<i>_standby<j>` 历史标量键与 `*_standbys` 列表同步）、`mmr_host`/`mmr_postgres_dir`/`mmr_data_root`、`enable_citus`（取自部署配置 mmr 集群 extensions，VALIDATE_SCRIPT 新增 `cluster_extensions` 事实）、framework 输出目录。缺节点或目录不满足 `<root>/<name>` 约定时显式拒绝。
- **test_context 失效规则**：`Workbench.associate()` 在部署配置内容摘要变化时调用产品 `deployment_invalidate` 钩子（fbasecman 删除 `test_context.yaml`）；worker 侧 `after_command` 钩子在 `deployment.create/clean/rejoin/restore`（无论成败，部分节点可能已重建）后同样失效。启停与 failover/switchover/reset 不触发——节点身份未变。
- **cman 目录布局限制**：`compile_template` 节点覆盖只允许改端口，`data_dir` 必须等于 `<data_root>/<name>`，否则 422。端口覆盖现在会同步 `mmr<i>_standby<j>` 标量键（原先只更新 `*_standbys` 列表导致标量失配）。
- **预加载库存在性**：`shared_preload_libraries` 成员在插件声明中标记 `preload_library`，探测核对 `pg_config --pkglibdir` 下对应 `.so` 真实存在（此前只查 `.control`）。
- **平台钩子契约**：`deployment_import_files(settings, facts, target, environment_id)` 增加 environment_id 参数（fbase-database 透传兼容）；新增 `deployment_invalidate(settings, environment)` 可选钩子。
- **浏览器冒烟修正**：`browser-smoke.mjs` 节点点击改点 `.node-label-text`——卡片几何中心命中"💻 SQL 控制台"行是设计行为（遗留语义：DB 节点主击区进 SQL），详情抽屉走标题区。README 备忘已更新。
- 验证：全量 385 passed（新增 11 项）；tsc/Vite build 通过；`deployment-workbench.mjs` 隔离验收通过；`browser-smoke.mjs` 对运行中实例通过。

## B 批次进展：真实差异计划（已完成第一项）

- **语义差异检测**（`workbench.py::diff_configs/diff_operations`）：导入模式下草稿 YAML 与当前 `deployment_config` 语义不一致时，计划切换为 `mode="diff"`——逐实例对比 host/installation/port/data_dir、参数集、四类拓扑段，忽略文本格式与键序。
- **唯一可自动执行操作**：向既有流复制集群追加备库（`add_standby`）——pgcluster `create_streaming`/`create_mmr` 幂等，发布配置后重放 `create <target>` 只物化缺失节点。判据：新实例仅出现在既有 `streaming_clusters` 的 standbys 追加项中，且追加项全部属于本批新增实例。
- **显式拦截**：删节点（实例级 clean 不卸复制槽，pgcluster 缺口）、改端口/目录/主机/安装（重建级）、postgresql 参数差异（无应用原语）、其它拓扑段变更 → 如实列出 `executable=False` 操作，`verify()` 与 apply/operations 双入口 422，前端按钮禁用。
- **逐节点探测语义**：diff 计划把新增节点标记 `existing=False`（空目录+端口可分配+父目录可写），既有节点按接管口径（PG_VERSION 匹配）。`inspect_plan` 与 worker 执行前重检共用该语义。
- **前端**：差异计划横幅（可执行=警告/不可执行=错误）、操作表新增类型与可执行列、确认框文案区分扩容与初始化。
- **验证**：新增 5 项专项（扩容可执行端到端含 apply 202、缩容列出但双入口 422、端口/参数变更拦截、规范重发仍触发上下文失效、漂移导入被拒且上下文保留）；工作台专项 27 passed，全量 392 passed；真机 fbase-mac 三场景实测通过（原样=纯接管、+备库=可执行 diff、改端口=422）。
- **已知边界**：cman/MMR 加成员暂不支持自动执行（`create_mmr` 虽幂等但 join 语义与流复制不同，保守拦截）；移除节点需 pgcluster 补实例级元数据卸载原语后才有安全缩容路径。

## B 批次进展：显式主机/凭据资源 + 多主机安装分配（已完成第二项）

- **pgcluster 侧**（仓库已推送 `288aa6c`）：`hosts.<name>.ssh = {user, port, identity_file, connect_timeout}` 经模型校验（POSIX 登录名、端口范围、密钥绝对路径、未知字段拒绝、address 全局唯一）；`Executor` 按地址查凭据构造 `ssh -o BatchMode -o ConnectTimeout [-i key] [-p port] [user@]address`，`Runtime` 默认从 `config.hosts` 注入——即执行层真实使用声明凭据，而非装饰。
- **平台 spec**：`DeploymentSpec.hosts`（≤16，名称/地址唯一，有声明时 spec.host 必须落在其中）、`NodeOverride.host`（主机资源名，空=主表单主机所在资源）、`HostResource.home` 覆盖该主机安装目录。`DiscoveryInput.ssh` 允许探测带凭据。
- **探测分发**：`probes.probe(host, req, ssh=…)`；`VALIDATE_SCRIPT` 导出 `facts.hosts`（name→address+ssh），`inspect_plan` 按节点地址映射凭据——导入 YAML 自带 ssh 段同样生效。
- **产品编译器**：fbase-database 与 fbasecman 均支持 `spec.hosts`——hosts 段带 ssh 段、节点按 `override.host` 归属、按主机 `home`（缺省 spec.home）去重生成 `deploy_postgres[_<host>]`/`regress_postgres[_<host>]` 安装条目；cman 的 `mmr_host` 跟随 test_mmr1 所在主机。
- **前端**：步骤一折叠面板"多主机与 SSH 凭据"（名称/地址/用户/端口/密钥/安装目录），步骤二节点主机列变为下拉（地址→资源名映射保存）。
- **校验**：工作台专项 31 passed（多主机编译、逐节点归属、安装去重、cman ssh 端到端 validate_config、重复/孤儿主机拒绝）；全量 396 passed。
- **边界**：凭据仅存密钥路径不存口令（走 agent/密钥文件）；sudo 安装扩展沿用目标主机本地 sudo 规则。

## B 批次进展：可恢复分阶段 checkpoint（已完成第三项）

- **断点锚点**：pgcluster `create` 本就是逐节点幂等（受管节点跳过），真正缺的是"部分完成后重放计划会被非空目录检查挡住"。`probe_agent` 现在识别 `.pgcluster-managed` 标记——有标记且标记声明的 node 名与本节点一致的目录按"既有受管实例"校验（PG_VERSION 匹配即可，跳过空目录/端口检查）；无标记或归属不匹配的目录仍按原口径拦截。断点是引擎自身写下的持久事实，不是平台附加状态。
- **重放提交**：apply 的幂等键改为 `deployment-plan:<id>` → `deployment-plan:<id>:attempt-<n>`——进行中的重复提交仍由 `associate` 的活动任务检查拦截，已终结尝试不再吞掉恢复提交。`GET /plans/<id>` 现在返回 `attempts[]`（task_id/status/时间）供前端展示断点历史。
- **恢复路径**：失败的 `deployment.create` → 重新 apply 同一计划（或重新生成计划）→ 受管节点幂等跳过、缺失节点继续创建 → 部署后健康验收照旧。worker 执行前检查（`worker_environment`）复用同一探测语义。
- **前端**：计划步骤在存在历史尝试时显示警告横幅与各次任务状态。
- **验证**：新增 2 项专项（部分受管→重放产生新尝试任务且 attempts=2；异集群标记不算断点）；全量 398 passed。

## B 批次进展：React Flow 自由拓扑编辑（已完成第四项）

- **后端契约**：`DeploymentSpec.mode="free"` + `cluster_name`（默认 `cluster`）；`compile_spec` 在固定模板校验之前分流到平台自有编译器 `deployment/freeform.py`——产品是承载 `product_id` 的关联对象，自由拓扑不依赖产品固定模板。
- **编译产物**：单个 `streaming_clusters.<cluster_name>`（恰好一个主节点 + 任意备库，`replication.mode=async`），复用 B2 的 `spec.hosts`/`NodeOverride.host` 多主机与 SSH 凭据模型、按主机 `home` 去重生成安装条目；action=`deployment.create`，probe 按新建口径（`existing=False`），断点重放、差异展示、attempts 历史等 B3 能力自然继承。
- **校验防线**：编译期友好错误（无主/多主/重名/非法标识符/缺安装目录/孤儿主机引用），结构安全仍由 `validate_config` 统一兜底（绝对路径、禁 `..`、禁平台目录、同主机端口目录唯一）。
- **前端**（`FreeTopologyEditor.tsx`，`@xyflow/react@12`）：画布自由拖拽摆放、添加主/备节点（主节点唯一性自动降级）、点选右侧编辑名称/角色/端口/目录/主机，连线按角色派生（流复制）；`nodes` prop 变化时保留拖拽位置重映射（草稿异步回填不再空白），改名保持选中态；节点卡片显示 `名称·角色·端口`。
- **验证**：工作台专项 35 passed（自由拓扑编译/校验/计划生成）；`frontend/tests/free-topo-check.mjs` 隔离 fixture 浏览器验收通过（画布渲染、增删节点、改名、计划检查通过、YAML 含 streaming 集群）；tsc/Vite build 通过。
- **已知边界**：自由拓扑仅覆盖单流复制集群（MMR/Citus 仍走产品固定模板）；React Flow 连线为角色派生的展示元素，不支持手动连边自定义复制层级（级联备库暂不支持）。

## 数据库管理补全（能力域 2.16）与 review 修复（最近批）

- **数据库管理补全**：`database.py` 新增 `list_sessions`（pg_stat_activity 含等待事件）、`list_locks`（pg_locks+pg_blocking_pids 阻塞链）、`list_replication`（发送端/接收端/复制槽三视图）、`list_settings`（非默认值+常用项/ILIKE 检索，%_ 通配符转义）、`cancel_backend`（pg_cancel_backend / terminate 走 pg_terminate_backend）。API：`GET …/sessions|locks|replication|settings?port=`、`POST …/sessions/{pid}/cancel`；所有视图接受 `port` 选节点，环境不存在先于查询报错（404 不被 except 吞成 422）。前端 DatabasePage 新增"实例运行状态"Tabs：会话表含取消查询/终止会话（Popconfirm）、锁表、复制三表、参数表（检索框）。
- **Review P1-1 差异完整性**：`diff_configs` 曾只比对实例引用名——主机地址、安装 home、HBA 改了依旧 `executable=True`。现补齐 `hosts`/`postgresql_installations` 逐字段 diff、`postgresql_config` 除 parameters 外全部键（hba/replication_capacity 等）、未知顶层段兜底——一律列出为不可执行操作（kind=host/installation/config/section），associate/apply 双入口 422。
- **Review P1-2 草稿主机分配丢失**：保存时按"地址→资源名"反查，草稿重载后存的是资源名 → 二次保存置空。统一：节点 host 全程用资源名，layout 响应在摄入时按地址映射回资源名，Select 选项改 value=name，无资源声明存空（=主表单主机）。
- **Review P1-3 探测接口 500**：`call()` 不转发 kwargs，`probe(…, ssh=ssh)` 必 TypeError。已修 `call(*args, **kwargs)` 并补端点级回归（无 ssh 与带 ssh 两路径）。
- **Review P2 阶段日志覆盖**：`_run_command` 以 "w" 打开任务日志，部署后健康验收复用 task_id 截断部署输出。改 "a" 追加 + 每段写 `===== 时间 命令 =====` 分隔行。
- **入队一致性**：apply 顺序保持 关联→入队（关联的活动任务守卫保护发布）；入队失败时 500 明确"已关联未执行"语义与重放恢复路径（幂等键按已终结尝试计数，失败入队不烧号）。
- **验证**：新增 `test_database_admin.py` 8 项（假 psycopg）+ workbench 4 项契约测试（discover kwargs、host/install/hba/未知段拦截、草稿再保存、阶段日志）；全量 412 passed；tsc/Vite build 通过。

## 继续工作定位

- backend/platform_app/deployment/：models、probe_agent、probes、workbench。
- backend/platform_app/api/routes_deployment.py：工作台 API。
- backend/platform_app/api/routes_operations.py、actions.py：计划任务快照与执行验收。
- products/*/deployment/templates.py：产品模板编译。
- frontend/src/components/DeploymentWizard.tsx：新工作台。
- tests/test_deployment_workbench.py：35 项后端专项（含符号链接阻断、cman 导入派生、test_context 失效、预加载库存在性、多主机分配、断点重放、自由拓扑）。
- frontend/tests/deployment-workbench-fixture.py 与 deployment-workbench.mjs、free-topo-check.mjs：隔离浏览器验收，JSON 夹具路径由服务启动打印。

临时调试脚本 frontend/tests/_mobile_dbg.mjs 与工作区 main 文件不属于本批，未纳入提交。临时隔离服务已关闭；没有创建活动目标／自动继续任务。
