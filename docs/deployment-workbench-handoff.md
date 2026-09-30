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
6. B 批次：~~现有集群修改的真实差异计划~~（已完成，见下）、多主机／凭据资源、分阶段恢复与 React Flow 自由拓扑编辑。

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

## 继续工作定位

- backend/platform_app/deployment/：models、probe_agent、probes、workbench。
- backend/platform_app/api/routes_deployment.py：工作台 API。
- backend/platform_app/api/routes_operations.py、actions.py：计划任务快照与执行验收。
- products/*/deployment/templates.py：产品模板编译。
- frontend/src/components/DeploymentWizard.tsx：新工作台。
- tests/test_deployment_workbench.py：20 项后端专项（含符号链接阻断、cman 导入派生、test_context 失效、预加载库存在性）。
- frontend/tests/deployment-workbench-fixture.py 与 deployment-workbench.mjs：隔离浏览器验收，JSON 夹具路径由服务启动打印。

临时调试脚本 frontend/tests/_mobile_dbg.mjs 与工作区 main 文件不属于本批，未纳入提交。临时隔离服务已关闭；没有创建活动目标／自动继续任务。
