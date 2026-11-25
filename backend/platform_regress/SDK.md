# 回归 SDK v2 参考

SDK v2 的公开类型从 `platform_regress.sdk` 导入。产品必须在 `product.yaml`
声明 `regression_sdk: "2"`。平台发现和 CLI 均拒绝缺失或不匹配的版本；
`plugin_api: v1` 属于产品安装协议，与回归 SDK 版本独立。

可运行的接入与导出示例见 [README](../../README.md#接入回归用例和生成报告)。
SDK 版本与结果和事件文件的 `schema_version` 独立，旧结果保留原判定含义。

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

## 用例登记与 Web 发布

- 产品 `cases.py` 的 `CASES` 将 `<suite>.<case>` 映射到实现对象。
  `CASE_ORDER` 可指定批跑顺序，`default_enabled` 可控制批跑的默认选择。
  产品有独立目录时，同步维护目录中的标题、目的、启用状态和 target。
- Web 通过产品清单和 Provider 发现用例、校验动作和 target，再下发任务。
  当 Provider 使用 `platform_regress.cli` 时，平台注入环境产物根、持久状态根、
  资源账本根和运行目录。发布结果按本次操作与执行身份关联。
- 产品公共准备适用于有共享对象或上下文依赖的套件。例如 fbasecman 的公共准备
  属于该产品的回归动作，可能修改测试角色、数据库对象和复制配置。根据用例实际依赖
  决定是否准备，并验证需要的前置条件。独立用例在 `setup` 或执行时完成自己的准备，
  用 `cleanup` 和已登记的恢复动作收尾。

## 报告与验收

### 写入执行事实

用例说明应明确测试目的、前置条件、操作范围和成功条件。每个成功条件都应有相应的
可比较断言。CLI 可用 `report_description` 将字典形式的用例说明附加到当次工件。
检查结果、运行日志和原始证据都应对应当次执行。实际值中的 `0`、`False`、
空列表和空字符串保留其原意。提供了 `actual` 时，报告显示该值，原始输出单独归档。

关键检查建议使用 `context.check()`，给出 `expected`、`actual`，并用 `details`
补充检查意图、节点、命令、比较规则或原文证据。也可以先用 `context.step()` 记录
结构化事实，再明确触发业务断言。步骤的 `intent` 使用 `prepare`、`action`、
`verify` 或 `cleanup`。准备成功和生命周期完成只代表该步骤已执行。
预期报错的测试要核对实际错误内容或 SQLSTATE，外部程序测试要保留退出码及原始输出。

```python
expected = (("1",),)
result = context.sql("primary", "SELECT 1")
context.attach_text("query.txt", repr(result.rows) + "\n")
context.check(
    "select-one", "核对查询返回一行一列", result.rows == expected,
    expected=expected, actual=result.rows,
    details={"intent": "verify", "node": "primary", "command": "SELECT 1"},
)
```

用例说明使用「测试目的」，写清被验证行为。JDBC 绑定和协议边界可展示最小的
实际客户端代码。SQL、启停和状态检查可直接记录节点、命令、时限与期望。
示例随当次执行归档。报告优先展示业务验证，准备动作单独归类。原始报告入口
提供本次 `report.txt`，日志与附件用于排障和审计。

**SQL_PARSE 场景示例。** 在同一 JDBC 连接中，先验证 `SELECT ?::int` 恰好返回一行
非空整数 42。随后分别制造除零错误并核对 SQLSTATE `22012`，通过 rollback 或调用
commit 结束失败事务，再在新事务查询 42。报告分别记录自动提交、失败事务结束方式
及下一事务的恢复查询。失败事务以回滚结束，读写路由需要另有后端归属证据。

[实际 SQL_PARSE 报告样板](../../docs/images/fbasecman-report-example.png)

### 会话和协议证据

- `RESET`、`RESET ALL`、`DISCARD ALL` 和 `SET LOCAL` 要在相关的原会话核对。
  `DISCARD ALL` 在事务块外执行。验证 `SET LOCAL` 时记录事务内及提交或回滚后的值。
- 事务用例说明连接是否复用、自动提交状态、事务边界，以及恢复检查发生在哪个事务。
  保存点恢复要验证原事务仍可用。验证失败事务结束语义时，保存实际响应和事务状态。
- 协议用例保留命令标签、SQLSTATE、事务状态和返回值，失败步骤先写事实再退出。
  表格按列名读取，数值按类型比较。退出成功、重试次数及耗时作为执行信息。

### 生成与检查报告

CLI 为用例写出结构化结果、步骤和报告。需要离线报告时，传入 `--html` 和
`--junit`。相对路径写到本次 `runs/<执行ID>/`。Web 任务可用 `export_reports`
请求离线导出，也可从环境结果 API 导出当前归档。默认报告展示当次执行的结构化事实，
并保留原始报告及日志供核对。前序失败导致未执行的计划步骤保留原期望，标为 BLOCKED
并记录原因。已执行但缺少判定的步骤标为 UNKNOWN。失败报告应指出步骤、节点、期望、
实际值和证据位置。初始化失败时尽量保留服务器日志。

验收时至少执行一个成功场景和一个不满足期望的场景。确认报告能定位失败步骤与实际值，
记录尚未执行的验证项和原因，区分业务失败和清理失败。历史查看应显示当次归档的说明和
证据，对早期缺失的字段保留未知状态。

SDK 变更还应验证公开入口与版本门禁、产品重复加载时注册表隔离、SQL 会话和
取消边界，以及 runtime 在部分 setup、失败清理和顺序复用时的行为。用同等环境与
产品构建核对迁移前后的判定、步骤和证据。真机套件验收记录进入进度文档。

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
