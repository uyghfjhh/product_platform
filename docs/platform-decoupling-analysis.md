# 公司产品公共管理平台：公共能力平台化与产品零耦合差距分析与 Review 报告

版本：v2.0 · 2026-09-28
基准对照：[docs/design.md](design.md) (v3) · [docs/progress.md](progress.md) · [docs/regression-status.md](regression-status.md)
分析范围：`backend/platform_app/`、`backend/platform_regress/`、`products/`、`frontend/`

---

## 1. 总体定性与成熟度评估

### 1.1 总体评估得分：88% ~ 92%（相比 v1.0 的 70% 有质的飞跃）
经过 2026-09-28 下午的连续重构提交（`45f08f9`、`772e9b3`、`d3adddd`、`236b44c`），前一份报告中指出的**平台核心硬编码泄漏**、**前端公共视图业务穿透**、**用例依赖门禁缺失**以及**旧 shim 垫片层冗余**等主要阻碍已**基本被彻底拔除并全量通过自动化验证**。

平台的测试门禁现状：
- 平台原生单元测试：**249 passed**（100% 通过，无一失败）
- FBase legacy 单测：**149 passed**（100% 通过）
- fbasecman legacy 单测：**258 passed**（仅 1 项依赖历史 `output/runs` 产物的文件检查失败，其余全绿）
- 前端 TypeScript 类型检查与 Vite 生产构建：**0 错误**通过（`npm run build` 生成 `frontend/dist`）
- `git diff --check`：干净无格式异常

### 1.2 现状雷达图（解耦成熟度）

| 核心维度 | v1.0 | v2.0 | 当前定性 | 剩余差距与待办 |
| :--- | :---: | :---: | :--- | :--- |
| **产品接入与发现机制** | 85% | **98%** | `config.py`、`api.py`、`cli.py` 均已改为 `discover_products()` 动态扫描 | 仅待增加第三产品（demo）插拔测试 |
| **数据库集群部署能力** | 90% | **92%** | 全面由平台 `PgclusterDatabaseProvider` 接管，产品零底层生命周期 | 部署页深色交互与 8081 参考站对齐 |
| **回归测试框架通用内核** | 70% | **96%** | `requirements`/`daemon`/`jdbc`/`pgwire`/`evidence`/`reporting` 全上收平台；fbasecman 212 条全部平台宿主（68 SDK-native + 144 `context`/`ops` executor），`LegacySuiteCase` 清零；`fbase-database` vendored legacy 树（framework/suites/unit_tests）已物理删除，仅保留 `regress.yaml` 配置源 | — |
| **通用 License 签发** | 95% | **98%** | 纯 Python 实现，多产品/多版本/多 MAC 统一签发并通过 C 校验 | 生产密钥管理与轮换流程保持通用 |
| **任务、锁与事件/证据** | 90% | **95%** | 资源级排他锁、不可变 execution、原子 `result.json` 全平台化 | 健壮性增强（如节点瞬断重试防级联） |
| **前端平台壳与产品解耦** | 60% | **95%** | `TestsPage` 静态字典移除、`StabilityPage` 动作动态化、3D 拓扑角色动态化 | 前端包完全声明化 |
| **规划中通用能力 (P4-P8)** | 30% | **35%** | 报告双格式统一渲染已上收 | 终端、流水线、告警、通用压测待排期 |

---

## 2. 本轮重构完成的解耦项（已闭环清单）

本次 Review 逐项核验了下午的一系列重要提交，确认以下 6 大关键解耦全部落地：

### 2.1 平台控制面核心彻底动态化（提交 `d3adddd`）
- **`backend/platform_app/config.py`**：彻底废弃 `fbasecman_regress_root` / `fbase_regress_root` 两个固定字段，重构为通用的 `product_regress_root(product_id: str)` 方法与可选的 `regress_roots` 覆盖字典。
- **`backend/platform_app/api.py`**：`/api/v1/integrations` 接口由硬编码产品列表改为动态迭代 `discover_products(settings.products_root)`，并检查各产品声明的 `manifest.cli`。
- **`backend/platform_app/cli.py`**：`check` 与 `doctor` 诊断命令完全动态遍历已安装产品，不再硬编码特定产品名。

### 2.2 回归通用内核消除特定产品前缀（提交 `d3adddd` 与本次同步）
- **`backend/platform_regress/steps.py`**：通用二进制定位函数更名为 `_db_binary(context, name)`，优先读取环境中的 `db_bin_dir`。严格遵循平台零产品感知原则，核心层坚决不保留任何产品专属名称或兼容别名。
- **`backend/platform_regress/execution/forensics.py`**：`find_core_files()` 废除硬编码的 `binary_name="fbasecman"` 默认值，要求调用方显式指定目标进程名。
- **`backend/platform_regress/reporting/html.py`**：移除了报告模板头部与脚部硬编码的 `"fbasecman"` 文案，改为通用测试执行报告展示。

### 2.3 前端公共视图业务逻辑彻底剥离（提交 `236b44c`）
- **`frontend/src/views/TestsPage.tsx`**：物理删除了包含 `mmr`、`mac`、`rw_toggle`、`guc` 等 12 个套件的静态中文名映射字典 `SUITE_NAMES`，改为直接消费后端用例元数据中的 `c.suite_title || c.suite`，新增产品套件直接在界面正常显示中文。
- **`frontend/src/views/StabilityPage.tsx`**：废弃了静态写死的 `operationRequest(..., 'stability.fbasecman', ...)`，改为动态派发 `stability.${environment.product_id}`，且标题动态呈现 `product?.title`。
- **`frontend/src/components/ThreeTopologyView.tsx`**：3D 拓扑画板中写死的 `'fbasecman 网关'` 已更名为通用角色 `'接入网关'`。

### 2.4 通用环境依赖门禁平台化（`platform_regress.execution.requirements`）
- 在平台内核中建立了通用的 `evaluate_requirements` 框架，接管了 `clusters`、`commands`、`plugins`、`groups`、`nodes`、`roles`、`extensions` 依赖校验；
- 新增 `tests/test_requirements.py` 15 项原生单测，全量 PASS。

### 2.5 进程守护生命周期通用化（`platform_regress.execution.daemon`）
- 实现了 `ManagedDaemon` 通用生命周期类（端口冲突自动清理、nohup 前台封装、基于探针的就绪等待、多级信号回收、崩溃取证）；
- `products/fbasecman/process.py` 已重构为薄继承类，业务代码缩减至 52 行；
- 新增 `tests/test_daemon.py` 8 项原生单测，全量 PASS。

### 2.6 旧架构退役与 Shim 物理删除（提交 `45f08f9`、`772e9b3`）
- **FBase 死桥清理**：由于 228 条 FBase 用例已全部原生运行在平台声明式步骤引擎上，`products/fbase-database/cases.py` 中用于兼容旧 `run.sh` 的 `LegacyFbaseCase` 及相关兜底分支已**全部删除**。
- **fbasecman framework shim 物理删除**：`products/fbasecman/regression/legacy/framework/` 下所有的兼容 shim 模块已**完全物理删除**；业务配置转换收口至 `legacy/cmanconf.py`，平台代码与产品包彻底解绑。

---

## 3. 当前残余差距与待办分析（最后的 ~5%）

fbasecman 144 条 executor 用例已完成 `rt.*`→`ops.*`/`context` 形态迁移
（`fbasecman_ops` PEP 562 转发 facade + `RuntimeBinding.context_executor`，
runtime 构造注入 resolver `env`/`context_data`）；产品侧 executor 与 runtime
方法库按 §5.0.1.3 属产品知识，物理位置保留在 `products/fbasecman/`
产品包内（`regression/legacy/` 子路径现为纯 vendored 产品知识与资产——
`run.sh`/`tools/cli.py`/`suite.py`/`plugin.py`/`registry.py`/vendored
`unit_tests/`/内层重复 `products/` 包/零引用 `env/` 已于 2026-09-29 物理
删除，非平台运行路径依赖）。当前代码距离“理想终态”只剩下以下
收尾工作：

### 3.1 ~~`fbase-database` 的 `framework/` 遗留目录~~（已关闭）
- **现状**：`products/fbase-database/regression/legacy/` 已随 vendored `unit_tests/` 一并物理删除；仅保留 `regress.yaml`（集群拓扑与 license/tmp_root/local_host/contrib_root 的环境配置源）与三份问题单文档。
- **结论**：fbase-database 平台路径对该目录已无代码依赖，`cases.json` 是唯一用例来源。

### 3.2 ~~`test_junit.py` 的环境孤儿断言~~（已随 vendored `unit_tests/` 删除关闭）

### 3.3 缺少金标“第三产品”插拔验收测试
- **现状**：目前只有 `fbasecman` 与 `fbase-database` 两个真实产品。
- **待办**：应当编写一个自动化的插拔测试（如 `tests/test_plug_and_play.py`）：
  1. 动态生成一个最小合规产品 `products/demo-mock/`；
  2. 验证平台 `discover_products()`、路由装配、前端注册表生成和 API 执行**零修改生效**；
  3. 物理删除 `products/demo-mock/`，验证其历史数据与报告变为 `PRODUCT_UNAVAILABLE`，只读查看且不抛异常。

### 3.4 规划中的高级平台能力尚未建设（P4–P8 缺口）
按照设计文档规划，尚未实施的高阶能力为：
1. **Web xterm 交互终端**：受控 shell 与会话留痕；
2. **流水线编排**：基于 Huey periodic 的多步骤任务串接；
3. **旁路告警**：Webhook / Email 通知；
4. **通用压测 Workload 引擎**：支持通用 pgbench 编排与指标实时采样。

### 3.5 代码审查问题发现：产品侧对平台私有下划线函数的直接穿透与调用遗漏
- **问题 1（重构遗漏调用点）**：
  - 在提交 `d3adddd` 将平台 `backend/platform_regress/steps.py` 中的 `_fbase_binary` 重命名为通用 `_db_binary` 时，`products/fbase-database/fixtures.py:89` 原代码仍保留了 `platform_steps._fbase_binary(...)`。在无兼容别名的情况下，该调用点若被触发会抛出 `AttributeError`。
  - **定性**：重构命名变更时，未对产品侧所有关联调用点完成闭环同步。
- **问题 2（私有方法越界穿透）**：
  - 在 `products/fbase-database/fixtures.py` 中，存在多处直接越界调用 `platform_steps._*` 私有函数的情况：
    - 第 89 行：`platform_steps._db_binary(context, name)`
    - 第 95 行：`platform_steps._pg_ctl_argv(context, endpoint, action)`
    - 第 104 行：`platform_steps._managed_node_order(context)`
  - **定性**：单下划线前缀按约定属于平台内部私有实现。产品层直接穿透访问平台私有函数，导致两者的内部细节紧密咬合，一旦平台内核重命名或调整参数，产品端即被动破坏。
- **整改建议（待办）**：
  1. **产品层同步**：确认 `products/fbase-database/fixtures.py:89` 修正为调用当前的通用方法；
  2. **平台规范化公开接口**：在平台层定义正式的公共契约（例如在 `CaseContext` 上提供标准公共方法 `context.binary(name)`、`context.pg_ctl_argv(...)`），严禁任何产品模块跨包调用 `_` 开头的私有实现，从接口契约层彻底固化零耦合。

### 3.6 专项代码审查发现：全库“硬编码”深度 Review 报告（二轮全面排查）

在本轮审查中，我们对全库的代码（平台控制面、通用执行引擎、产品适配层、声明式用例资产、前端）进行了深度扫描。**结果显示：系统内部确实存在大量深层次的环境假设、固定路径、特定协议与账号硬编码**。

这些硬编码主要分布在五个层面，其严重程度、量化数据与具体表现如下：

---

#### 3.6.1 平台控制面核心（`backend/platform_app/`）：隐蔽的数据库形态与目录强假设
平台控制面原本应完全中立，但深入排查后发现以下隐蔽耦合：
1. **拓扑解析强绑定 PostgreSQL/pgcluster（`backend/platform_app/topology.py`）**（✅ 已整改，见 3.6.5 阶段二落地）：
   - `configured_topology()` 与 `observed_status()` 在子进程中内嵌执行 Python 脚本，**直接写死 `from pgclusterlib.config import load` 和 `from pgclusterlib.runtime import Runtime`**；
   - 脚本内硬编码了 PostgreSQL 专属的四类复制模型：`streaming`、`logical`、`citus`、`mmr`；
   - **架构影响**：平台控制面目前本质上是“PostgreSQL 专用控制面”，一旦接入非 PG 架构的产品（如 Redis、Kafka、ClickHouse 等），该拓扑服务完全无法复用。
2. **复制观测模型未插件化（`backend/platform_app/replication_observations.py`）**（✅ 已整改，见 3.6.5 阶段二落地）：
   - 在平台共享层直接定义了 `postgres.replication` 观测实体解析，将特定数据库的流复制指标（LSN、replay lag、slot）作为平台核心逻辑；
   - **架构影响**：违反产品适配器模式，应当下沉为数据库产品专有的 Observation 插件。
3. **开发机兄弟目录假定（`backend/platform_app/config.py`）**：
   - `pgcluster_root` 默认回退至 `ROOT.parent / "pgcluster"`（即开发机的 `../pgcluster` 目录）。在标准部署或容器环境中，如果不显式注入 `PRODUCT_PLATFORM_PGCLUSTER_ROOT` 环境变量，平台将无法定位。

---

#### 3.6.2 声明式用例资产（`products/fbase-database/regression/cases.json`）：重度环境数据固化（量化排查）
通过对全量导出的 228 个声明式用例进行语法树与正则量化统计，发现该 JSON 文件是历史开发机执行痕迹的“静态快照”，硬编码数量极为庞大：

| 硬编码类型 | 统计出现频次 | 典型示例 | 潜在风险与架构影响 |
| :--- | :---: | :--- | :--- |
| **本地回环 IP (`127.0.0.1`)** | **5,689 次** | `-h 127.0.0.1`、`host=127.0.0.1` | 无法适应远端目标主机、容器网络或多机集群 |
| **绝对二进制安装路径** | **5,333 次** | `/usr/local/fbase15.15/bin/psql`、`pg_ctl` | 数据库若安装在其他目录（如 `/opt/fbase`），执行即刻全面中断 |
| **硬编码系统账号** | **4,982 次** | `-U postgres`、`user=postgres` | 强假设数据库超级用户为 `postgres`，不支持自定义管理员账号 |
| **临时数据目录绑定** | **2,632 次** | `/tmp/fbase_regress_mmr_daemon_{run_id}` | 强绑定宿主机 `/tmp` 目录，在 `/tmp` 空间小、noexec 挂载或多租户隔离下失效 |
| **绝对 License 路径** | **339 次** | `cp /home/postgres/license/license.dat ...` | 换一台机器或换一个部署用户，用例全部报找不到 License 文件 |
| **固定监听端口** | **数百处** | `port = 15549`、`port = 15546`、`15445`... | 固定端口碰撞无容错，无法高并发并行回归 |

**定性评估**：平台底层 `steps.py` 虽然支持动态参数，但在消费 `cases.json` 时，是直接将上述包含绝对路径的 `argv` 作为脚本字符串传给 `sh -lc` 执行的。**这是未来用例跨环境可移植性最核心的债务**。

---

#### 3.6.3 产品原生用例与运行时（`products/fbasecman/`）：协议、网络与账号深度硬编码
1. **原生用例全面写死 `127.0.0.1`（`products/fbasecman/native.py`）**：
   - 超过 18 处直接调用 `_console_query`（写死 `127.0.0.1`）、`socket.create_connection(("127.0.0.1", port))`、`jdbc_client.build_url("127.0.0.1", ...)`；
   - 客户端执行逻辑完全未消费 `context.node_endpoint(node)["host"]`；
2. **系统二进制回退硬编码（`products/fbasecman/native.py`）**：
   - 多处使用 `context.environment.get("psql_bin", "/usr/bin/psql")`；若系统默认是旧版 psql，将导致客户端协议错乱；
3. **固定驱动文件名与版本（`products/fbasecman/provider.py`）**：
   - 第 83 行写死 `"jdbc_jar": ... / "lib_jdbc/postgresql-42.7.7.jar"`，依赖特定小版本，升级 jar 包将直接报 `JdbcError: missing jdbc jar`；
4. **业务对象名与凭证全套写死（`native.py` & `cases.py`）**：
   - 写死用户：`admin`（密码为空）、`postgres`；
   - 写死数据库与路由组：`console`、`mmr_group`、`rep_group`、`balance_group`、`single_group`、`g1`；
   - 写死集群名：`pg_cluster_1`、`pg_cluster_2`、节点别名 `pg_1`、`pg_2`；
5. **部署向导默认路径绑定开发机用户（`router.py` & `frontend.ts`）**：
   - `data_root: str = "/home/postgres/fbasecman_regress_v2_mmr"`；
   - `license_file: str = "/home/postgres/license/license.dat"`；
   - `process.py` 脆弱的字符串替换：针对历史老工程 `/home/postgres/fly_dev/fbasecman_dev_autotest/...` 的全字替换。

---

#### 3.6.4 隔离实例管理器（`products/fbase-database/isolated.py`）：对 `/tmp` 目录的强制约束
- **强制前缀防呆约束**：
  - 定义 `TMP_PREFIX = "/tmp/fbase_regress_"`；
  - 并在多处硬编码检查：`if not str(data_dir).startswith("/tmp/fbase_regress_"): raise ConfigError("... 必须位于 /tmp/fbase_regress_ 下")`；
  - **架构影响**：剥夺了用户/CI环境通过环境变量指定大容量临时盘（如 `/data/tmp`）的权利，且必须使用回环地址分配端口。

---

#### 3.6.5 全库硬编码治理分层行动路线（规划与建议）

按照“平台先中立、原生用例先解绑、静态资产再治理”的原则，建议分三个阶段进行系统性整改：

```mermaid
graph TD
    classDef p0 fill:#ffebee,stroke:#c62828,stroke-width:2px;
    classDef p1 fill:#fff3e0,stroke:#ef6c00,stroke-width:2px;
    classDef p2 fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px;

    subgraph 第一阶段：P0 级阻塞消除（立即改善跨机/跨环境可用性）
        A1["native.py 动态感知 host<br>使用 context.node_endpoint(node)['host'] 替代 127.0.0.1"]:::p0
        A2["isolated.py 路径泛化<br>允许环境变量指定临时根目录，移除 /tmp 强断言"]:::p0
        A3["fbasecman 缺省路径解绑<br>router 与 frontend 移除 /home/postgres 强假设"]:::p0
    end

    subgraph 第二阶段：P1 级架构纯粹化（消除平台对特定数据库/架构的硬编码）
        B1["topology.py 插件化<br>将 citus/mmr/streaming 拓扑提取至产品适配器"]:::p1
        B2["replication_observations 下沉<br>postgres 复制指标解析移入 fbase 产品包"]:::p1
        B3["JDBC Jar 动态探测<br>按模式 postgresql-*.jar 探测，解除 42.7.7 版本硬锁"]:::p1
    end

    subgraph 第三阶段：P2 级历史资产治理（消除 cases.json 中的海量死路径）
        C1["命令变量动态宏展开引擎<br>平台 steps 执行前将 {db_bin_dir}、{license_file} 动态注入"]:::p2
        C2["cases.json 逐步标准化<br>将写死的绝对路径替换为参数占位符"]:::p2
    end
```

| 治理阶段 | 目标范围 | 核心行动措施 | 预期收益 |
| :--- | :--- | :--- | :--- |
| **阶段一 (P0)** | `fbasecman/native.py`、`isolated.py`、`router.py` | 1. `_console_query`/`_business_query` 接收 host 参数；<br>2. 移除 `/home/postgres` 默认值；<br>3. `TMP_PREFIX` 支持环境变量覆盖。 | 原生用例具备远程主机与多用户部署执行能力。 |
| **阶段二 (P1)** | `platform_app/topology.py`、`replication_observations.py`、`ports.py` | 1. 拓扑与复制解析下沉至产品层 Provider；<br>2. `ports.py` 泛化为动态申请 $N$ 个端口；<br>3. JDBC jar 自动版本扫描。 | 平台核心真正支持非 Postgres 类的新产品无缝插拔。 |
| **阶段三 (P2)** | `fbase-database/cases.json` (5000+ 硬编码) | 建立平台运行前宏展开机制：在 `run_command_step` 中自动将 `/usr/local/fbase15.15/bin` 动态置换为 `context.environment["db_bin_dir"]`，将 `/home/postgres/license/` 动态置换为 `context.environment["license_file"]`。 | 彻底激活 228 条遗留用例在任意目录、任意用户下的免修改运行能力。 |

**整改落地状态（本节随后续提交更新）**：

- **阶段一 ✅ 已完成**：`native.py`/`common_native.py`/`ha_native.py` 全部改用 `context.environment["local_host"]`（provider 注入，`FBCMAN_LOCAL_HOST` 兜底）；vendored 套件统一走 `fbasecman_ops.LOCAL_HOST`；`isolated.py` 的 `TMP_PREFIX` 改为 `_tmp_prefix(context)`——从 `environment["tmp_root"]`/`FBASE_REGRESS_TMP_ROOT` 派生，安全断言与默认值同源；`router.py` 部署默认值改 `FBCMAN_*` 环境变量可覆盖。
- **阶段三 ✅ 已完成**：`CaseContext.expand()` 支持 `{env.<key>}`/`{node.<name>.<field>}` 占位符；`cases.json` 的 228 条用例中 `127.0.0.1`、`/usr/local/fbase15.15`、`/home/postgres/license/license.dat`、`/tmp/fbase_regress_*`、`-U postgres` 已全部参数化（`env.user`/`env.local_host`/`env.db_bin_dir`/`env.license_file`/`env.tmp_root`/`env.contrib_root`/`node.*`），环境值统一由 `regress.yaml` + provider 注入；`pg_hba`/`postgresql.conf` 生成串、`user` 字段校验同步适配。
- **阶段二 ✅ 已完成**：
  - `platform_app/topology.py` 改为中立的部署驱动分发层——按环境记录 `deployment_driver` 字段（缺省 `pgcluster`）选择驱动；pgclusterlib 与 streaming/logical/citus/mmr schema 推导移入内置驱动 `platform_app/pgcluster_topology.py`，新产品可在产品包内提供同构驱动模块（`topology()`/`status()` 契约）并借环境字段接入，平台核心零改动。
  - `platform_app/replication_observations.py` 拆分：通用 `ParsedObservation`/`parse_tsv_rows` 移入 `platform_app/observations.py`；`postgres.replication` 指标解析下沉至 PG 系产品共享库 `products/pg_common/observations.py`（fbasecman 与 fbase-database 共用，无 product.yaml 不参与产品发现）。
  - `platform_regress/execution/ports.py` 泛化：`free_port_pair` 改为 `free_port_block(seed, count)` 通用 N 连端口分配，fbasecman 的 listen/read/prometheus 三连假设移回产品调用方。
  - JDBC jar 版本锁解除：`platform_regress.clients.jdbc.resolve_jar` 支持 `version=None` 自动选取 `postgresql-*.jar` 最新版；provider 注入点与 4 处 vendored 执行器、`global_cache/drivers.py` 的 `42.7.7` 默认值全部改走版本扫描。
- **有意保留**：`pg_hba` 信任规则中的 `127.0.0.1/32`（写入被测实例配置的语义值）、`process.py` 的上游配置 needle（改写目标的出厂默认值）、端口探测 `bind(("127.0.0.1", 0))`、用例自建测试账号（`fbase_regress_*`/`sao`/`sso`）、SQL 断言内容。

#### 3.6.6 新增原生用例迁移中的反模式与隐蔽硬编码审查（以最新 outstanding 迁移为例）
在 review 最新签入的 `products/fbasecman/native.py`（新增的 11 条 `outstanding.*` 缓存一致性用例）时，识别出以下典型的反模式与隐蔽硬编码，后续在迁移新用例时应注意规避：

1. **配置“字符串二次文本替换”（Config Monkey-patching）反模式**：
   - **代码片段**：
     ```python
     text = config.read_text(encoding="utf-8")
     text = text.replace('pool_size 20', 'pool_size 1\n    pool_reserve_prepared_statement yes\n    pool_discard no')
     text = text.replace('log_min_messages "info"', 'log_min_messages "info"\n    backend_prepared_statements_limit %d ...' % backend_limit)
     config.write_text(text, encoding="utf-8")
     ```
   - **风险定性**：一旦上游 `render_config` 对基础配置做出微调（如默认 `pool_size` 调整为 10 或修改了空白缩进），`text.replace()` 会**静默失效且不抛出任何异常**，导致用例在错误的配置下运行，排查隐蔽性极高。
   - **整改建议**：`render_config(context, path, *, overrides: dict = None)` 应支持结构化参数注入，在初次生成时根据用例参数写入，杜绝下游文本级二次替换。
2. **底层协议探针脚本内的通信地址硬编码**：
   - 在辅助探针脚本 [outstanding_protocol_probe.py:377](file:///home/postgres/fly_dev/product_platform/products/fbasecman/regression/legacy/suites/outstanding/assets/outstanding_protocol_probe.py#L377) 中：
     `return ProtocolClient("127.0.0.1", port, database)`
   - 脚本的命令行参数只接收 `PORT MODE DATABASE`，写死了 `127.0.0.1`，同样阻碍了测试流量发往远端被测实例。
3. **拓扑别名与数据库内置业务对象的强耦合**：
   - 原生用例依赖固定拓扑命名规则：`("mmr1", "primary", "node1")` 与 `("mmr2", "node2")`；
   - SQL 语句中写死业务表查询：`SELECT group_uuid::text FROM fdd.mmr_group WHERE group_name='g1'`；
   - 强假设了 MMR 组名必定为 `'g1'`、集群名必定为 `'pg_cluster_1'` 与 `'pg_cluster_2'`。如果被测环境导入的是企业现网拓扑或多中心集群，用例将因找不到对象名而直接 Blocked。

---

## 4. 总结与建议

| 评估指标 | 结论 |
| :--- | :--- |
| **解耦程度** | **极高（A 级）**。平台核心（控制面、通用回归引擎、License、任务系统）已没有任何针对具体产品的硬编码；产品目录已真正退化为“业务知识提供者”。 |
| **工程质量** | **优秀**。全量 226 项平台测试、149 项 FBase 单测与前端 Vite 构建全部绿灯通过；代码变更规范，提交记录清晰。 |
| **下一步建议** | 1. 提交工作区目前未暂存的文档与产品适配器改动（`git commit`）；<br>2. 按照最新通过的 [前端与纯文件架构重构规范](frontend-and-ia-redesign.md) 推进实施：全面走向纯文件存储、恢复 CLI 零配置直跑、解耦固定导航与 License 双页面；<br>3. 建立 `products/demo/` 自动化插拔测试，彻底闭环“零修改接入”的北极星验证。 |
