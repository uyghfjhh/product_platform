# 当前实施状态

## 2026-10-01：目录与运行数据收敛

- 持久状态归 data，队列／PID／锁归 runtime，执行日志归 logs，报告归 output。
- 回归产物统一为 output/<产品>/<环境>/runs/<执行ID>/cases/<目标>，取消旧目录查找及报告镜像。
- 资源账本、失败重跑记录、执行历史、稳定性状态、测试上下文在持久区；报告清理不再影响它们。
- 依赖统一 pyproject.toml + uv.lock，删除 requirements.txt；历史评审／交接退出当前文档。
- 用户要求直接删除过期历史，不迁移旧报告。有效环境、拓扑、密钥和测试上下文保留。
- 验证：全量 434 passed（39.84 秒）、前端构建、改动模块 F/I、shell 语法、生成契约和 uv 冻结锁文件检查通过。隔离部署工作台与 MAC/MMR 报告浏览器验收通过。
- 正式服务在无活动任务、无运行产物所有者时停止并清理；旧报告／日志／已结束任务历史直接删除，不迁移。有效 cman-lab 测试上下文保存在 data/profiles/cman-lab/fixture。
- 服务已启动，线上健康确认 runtime、logs 分离，Web 日志位于 logs/web.log；data/platform 与 data/locks 已不存在。未修改实际数据库目录。隔离服务已关闭。

## 已实现

公共任务提交和部署恢复服务；实例资源锁；导入无运行副作用；Provider 注册表与能力校验；分段事件、任务分页及归档；前后端响应契约生成；统一 Studio 工作区及四套主题。


## 残留清理核对

- 初始化对外只保留 web.sh setup；实现移入 scripts/setup_env.sh，删除根目录 install_env.sh。
- 删除两个产品 legacy 目录的 ignored 运行残留及源码树 Python 缓存；没有删除产品用例源码。
- 删除过期 studio-panel-check.mjs，只保留覆盖统一工作区的 studio-workspace.mjs。
- 清理全 backend/products 的 F401/F841；JUnit skipped 元素仍创建，未删除副作用。
- 全量 434 passed（33.52 秒）、F401/F841、shell 语法与 git diff --check 通过。


## 2026-10-01：仓储组合、画布事件与生成产物

- FileStore 缩为 27 行组合根，业务分到 storage/environments、tasks、bindings、deployments、results、diagnoses。所有仓储共享 StorageBackend 的 flock／原子写；任务状态与事件仍在同一域提交。调用方显式使用各域，无旧平铺方法转发。
- 画布模板无 onclick 或 window.dashboard，data-action／data-node-id 由 React 容器委托；节点 ID 属性转义，恶意字符不会变成脚本。
- predev／prebuild 生成 API 类型与产品注册；两个 generated 文件忽略且不跟踪。删除本地产物后构建自动生成成功，后端契约测试在临时目录生成验证。
- 新增跨域保护／诊断失效及画布注入检查；全量 436 passed（32.19 秒），前端构建、F/I、格式检查通过。部署工作台和通用浏览器验收通过。
- 无活动任务时重启正式服务，健康、环境和任务 API 线上通过；隔离服务已关闭，没有执行数据库变更。
