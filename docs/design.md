# 公司产品公共管理平台设计

版本：v3 · 2026-09-26
状态：目标架构。旧仓库、旧 Web、旧回归框架和旧后台服务只作为业务研究材料，不作为新平台的模块边界。

## 1. 总体定位

平台是面向公司数据库产品的现代化产品管理与验证工作台。平台统一提供：

- 产品和版本目录；
- 数据库集群环境规划、部署、启停、检查、恢复和清理；
- 统一回归测试框架和测试 SDK；
- 任务、资源锁、事件、日志、证据、报告和当前结果；
- 通用 License 签发、密钥和格式兼容；
- 数据库管理、压测、稳定性、知识和 AI 等后续能力；
- Web、CLI 和 CI 统一入口。

**平台拥有通用能力，产品目录拥有产品知识。** 新增产品只增加一个产品包，不修改平台核心；删除产品包后平台不再发现或执行该产品，但已有证据和 License 文件仍可只读查看。平台不会取消产品自己的 CLI：产品命令仍由产品包提供，Web、CI 和平台任务只是调用这些命令或同一 Provider。

### 1.1 当前范围

首期只支持**数据库集群环境**。多活、等保、FBase 数据库、fbasecman 等产品都使用平台同一套数据库集群部署能力。平台暂不建设任意中间件或应用服务部署抽象，也不以非数据库 demo 作为验收目标。

部署引擎的第一实现可以调用 `pgcluster`，但平台拥有部署协议、任务生命周期、资源归属、结构化事件和 Web 交互。旧 `env setup/start/stop/heal` 不是新平台的边界。

### 1.2 现代化重写原则

1. 不围绕旧目录、旧模块或旧 Web 兼容组织新代码。
2. 不把产品名称写进平台核心的条件分支。
3. 不把部署、回归、License 的公共逻辑复制到产品目录；产品专属 CLI、命令、配置和业务逻辑必须留在产品目录。
4. 旧代码只迁移已经验证的业务知识，必要时用隔离适配器过渡。
5. 新协议先定义，再迁移产品内容；迁移完成后删除适配器和旧框架。
6. 产品、环境、任务、证据和版本边界清晰，页面不直接读写产品源码或运行目录。
7. AI、动画和报告只能消费事实，不能制造事实。

## 2. 目标架构

```text
产品包 products/<id>/ ──────┐
                            ▼
                    Product SDK / Registry
                            │
Web / CLI / CI ──► Platform API ──► Task Worker
                            │              │
        ┌───────────────────┼──────────────┼──────────────┐
        ▼                   ▼              ▼              ▼
 Product Catalog   Database Deployment  Regression     License
 Environment       Engine               Engine         Engine
        │                   │              │              │
        └──────────────► Event / Evidence / Result ◄──────┘
                            │
                 SQLite metadata + local files
```

首期形态是单机控制面：FastAPI、任务 Worker、SQLite WAL、控制面文件和编译后的 React 前端。数据库集群由 `pgcluster` 部署到目标主机，数据库 `data_dir` 永远属于目标主机，不得指向平台项目目录。控制机只保存配置、资源引用、任务日志、结果和证据。

控制面运行目录：

```text
data/
├── platform/                 # SQLite、队列、锁、操作日志、Web 日志
├── environments/             # profile、fixture 上下文、回归证据和结果附件
└── (legacy database paths)   # 迁移期已有本机 PGDATA，只由 pgcluster 资源引用管理
```

新部署不得把 PostgreSQL 数据目录写入 `data/` 或项目 checkout；远程路径由产品/部署配置声明，平台只校验资源归属和目标主机。

### 2.1 平台核心模块

```text
platform/
├── catalog/          # 产品、版本、构建产物和能力目录
├── environments/     # 环境、资源、连接和归属
├── deployment/       # 数据库集群部署引擎
├── regression/       # Test SDK、Suite/Case、判定和清理
├── license/          # 通用 LicenseEngine 和密钥库
├── operations/       # 任务、执行、锁、取消和恢复
├── events/           # 版本化事实事件和订阅
├── evidence/         # 原始日志、配置、附件和摘要
├── results/          # 当前结果和报告发布
├── database/         # SQL、对象、会话、锁、复制、参数
├── diagnostics/      # 规则诊断和 AI 网关
└── api/              # 按领域组装 HTTP/WebSocket API
```

平台核心不依赖任何具体产品包，不导入旧产品的 `framework` 包，不保存产品专属字段的大型公共表单。产品 CLI 由平台通过受控参数数组调用，平台不复制 CLI 内部逻辑；CLI 也可以脱离 Web 单独运行，保持 CI 和人工运维入口。

## 3. 产品包模型

### 3.1 一个产品一个包

产品的安装、启用和删除单元是一个目录：

```text
products/<product_id>/
├── product.yaml                 # 产品身份、版本和能力声明
├── adapter.py                   # 导出 ProductAdapter
├── deployment/                  # 集群参数、初始化和产品探针
├── regression/                  # 用例、fixture、断言和清理
├── database/                    # 产品数据库扩展，可选
├── diagnostics/                 # 产品日志和问题规则，可选
├── reports/                     # 产品报告扩展，可选
├── license/                     # 产品授权元数据，可选
├── knowledge/                   # 文档和源码来源，可选
├── cli/                         # 产品 CLI（run.sh、stable.sh 或 Python 入口）
└── frontend/                    # 专属页面和组件，可选
```

`products/<id>/` 是产品代码的唯一来源。不能把一个产品拆成平台中的多个硬编码目录，再通过兼容导入维持旧结构。CLI 入口必须是产品包内的相对路径，平台校验后以参数数组和明确工作目录调用；平台不接受产品包外的任意脚本路径。

产品包是版本化的受控发布物。平台部署时从产品目录构建后端包和可选前端资源，记录包版本与内容摘要；启动时校验插件 API 版本。文件系统中的已安装产品包是可用能力的权威来源，SQLite 中的 `products` 仅保存注册投影和未安装产品的只读元数据。前端专属组件随平台重新构建或发布，不承诺删除目录后无需构建即可卸载已打包的 JavaScript。

### 3.2 产品声明

```yaml
id: fbasecman
title: fbasecman
plugin_api: v1
regression_sdk: "2"
versions:
  - id: 1.7
    binary: /path/to/fbasecman
capabilities:
  deployment: database-cluster
  database: postgresql-compatible
  tests: fbasecman-regression
  stability: fbasecman-stability
  license: fd-licenser
cli:
  run: cli/run.sh
  stable: cli/stable.sh
license:
  product_code: fbasecman
  allowed_versions: ["1.7"]
```

Manifest 只描述能力和声明式参数，不允许任意字符串直接执行 shell。平台启动时检查产品 ID、插件 API、能力实现、动作 ID、schema 和依赖资源。

### 3.3 ProductAdapter

```python
class ProductAdapter(Protocol):
    manifest: ProductManifest
    def deployment(self) -> ProductDeploymentSpec | None: ...
    def tests(self) -> TestProvider | None: ...
    def database(self) -> DatabaseExtension | None: ...
    def diagnostics(self) -> DiagnosticsExtension | None: ...
    def reports(self) -> ReportExtension | None: ...
    def license(self) -> LicenseProductDescriptor | None: ...
```

部署、回归和 License 的公共执行逻辑不由 Adapter 实现；Adapter 只提供产品所需参数、用例和差异。

### 3.4 产品生命周期

新增产品：新增产品包，执行插件检查，平台自动生成该产品声明的环境、测试和 License 选项。未声明的能力不显示。

删除产品：

1. 先禁止新建环境、任务、License 和流水线；
2. 等待或取消运行中的任务；
3. 移除产品包并重新加载 Registry；
4. 引用该产品的环境、结果、报告和诊断标记为 `PRODUCT_UNAVAILABLE`，只读展示；
5. 历史证据和 License 文件仍可下载和验证；
6. 清理产品数据必须是独立的显式操作。

删除产品包不等于物理删除数据，也不能破坏公共 License 解析器和公共报告查看器。

### 3.5 前端插件

首期前端插件在构建时注册，不运行时下载未知 JavaScript。公共页面按能力和 schema 渲染；产品前端只提供确实无法公共化的报告、拓扑或字段组件。构建脚本从已安装产品包生成前端注册表，不能要求手工修改平台路由。产品删除后重新构建前端，旧数据使用公共只读视图。

## 4. 数据库集群部署

### 4.1 平台拥有部署能力

数据库集群部署是平台公共能力，提供：

- 拓扑校验和无副作用 plan；
- 主机、安装目录、数据目录、端口和节点关系；
- PostgreSQL 实例初始化；
- 流复制、MMR、Citus 等数据库集群能力；
- create/start/stop/restart/clean/health/doctor/heal；
- failover/rejoin；
- 资源归属标记和清理边界；
- 结构化步骤、配置快照、节点观测和证据。

产品不实现另一套底层集群启停框架。产品只声明拓扑模板、产品二进制/扩展、初始化角色和测试 fixture；产品自己的配置、专项检查和 CLI 仍归产品包。

### 4.1.1 环境用途与回归绑定

数据库集群环境有唯一所属产品；部署能力仍由平台统一提供。环境登记时选择用途，首期用途包括 `regression` 和 `development`，可同时选择。回归用途下可绑定该产品声明的一个或多个测试 profile（例如 FBase 的 `mmr`、`mac`，fbasecman 的 `cman`）。绑定的是 suite/profile，不是单条用例；每次执行仍可选择目标。

产品 manifest 声明 `test_profiles` 的 ID、名称、适用的 pgcluster 目标模式和用例 suite。平台保存环境与 profile 的关联，并在创建、修改和提交任务时校验环境所属产品、目标拓扑及 suite/profile；Web 筛选和产品 CLI 使用同一规则。产品目录删除后绑定只读保留，禁止新任务。改变用途或解绑需先结束该环境的运行任务，既有结果与证据继续保留。测试页只显示与当前产品/profile 匹配的环境，并分别记住最近选择；部署页是设置用途与绑定关系的入口。

### 4.2 DatabaseClusterProvider

```python
class DatabaseClusterProvider(Protocol):
    def validate(self, context: DeploymentContext) -> ValidationResult: ...
    def plan(self, context: DeploymentContext) -> DeploymentPlan: ...
    def apply(self, context: DeploymentContext, plan: DeploymentPlan) -> None: ...
    def inspect(self, context: DeploymentContext) -> ClusterObservation: ...
    def lifecycle(self, context: DeploymentContext, action: LifecycleAction) -> None: ...
```

Provider 通过 `ExecutionContext` 发布事件和附件，不直接更新平台任务状态或 SQLite。平台负责锁、取消、超时、恢复和最终结果。

### 4.3 pgcluster 迁移策略

第一阶段将 `pgcluster` 作为平台的数据库集群 Provider，通过受控桥接调用其配置和运行能力；桥接输出平台标准事件。第二阶段可将已经验证的数据库领域实现迁入平台。迁移不改变平台协议，也不要求保留旧 CLI 结构。

首期不设计非数据库服务 Provider。只有出现第二类真实部署对象后，才重新评估是否抽象服务级模型。

## 5. 回归测试平台

### 5.0 旧回归迁移保真原则

fbasecman、多活和等保的既有回归实现是迁移行为基准，参考工程固定为：

- fbasecman：`/home/postgres/fly_dev/fbasecman_dev/fbasecman_regress_v2`
- FBase 多活/等保：`/home/postgres/fly_dev/postgresql_for_fbase_dev/fbase_regress`
- 数据库集群部署：`/home/postgres/fly_dev/pgcluster`

迁移只替换执行宿主和平台集成方式。产品配置、测试准备、步骤顺序、断言内容、
资源清理、报告证据和判定语义必须保持一致。旧版本已失败的用例迁入后继续 FAIL；
不得为了平台测试变绿而删步骤、放宽预期、把 BLOCKED/ERROR 改成 PASS 或重写业务
断言。每个迁移批次必须逐项对照参考工程中的 executor、fixture、配置变换和报告。

平台公共 SDK 负责运行隔离、取消、超时、资源生命周期、事件、结果和证据；产品包
负责产品配置、协议、产品专属 fixture 与业务断言。无法解释的差异保持原判定并记录，
不得猜测测试意图。代码目录也必须遵守：平台代码在 `backend/platform_*`，产品代码
只在 `products/<product_id>/`，数据库部署由 pgcluster 管理，禁止把 PostgreSQL
数据目录放进项目 `data/`。

### 5.0.1 SDK 原生迁移完成标准

回归迁移以平台 SDK 能力完整和运行路径去旧化为完成标准，不以“已在
`RegressionEngine` 中注册”或“测试能够执行”为完成标准：

1. **平台 SDK 优先**：迁移发现生命周期、fixture、协议客户端、断言、并发执行、
   配置变换、锁、取消、清理、报告或证据能力缺失时，先在 `platform_regress`
   建立产品无关契约和实现，再由产品用例使用；不得把同类通用能力复制进产品包。
2. **禁止伪原生完成**：仅把旧 `run_case`、旧 Runtime 或旧 suite runner 包装成新的
   Case 类，仍属于临时接线，不得标记为 SDK 原生迁移完成。临时桥必须登记数量、
   依赖和删除条件。
3. **产品只保存产品知识**：产品配置模板、产品协议、产品专属 fixture、SQL、观测解析
   和业务断言留在 `products/<id>/`；进程与命令执行、SQL 通道、资源锁、取消、超时、
   清理编排、事件、证据、判定和报告事实模型归平台。
4. **保真行为，不保留旧结构**：必须保持参考工程的准备条件、步骤顺序、配置语义、
   断言、清理和 verdict；可以并且应当重构旧目录、旧 Runtime、旧报告写法和内部调用
   结构，不得为兼容历史模块边界持续增加垫片。
5. **最终删除条件**：产品平台运行路径不得依赖 `SuiteNativeCase`、`LegacySuiteCase`、
   suite `run_case`、旧 suite runner 或旧 framework。只有所有目标直接使用平台 SDK，
   且临时桥和兜底分支物理删除并通过目录覆盖测试，迁移才算完成。
6. **验收证据**：除自动测试外，每批需证明 catalog 中没有静默回退目标，并在等价
   环境/二进制下批量对照旧代码 verdict；环境暂不可用时只能记为“代码迁移待实测”，
   不得宣称完成。

fbasecman 212 条已全部达到本完成标准：68 条 SDK-native 用例 + 144 条
executor 用例（`def case_x(context)` + `ops = context.ops` 直连套件
runtime，`fbasecman_ops` 转发门面已删除；runtime 构造注入 resolver
`env`/`context_data`，不再自行加载 legacy 配置、
不再经过 suite `run_case`/runner/registry/framework）。executor 与 runtime
方法库作为产品知识保留在 `products/fbasecman/` 内，其通用原语全部为平台件。

### 5.1 RegressionEngine

回归测试通用能力属于平台：

- Suite/Case 发现和注册；
- 参数 schema、标签和资源需求；
- fixture setup/teardown；
- 前置检查、步骤、断言和清理；
- `PASS/FAIL/BLOCKED/SKIPPED/ERROR/CANCELLED` 判定；
- 事件、日志、附件、报告和当前结果；
- 取消、超时、恢复和资源锁；
- CLI、Web、CI 和后续流水线入口。

产品只提供业务用例、产品命令、SQL、观测解析和业务断言。`CaseResult`、`EvidenceRef`、清理协议和报告事实模型属于平台。产品 `run.sh`/`stable.sh` 等入口仍然保留，必须调用产品包内代码或平台 SDK，不能把产品逻辑重新搬回平台核心。

### 5.2 Test SDK

当前实现为 SDK v2，公开契约与迁移规则见 [SDK 指南](../backend/platform_regress/SDK.md) 和 [v2 设计](sdk-v2-design.md)。下方接口示意属于目标能力说明，具体签名以 SDK 指南为准。

```python
class TestProvider(Protocol):
    def discover(self, context: DiscoveryContext) -> list[TestCaseSpec]: ...
    def requirements(self, target: str) -> ResourceRequirements: ...
    def execute(self, context: ExecutionContext, target: str, parameters: dict) -> CaseResult: ...

class CaseContext(Protocol):
    def sql(self, request: SqlRequest) -> SqlResult: ...
    def command(self, request: CommandRequest) -> CommandResult: ...
    def observe(self, request: ObservationRequest) -> Observation: ...
    def evidence(self, item: EvidenceInput) -> EvidenceRef: ...
    def emit(self, event: PlatformEvent) -> None: ...
    def cancelled(self) -> bool: ...
```

用例不能直接改平台数据库，不能通过中文日志标题驱动 UI，不能自行绕过资源锁。旧框架只允许作为隔离适配器，不能成为新 SDK 的依赖。

### 5.3 迁移顺序

1. 先实现平台 SDK、判定、证据和报告模型；
2. 用 fbasecman 的真实用例验证 SDK；
3. 接入 FBase 多活和等保用例；
4. 新用例直接写平台 SDK；
5. 删除旧 `framework` 和旧 Web 运行器；产品 CLI 作为产品包的长期入口保留，并逐步改为调用平台 SDK。

“测试通过”不代表平台化完成；验收必须证明多个产品共用同一执行内核、锁、事件、证据和结果发布。

## 6. LicenseEngine

### 6.1 License 是平台通用能力

既有 `fd_licenser` 工程的真实模型是一个跨产品签发器：一份 License 可以包含多个产品，每个产品有独立版本和有效期，并绑定多组 MAC；密钥拥有版本、轮换和加密保护。

因此平台只建设一个 `LicenseEngine`，不为每个产品建设一个签发器。

平台负责：

- 产品授权选项汇总；
- 多产品请求；
- 版本、日期、MAC 和用途校验；
- Ed25519 签名；
- Argon2id 密钥派生；
- XChaCha20-Poly1305 私钥保护；
- 密钥生成、查看、口令变更、撤销和轮换；
- 旧 License 文件格式兼容、验证和下载。

产品目录只声明 `product_code`、可授权版本和产品特殊字段/规则。

```python
class LicenseProductDescriptor(Protocol):
    product_code: str
    def options(self) -> list[ProductVersion]: ...
    def validate(self, item: LicenseProductInput) -> None: ...

class LicenseEngine(Protocol):
    def options(self) -> LicenseOptions: ...
    def validate(self, request: LicenseRequest) -> None: ...
    def generate(self, request: LicenseRequest) -> GeneratedLicense: ...
    def verify(self, content: bytes) -> LicenseDocument: ...
```

### 6.2 兼容性要求

旧 C 程序不是新平台任何环节的依赖。License 移植已完成，格式与签名契约由平台实现自身用专用测试密钥验证：

- payload 字段和原始 JSON 字节；
- 多产品、产品版本和独立到期时间；
- 多 MAC；
- Ed25519 签名、Base64、分隔线和 MD5SUM（测试逐字段断言并对签名自验签）；
- v1.1/v1.2/v1.3 密钥读取和轮换；
- 错误签名、错误口令、撤销密钥和未知产品。

产品包删除后，LicenseEngine 仍可验证历史文件并显示原始产品编码；新签发只能选择当前已注册的产品。

首版生成可同步完成，不进入任务队列，不建立申请/审批表。私钥、口令和数据库凭据不写日志、不进入事件 payload 或 AI 输入。

## 7. 任务、执行、事件和证据

### 7.1 执行身份

平台不建设面向用户的测试历史中心，但每次实际执行必须有不可变的内部 `execution_id`，关联任务、事件、日志、指标、结果和诊断。执行记录可按保留策略清理。

- `Operation`：需要后台生命周期的部署、测试、负载或诊断动作；同步 SQL 查询和 License 签发使用请求 ID，不创建后台任务；
- `Execution`：一次测试/压测/稳定性实际执行；
- `LatestResult`：目标的当前结果；
- `Evidence`：本次执行的原始材料和摘要。

当前结果可以覆盖，执行身份不能在事件产生后改变。这样既不建设历史归档页，也不牺牲回放、诊断和指标的可追溯性。

### 7.2 状态

任务状态：

```text
QUEUED → RUNNING → SUCCEEDED / FAILED / CANCELLED / RECOVERY_REQUIRED
RUNNING → CANCELLING → CANCELLED / RECOVERY_REQUIRED
```

测试判定独立为：

```text
PASS / FAIL / BLOCKED / SKIPPED / ERROR / CANCELLED
```

命令成功退出不等于业务 PASS；清理失败不覆盖业务 FAIL；外部环境状态无法确认则进入 `RECOVERY_REQUIRED`。

### 7.3 资源锁和取消

资源锁按真实主机、端口、数据目录、实例、集群和环境归一化，不按 YAML 文件名或套件名锁定。Worker 领取任务时再次核对计划和资源快照。

取消顺序：标记取消、通知 Provider、终止受控进程组、执行清理、核对环境、发布最终状态。无法确认远端状态时禁止自动重派破坏性动作。

### 7.4 事件

事件是唯一事实来源，包含版本和单调序列号：

```json
{
  "schema_version": "1.0",
  "execution_id": "...",
  "sequence": 42,
  "recorded_at": "2026-09-26T12:00:00Z",
  "kind": "observation.captured",
  "step_key": "check-replication",
  "entity_id": "db-a-standby-1",
  "payload": {},
  "evidence": []
}
```

事件先写入唯一权威存储，再通过 SSE 推送。客户端按 `execution_id + sequence` 去重，断线按 sequence 补齐，乱序/缺口显示为数据异常。快照是派生缓存，不是事实来源。

### 7.5 证据和当前结果

原始日志、命令输出、配置快照、观测、报告和附件记录采集来源、时间、文件身份和内容摘要。当前结果使用临时目录、完整性检查和原子替换，不能出现“新报告 + 旧结果”。

```text
data/latest/<product>/<environment>/<target>/<profile>/
├── result.json
├── execution.json
├── events.jsonl
├── report.html
├── report.txt
├── topology.json
├── config/
└── logs/
```

目录路径不包含执行 ID；`execution.json` 保存本次执行身份和版本摘要。诊断绑定证据摘要，结果替换后旧诊断标记过期。

## 8. 数据模型

首期 SQLite 按领域拆分，统一迁移：

| 表 | 作用 |
| --- | --- |
| `products` | 已安装产品 manifest 的注册投影及未安装产品的只读元数据；不独立决定可执行能力 |
| `product_versions` | 产品版本、源码 revision、构建产物和校验和 |
| `environments` | 产品绑定、连接、部署配置和用途 |
| `resources` | 主机、端口、数据目录、实例和集群归属 |
| `tasks` | Operation 状态、动作、参数、取消和 Worker 信息 |
| `executions` | 测试/压测执行身份和资源快照 |
| `events` | 结构化事实事件和序列号 |
| `resource_locks` | 真实资源占用和心跳 |
| `latest_results` | 产品+环境+目标+profile 的当前判定 |
| `evidence_refs` | 日志、报告、配置和附件摘要 |
| `diagnoses` | 绑定证据摘要的规则/AI 诊断 |
| `knowledge_sources` | 产品文档、源码和 revision 来源 |
| `report_templates` | 模板版本和输入 schema |
| `pipelines` | 后续流水线定义 |
| `notifications` | 后续旁路通知规则 |

License 私钥和口令不进 SQLite，保存在受控密钥目录。模型 API 密钥不进 SQLite、不写日志。

## 9. API 和前端

API 以 `/api/v1` 为稳定前缀，按领域拆分：

- `/products`、`/product-versions`；
- `/environments`、`/deployments`；
- `/tests`、`/operations`；
- `/database`；
- `/licenses`；
- `/evidence`、`/reports`、`/diagnostics`；
- 后续 `/workloads`、`/pipelines`、`/notifications`。

动作由 `ActionSpec` 描述输入 schema、资源、是否修改环境、超时、取消和重入策略。HTTP handler 不拼接产品命令，不读写产品源码目录。

前端使用 React + TypeScript + Ant Design、TanStack Query、SSE、真实路由和 OpenAPI 生成类型。公共平台壳、平台页面和产品页面分层；产品专属组件位于产品前端包，不进入公共 `components`。导航、测试入口和能力菜单来自产品 manifest，不写死产品名称。

工作台以产品和环境为稳定上下文，侧栏按实际能力显示导航。首屏是可操作的当前工作区：环境健康、待处理任务、最近失败、部署和测试入口。部署页以拓扑与计划为主体，测试页以用例、步骤、判定和证据为主体，License 页支持一次选择多个产品。详情页有可分享 URL；刷新、返回和切换环境时保留筛选与选中对象。布局优先服务密集扫描和重复操作，移动端保留完整任务、日志与结果访问路径。

视觉使用统一设计 tokens、清晰层级和克制的状态色。3D/动画仅在真实拓扑和事件有解释价值时出现，并可关闭动态效果；不能让装饰遮挡操作和证据。公共空状态、加载、失败、离线、权限不足和产品已卸载状态必须完整设计。前端以桌面和手机截图、键盘操作、日志长文本和真实数据验收，不以静态样例页代替。

数据库工作台提供对象树、一次 SQL 请求、结果表、原始输出、取消查询、会话、锁、复制和参数视图。拓扑画布只展示配置和真实观测：操作、观测、判定分层表达；没有观测时显示未知或待确认，不显示虚构流量。

## 10. 报告、知识和 AI

报告消费统一结果和证据模型。Jinja2 输出 HTML、文本和 JUnit；产品只能添加章节和解析规则，不能从展示文本重新猜判定。AI 只能填充允许的分析章节。

AI 是后置能力：

- 绑定产品、版本、execution 和证据摘要；
- 引用必须定位真实证据或源码 revision/行号；
- 结果变化后诊断过期；
- 模型不可用不影响部署、测试、License 和事实报告；
- 发送前脱敏密码、私钥、凭据和禁止外发的日志。

知识库、源码索引、代码问答、失败聚类、流水线和通知在确定性平台内核稳定后实施。

## 11. 安全和运行形态

首期为内部单机软件：Python 3.12、FastAPI、Huey、本地 SQLite WAL、本地证据目录和静态前端。远程数据库执行由 Provider 实现，平台不能假定远程路径、本地 PID 或本地进程组有效。

最低要求：

- 产品、主机、端口、路径、目标和参数白名单校验；
- CLI 调用使用参数数组和明确工作目录，平台记录命令、产品版本和退出结果；禁止从产品包读取未经校验的 shell 字符串直接执行；
- 破坏性部署和 SQL 写操作明确确认；
- 命令参数数组执行，禁止未经校验的 shell 拼接；
- License 口令、密钥和数据库凭据不进入日志、事件和 AI；
- 终端若启用，必须绑定资源、记录会话并与 clean 互斥；
- 外部 CLI 可能绕过平台锁，页面必须展示这一控制边界。

## 12. 实施路线

### P0：确定新平台内核

- 建立 `platform/` 核心包和产品 SDK；
- 定义 ProductManifest、Provider、ActionSpec、Event、Evidence、Execution、LatestResult；
- 用最小验证产品证明“只新增产品包，平台核心零修改”；该产品可以只声明测试能力，不承担非数据库部署验收；
- 删除平台核心中的产品名称分支和旧兼容垫片；
- 建立 License 格式固定测试向量（格式断言 + 平台自验签）。

### P1：数据库部署公共能力

- 实现 DatabaseClusterProvider 和 pgcluster 桥接；
- 完成 plan、apply、启停、health、doctor、heal、clean、failover、rejoin；
- 统一资源锁、事件、证据和环境归属；
- 验证多活、等保、FBase、fbasecman 共用部署入口。

### P2：回归测试公共能力

- 实现 RegressionEngine、Test SDK、统一判定和清理；
- 先接入 fbasecman 真实套件，再接入 FBase 多活/等保；
- 旧框架只作为隔离迁移适配器，最终删除；
- CLI、Web、JUnit、HTML 和当前结果共用同一事实模型。

当前进度与剩余缺口见 [regression-status.md](regression-status.md)。

### P3：License 平台能力

- 实现 LicenseEngine、密钥库和产品授权目录；
- 支持一份 License 多产品、多版本、多 MAC；
- 通过平台自验签与格式断言覆盖签发正确性；
- 产品删除后禁止新签发但保留历史验证。

### P4：数据库工作台和报告

- SQL、对象、会话、锁、复制、参数和查询取消；
- 统一报告模板、证据查看和事件回放；
- 完成桌面和移动端关键交互验收。

### P5：压测与稳定性

- pgbench/JDBC WorkloadProvider；
- 指标采样、曲线、阈值、收尾和恢复；
- 复用产品已有 supervisor，平台不启动第二个常稳管理者。

### P6：知识、AI 和运营闭环

- 产品/版本限定的知识索引和源码索引；
- 证据绑定的 AI 诊断和代码问答；
- 流水线、定时、通知和产品版本视图。

非数据库部署、分布式 Worker、复杂权限和向量数据库不属于当前路线的前置工作。

## 13. 工程验证

每个阶段必须通过：

```bash
.venv/bin/python -m pytest
(cd frontend && npm run build)
git diff --check
```

此外必须单独执行真实数据库集群部署、真实回归、License C 兼容、取消/恢复和前端关键场景验收。演示数据和单元测试不能代替真实链路。

## 14. 最终验收

1. 新增 `products/demo/` 后，平台自动发现其已声明的能力，不修改平台核心或公共前端路由。
2. 删除产品包后，平台禁止该产品的新环境、任务和 License；已有数据可只读查看。
3. 多活、等保、FBase、fbasecman 共用同一数据库集群部署引擎。
4. 多个产品共用同一回归执行内核、资源锁、事件、证据和结果发布。
5. 一份 License 可同时授权多个产品，签发结果通过平台 Ed25519 验签与格式断言。
6. 任务取消、清理失败、环境待恢复和业务 FAIL 分别表达。
7. 前端只由结构化事件和真实观测更新拓扑，不从中文日志或动画推断状态。
8. AI、知识库、通知或专属页面不可用时，不影响确定性的部署、测试、License 和报告链路。
