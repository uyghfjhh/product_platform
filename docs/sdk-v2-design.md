# 回归 SDK v2 设计与验收

状态：实现与契约验证完成（348 项平台测试通过，前端构建通过）；真实集群套件保真验收待执行。SDK 版本与事件/结果文件 schema 版本独立。

## 设计边界

v2 的设计依据是产品实际需要的回归能力，不以旧 SDK 导入路径或旧 runtime
构造参数作为兼容要求。仓库内产品、CLI 和测试同步迁移，不提供 v1 facade、
弃用别名或旧版本兜底。业务准备、步骤顺序、断言、清理、判定与报告证据是
行为基准；历史数据文件不能因为 SDK 升级而删除。

不在本批次合并三个产品 runtime，也不声明式重写 executor 用例。两者属于
产品实现，继续通过明确的 v2 契约运行；它们不是 SDK 的设计依据。

## 公开契约

- `platform_regress.sdk` 是类型与核心接口的唯一公开入口。
- `RegressionCase.run(context)` 是最小用例契约；`SetupCase`、`CleanupCase`
  明确声明可选的准备/清理能力。
- `CaseContext` 是一次执行的能力入口，不能跨次执行复用。每次执行新建 context；
  用例对象可以顺序复用，其实现必须重置运行状态。
- `CaseEnvironment`、`NodeEndpoint` 定义平台字段；产品额外字段由产品定义。
  输入容器在构造时校验，实际使用节点时校验 host/port，前置条件错误保留 BLOCKED。
- `CaseResult` 的业务结论和清理结论相互独立；清理失败最终为 ERROR。
- `context.step` 只记录事实；`context.check` 记录检查并在失败时抛出 CaseFailure。
  禁止从展示文本反推 verdict。
- `RequirementRegistry` 由产品显式拥有；公共 gates 只读，产品复制后注册并冻结。
  注册顺序显式、键不可重复，不存在产品 import 修改公共 registry 的路径。
- SQL 会话显式拥有连接；transaction 成功提交、异常/取消回滚；离开作用域后拒绝使用。
  SQL 默认文本化结果用于回归断言，也可选择保留驱动的 Python 值；query 参数化与
  连接/语句超时均明确。查询期间采用数据库 statement_timeout，取消在 SQL 边界检查。
- runtime 必须实现 enter/exit/finish/stop。RuntimeBinding 只保留一个 `(context, runtime)`
  execute 入口，没有旧 executor/context_executor 双分支。finish/teardown 顺序由契约声明。

## 内部职责

| 组件 | 所有权 |
| --- | --- |
| RegressionEngine | 生命周期、判定、最终结果 |
| CaseContext | 执行身份、产品输入、能力组合 |
| EvidenceRecorder | 事件序列、operation key、证据写入 |
| CommandExecutor | 命令、进程、TCP、进程句柄和日志 |
| FixtureManager | 清理注册、优先级、恢复与清理失败汇总 |
| EnvironmentResolver | 节点选择和配置占位符展开 |
| SqlExecutor / SqlSession | 连接、事务、类型选择、SQL 证据 |
| RuntimeExecutorCase | 类型化 runtime 绑定和每次运行状态 |

产品可以使用 SDK.md 明确列出的公共能力子包。其余执行实现模块不能由产品直接导入。

## 版本与迁移

回归产品必须在 product.yaml 声明 `regression_sdk: "2"`，平台发现和 CLI 运行
均校验精确版本。`plugin_api: v1` 是独立的产品安装协议，不代表回归 SDK v1。

移除：根包重复导出、ProductCase 重复协议、tcp_probe 别名、全局 register_requirement、
runtime 双 executor 入口、EnvironmentRef/RegressionContext 重复运行上下文。
产品调用全部迁往公开入口；报告 runtime 由宿主显式注入解析后的产品环境与输出位置。

## 验收

1. 公共契约测试：断言判定、无效输入、公开入口/版本门禁。
2. 产品重复加载测试：不会污染公共 registry 或另一个产品的 registry。
3. SQL：会话共享、类型选择、参数证据、事务提交/回滚、取消、超时和连接失败证据。
4. runtime：成功/失败/阻塞/取消、部分 setup、teardown 故障、顺序复用。
5. 原平台测试全量通过，产品 catalog 全覆盖且无旧运行器静默回退。
6. 前端构建和 git diff --check。
7. 真机业务套件与逐套件报告保真验收另记状态；纯契约测试不能替代这一项。

公共能力后续收敛见 [平台 v2 能力](platform-v2-capabilities.md)。
