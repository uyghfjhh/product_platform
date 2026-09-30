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

1. 补齐 cman 既有集群导入时的辅助回归配置重新派生、旧 test_context 失效规则；确认逐节点数据目录自定义与 cman runtime 的固定根目录约定一致。不能在此之前直接进行 cman 新路径真机验收。
2. 为 Cman 模板明确限制暂不支持的节点目录布局，或补齐完整映射；参数校验再覆盖共享预加载库实际文件存在性。
3. 最新的实际目录符号链接进入平台项目目录阻断规则需增加专门测试（已添加实现，尚未跑该规则专门用例）。
4. 再跑最终全量与更新后的 browser-smoke；检查计划失效、接管版本不匹配、远程失败和安装工具混版本等路径。
5. 完成上述检查后，在无活动任务的窗口重启运行平台加载新 API，核验四个既有环境；让用户审阅具体方案后再做真机部署，不自动使用当前集群做初始化验证。
6. B 批次：多主机／凭据资源、现有集群修改的真实差异计划、分阶段恢复与 React Flow 自由拓扑编辑。

## 继续工作定位

- backend/platform_app/deployment/：models、probe_agent、probes、workbench。
- backend/platform_app/api/routes_deployment.py：工作台 API。
- backend/platform_app/api/routes_operations.py、actions.py：计划任务快照与执行验收。
- products/*/deployment/templates.py：产品模板编译。
- frontend/src/components/DeploymentWizard.tsx：新工作台。
- tests/test_deployment_workbench.py：12 项后端专项。
- frontend/tests/deployment-workbench-fixture.py 与 deployment-workbench.mjs：隔离浏览器验收，JSON 夹具路径由服务启动打印。

临时调试脚本 frontend/tests/_mobile_dbg.mjs 与工作区 main 文件不属于本批，未纳入提交。临时隔离服务已关闭；没有创建活动目标／自动继续任务。
