# Platform regression SDK v2

SDK v2 的公开类型从 `platform_regress.sdk` 导入。产品必须在 `product.yaml`
声明 `regression_sdk: "2"`。平台发现和 CLI 均拒绝缺失或不匹配的版本；
`plugin_api: v1` 属于产品安装协议，与回归 SDK 版本独立。

设计和验收见 [docs/sdk-v2-design.md](../../docs/sdk-v2-design.md)。SDK 版本与
结果/事件文件的 `schema_version` 独立，升级 SDK 不改变已有结果文件的判定含义。

## 用例与断言

```python
from platform_regress.sdk import CaseContext, RegressionCase


class VerifyCluster(RegressionCase):
    def run(self, context: CaseContext) -> bool:
        result = context.sql("primary", "SELECT 1")
        context.check(
            "cluster-ready", "校验集群可用",
            result.rows == (("1",),),
            expected=(("1",),), actual=result.rows,
        )
        return True
```

`RegressionCase` 只要求 `run(context)`；需要准备和清理的用例实现
`SetupCase.setup(context)`、`CleanupCase.cleanup(context)`。实现采用结构化
协议，不要求继承公共基类。

- `context.step(...)` 记录一个事实，**不会修改 verdict**。负向测试可以记录
  被观察到的失败，仍通过业务断言证明它符合预期。
- `context.check(..., passed: bool)` 记录 expected/actual，并在失败时抛出
  `CaseFailure`；可用 `reason=` 提供领域失败原因。
- 返回 `True`/`None` 表示 PASS，`False`、`CaseFailure` 或 `AssertionError`
  表示 FAIL；其它返回类型是 ERROR。
- `Blocked` 是环境前提不满足，`Cancelled` 是合作式取消；其它执行异常是 ERROR。
- `Verdict` 与 `CleanupStatus` 是明确的状态枚举，拒绝未知状态。

## 执行身份与环境

每次执行创建一个 `CaseContext`，不能把同一个 context 用于两次 engine.run。
用例对象可以顺序复用；其每次运行状态必须重置。context 不支持并发写入。

`CaseEnvironment` 定义 `nodes`、`node_groups`、`node_aliases`、`users`、`user`、
`product_id`、`environment_id`、`cluster`、`state_root`、`ledger_root`、`runtime_root` 等公共字段。
`NodeEndpoint` 定义 host/port 与可选 data_dir/role。产品额外字段由产品自己
定义类型；平台不把产品二进制、配置模板或业务参数变成公共字段。

输入容器在 context 构造时校验。`node_endpoint(selector)` 校验实际 endpoint；
缺失节点或无效 host/port 抛 Blocked。`resolve_node` 支持直接节点名、primary、
standby、MMR member 等选择器；`expand` 展开 run、环境、节点占位符。

CaseContext 组合环境解析、SQL、命令、夹具和证据组件；产品不访问 `_` 成员。

## SQL、会话与事务

`context.sql(node, query, ...)` 是一次连接、一次执行的便捷接口，默认自动提交，
返回文本化 rows（NULL 仍为 None），适合回归字符串断言。参数化 SQL 使用
`parameters=`；`preserve_types=True` 保留驱动返回的 Python 值。

```python
with context.sql_session(
    "primary", preserve_types=True, statement_timeout_seconds=30,
) as session:
    with session.transaction():
        session.execute("INSERT INTO qa_case.items(id) VALUES (%s)", (7,))
        result = session.execute("SELECT count(*) FROM qa_case.items")
        context.check("rows", "检查事务数据", result.rows == ((1,),),
                      expected=((1,),), actual=result.rows)
```

- 一个 session 共享一条连接；退出作用域后连接关闭，继续使用会报错。
- 事务正常退出提交，异常和取消回滚。嵌套事务遵循 psycopg 的 savepoint 语义。
- 默认连接超时 5 秒，语句超时 10 秒；session 支持独立配置。
  `statement_timeout_seconds=None` 显式关闭语句超时。
- SQL 在执行前后检查取消；同步查询执行期间依赖数据库语句超时，并不提供
  即时中断连接的承诺。事务提交前再次检查取消。
- 语句、结果、参数和错误形成证据；保留 Python 类型时 JSON 中不可直接表达
  的值（如 Decimal/datetime）以字符串归档，内存结果保留原类型。
- 需要 PostgreSQL wire-level 行为验证时使用 `clients.pgwire`；psql/JDBC
  测试分别使用对应客户端。它们不是同一种会话语义。

## 命令、进程与 TCP

- `command(argv, timeout_seconds=30, ...)` 返回 `CommandResult`；非零返回码
  由业务断言判定。超时抛 TimeoutError，保留 partial_stdout/partial_stderr。
- `start_command`/`finish_command` 是显式配对的后台命令接口；finish 的超时
  返回码为 124，取消抛 Cancelled。超时/取消终止整个进程组。
- `start_process(..., ready_host=..., ready_port=...)` 启动产品进程并注册清理；
  端口监听只是通用 readiness，产品协议/路由就绪须另写探针。
- `tcp_exchange` 提供字节交换、读取长度/EOF 策略和响应证据。
- `stop_processes` 用于同一用例内切换产品配置前停止已有实例。

## 夹具与资源恢复

`defer_cleanup(callback, priority=0)` 注册幂等恢复动作。高优先级先执行，
同优先级逆序执行；某个恢复失败不会阻止其他动作。setup 失败、BLOCKED、
FAIL 和取消都会执行已注册清理。

清理阶段抑制取消，以允许恢复命令运行。`cleanup` 独立记录 PASS/ERROR；
清理失败最终 verdict 为 ERROR，原 `business_verdict` 仍保留。
持久化资源台账通过 `context.ledger` 管理，产品夹具必须登记需要跨崩溃回收的资源。

## 产品扩展注册

```python
from platform_regress.sdk import COMMON_REQUIREMENTS, Blocked

PRODUCT_REQUIREMENTS = COMMON_REQUIREMENTS.copy()

@PRODUCT_REQUIREMENTS.register("product_ready", before="system_time_control")
def require_product_ready(context, requirements):
    if requirements.get("product_ready") and not product_is_ready(context):
        raise Blocked("产品尚未就绪")

PRODUCT_REQUIREMENTS.freeze()
```

产品显式调用自己的 registry.evaluate。公共 registry 已冻结；同一个 registry
中的键不可重复，before 必须指向已有键。产品重复加载和另一个产品的注册不影响
公共 gates。注册表在装配期完成后冻结，执行期不修改。

## runtime 与报告

`RuntimeBinding[Spec, Runtime]` 要求单一的 `execute(context, runtime)` 回调。
Runtime 实现 `CaseRuntimeProtocol` 的 enter/exit/finish/stop；不提供方法缺失兜底。
`RuntimeExecutorCase` 管理每次 setup/run/cleanup 状态，可以顺序重复运行。

`teardown_before_finish` 声明是否在写报告前释放资源；诊断失败不能覆盖业务异常，
但会记录 diagnostic_failed 事件。部分 setup 也清理，teardown 失败记录清理 ERROR。

`ReportRuntime` 通过 `ReportSpec`、workspace、case_dir、lock_dir 和 context_data 显式构造。
它提供报告与 journal 基础能力，**不解析产品环境、不读取产品上下文文件、不自动
猜测输出位置**。产品负责解析配置后注入。产物位于 case_dir，套件锁位于 runtime 的 lock_dir。

## 公共入口与稳定性

| 公共入口 | 内容 |
| --- | --- |
| `platform_regress.sdk` | case/context/result、环境类型、断言异常、registry、SQL session、runtime 绑定和报告宿主 |
| `platform_regress.catalog` | 用例目录契约 |
| `platform_regress.steps` | 已定义的声明式步骤与断言能力 |
| `platform_regress.clients` 及其子模块 | psql、JDBC、pgwire |
| `platform_regress.execution` 及其子模块 | 命令、后台进程、daemon、锁、端口、轮询、取证 |
| `platform_regress.evidence`，`backup/config_diff/step/jdbc` 子模块 | journal、备份、配置差异、协议证据 |
| `platform_regress.reporting` 及其子模块 | 报告事实模型、渲染与导出 |
| `platform_regress.configuration` 及其子模块 | 通用配置解析与校验 |
| `platform_regress.persistence` 及其子模块 | 原子文件写入 |
| `platform_regress.ledger` | 外部资源台账与崩溃后回收 |

未列出的实现模块是平台内部接口。公共 API 的破坏性改动升级 SDK major 并同步
迁移产品；本仓库不提供旧 major 兼容层。事件与结果 schema 的变化单独管理。
根包不重复导出 SDK，v1 的 ProductCase、tcp_probe、全局 register_requirement、
EnvironmentRef/RegressionContext 与 runtime 双 executor 入口均已移除。

## v2 公共能力扩展

完整的产品迁移范围见 [平台公共能力](../../docs/platform-v2-capabilities.md)。
以下主要类型也可以直接从 `platform_regress.sdk` 导入：

- `PostgresFixtures(context)`：角色、库、表、序列、授权、配置夹具；默认使用
  原生 context SQL，特殊报告/psql 通道可显式注入。`PostgresLifecycle` 提供
  回归节点动作，管理环境的部署仍走 pgcluster。
- `DisposablePostgresResources`：声明临时资源根范围与 pg_ctl 解析器，平台完成
  回收、监听者检测和身份检查。产品扩展与拓扑初始化不进入公共件。
- `RemoteTarget`、`RemoteExecutor`、`ServerLogCollector`：远程连接与文件采集；
  日志源由 current_log 回调提供，不在公共模块硬编码产品路径。
- `FileRestoreGuard`：构造时保存文件并注册恢复，随后 write_text；额外 reload
  等操作通过 after_restore 提供。
- `PgbenchRequest`：标准 argv；标准结果、输出过滤来自 `clients.pgbench`。
  `execution.monitoring.sample_process` 提供进程资源样本。
- `WorkloadGroup`：通过 launch/describe/evaluate 插入产品负载；run 后必须在
  finally 中 close。`execution.longrun` 提供独立观察、监督、终态和 cleanup_actions。
- `JsonStateStore`：显式传入产品字段 defaults，公共存储负责版本、revision 与锁。
- `ArtifactRepository`、`CleanupReport`：公共报告/日志访问、清理结果。大型日志
  只生成预览，原证据保留。

新增公开工具子模块：`environment.disposable/guards/postgresql_fixtures/postgresql_lifecycle`、
`evidence.artifacts/fingerprint/postgresql_logs/server_logs`、`execution.remote/processes/monitoring/longrun`、
`persistence.state`、`clients.pgbench`。控制面发布和进度接口位于
`platform_app.result_publication/artifact_progress`，不属于产品用例的 SQL 执行面。


命令的 input_text 通过受超时/取消控制的 communicate 同时写入和读取，不能假设
输入写入已在 start_command 返回前完成；后台命令应配对 finish_command，且不直接
读取其 stdout。未结束命令由用例清理回收。


## 目录

CLI 使用 --output-dir <产品环境产物根> --state-dir <持久状态根>，生成 runs/<执行ID>/cases/<目标>。失败重跑与 history.jsonl 写入 state-dir。应用层注入独立 ledger_root（data/resources）及 runtime_root（runtime/products）；外部资源操作不能将账本放入 output。
