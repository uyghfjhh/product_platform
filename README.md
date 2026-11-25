# 公司产品公共管理平台

面向数据库产品的内部工作台，支持集群部署与接管、数据库监控、Studio、
回归测试、压测、稳定性测试和 License 签发。开发进度、未完成工作和验收记录见
[进度文档](docs/progress.md)。用例 API 与报告契约见 [回归 SDK](backend/platform_regress/SDK.md)。

## 技术结构

- **前端**使用 React、TypeScript、Ant Design 和 Vite。构建时生成 API 类型和产品扩展注册。
- **后端**使用 FastAPI。任务服务把请求持久化，再由 Huey worker 执行。Web 和 worker
  共用文件存储中的环境、任务、事件和结果索引。
- **产品接入**从 `products/<产品>/product.yaml` 发现能力。Provider 提供动作、
  用例目录和产品专属执行参数。公共 SDK 执行用例并收集断言、步骤和证据。
  PostgreSQL 系集群部署使用仓库内的 `backend/pgcluster/`。

## 运行

前端构建需要 npm 和 Node.js 20.19.x 以上的 20.x，或 Node.js 22.12.0 及以上。

```bash
./web.sh setup
./web.sh build
./web.sh start
```

默认在浏览器打开 http://localhost:8080。以下命令用于查看状态、查看日志和按需停止：

```bash
./web.sh status
./web.sh logs
./web.sh stop
```

Python 依赖由 `pyproject.toml` 和 `uv.lock` 管理。前端依赖由 `frontend/package.json` 和锁文件管理。

首次安装或更新代码后，执行 `./web.sh setup` 一键安装／同步 Python 依赖，可重复运行。
`./web.sh start` 会检查依赖清单中的运行包，缺包时自动调用 setup。
setup 需要网络，会准备 uv、Python 3.12 和项目 `.venv`；前端依赖与编译另执行 `./web.sh build`。

## 目录结构

### 源码与默认配置

```text
backend/platform_app/       API、任务与环境管理
backend/pgcluster/          数据库集群部署引擎
backend/platform_regress/   回归 SDK、执行和报告
frontend/                   Web 界面与浏览器验收
products/<产品>/            产品声明、部署模板、回归用例和前端扩展
config/                     仓库默认配置
cli/                        CLI 和验证入口
tests/                      平台和 SDK 测试
docs/                       平台说明与开发进度
```

### 平台本地数据

以下目录由运行平台的服务器维护，按需创建。路径中的 `<环境ID>`、`<任务ID>`
和 `<执行ID>` 都是占位符。实际主机地址、实例端口、被测二进制路径和授权目录
在平台配置中维护。常稳任务的产物目录使用该次任务的 `<任务ID>` 作为 `<执行ID>`。

```text
data/                       持久数据，重启后继续使用
  environments/             环境登记和被测构建设置
  profiles/<环境ID>/        部署配置、回归覆盖配置和夹具上下文
  tasks/                    任务记录和事件
  archived-tasks/            已归档任务
  resources/<产品>/<环境ID>/   受管测试资源的回收账本
  regression/<产品>/<环境ID>/  失败重跑记录、执行历史和稳定性状态
  deployment-*/             部署草稿、审阅申请、执行计划和恢复记录
  license/                  平台授权相关数据
runtime/                    运行锁、任务队列和临时工作目录
logs/                       Web 日志
  operations/<任务ID>.log    任务执行日志
output/<产品>/<环境ID>/runs/<执行ID>/
  run.json                  回归执行身份
  cases/<用例目标>/          用例结果、步骤与附件证据
  suite-result.json          批次结果汇总（批量执行）
  report.html + junit.xml    请求导出时生成的回归报告
  longrun/                  常稳负载日志
  memory/                   内存采样和诊断产物
    asan/                   ASan 原始报告和被测构建信息
```

四个本地目录默认位于平台项目根目录，均由 Git 忽略。可通过以下环境变量调整：

| 目录 | 环境变量 |
| --- | --- |
| `data/` | `PRODUCT_PLATFORM_DATA_DIR` |
| `runtime/` | `PRODUCT_PLATFORM_RUNTIME_DIR` |
| `logs/` | `PRODUCT_PLATFORM_LOGS_DIR` |
| `output/` | `PRODUCT_PLATFORM_OUTPUT_DIR` |

设置 `PRODUCT_PLATFORM_DATA_DIR` 后，其余三个目录默认改为新数据目录的同级目录，
也可分别覆盖。执行环境的数据库实例数据目录（PGDATA）由部署配置指定，
可能在远程数据库主机上，需要按数据库备份流程处理。

迁移平台时，先结束运行中的任务，再备份 `data/` 和本机使用的配置覆盖。
若使用 `PRODUCT_PLATFORM_LICENSE_KEYS` 将签发密钥放到外部目录，该目录也要单独备份。
需要追溯测试结果时，一并备份 `output/` 和 `logs/`。恢复后核对环境端点、
被测二进制和授权文件路径。整理报告可单独处理 `output/`，任务历史和资源回收账本
仍以 `data/` 为准。运行期间保留 `runtime/` 中的队列和锁。

## 验证

```bash
./cli/check.sh quick
./cli/check.sh full
(cd frontend && npm run build)
git diff --check
```

## 接入回归用例和生成报告

### 新增用例并用 CLI 验证

从仓库根执行下面的例子。它在临时目录复制 Demo 产品清单，新增
`smoke.context_check`，核对执行上下文里的环境 ID，再生成 HTML 和 JUnit。
示例无需连接数据库。

```bash
example_dir=$(mktemp -d)
mkdir -p "$example_dir/product"
cp products/demo/product.yaml "$example_dir/product/product.yaml"
cat > "$example_dir/product/cases.py" <<'PYTHON'
from platform_regress.sdk import CaseContext


class ContextCheck:
    def run(self, context: CaseContext) -> None:
        actual = context.environment.get("id")
        context.attach_text("environment.txt", str(actual) + "\n")
        context.check("environment-id", "核对环境 ID",
                      actual == "demo-local", expected="demo-local", actual=actual)


CASES = {"smoke.context_check": ContextCheck()}
PYTHON

PYTHONPATH=backend:. .venv/bin/python -m platform_regress.cli \
  --product-dir "$example_dir/product" \
  --output-dir "$example_dir/output" \
  --state-dir "$example_dir/state" \
  --context-json '{"id":"demo-local"}' \
  --html report.html --junit junit.xml smoke.context_check
```

打开 `$example_dir/output/runs/<执行ID>/report.html` 查看报告，同级的 `junit.xml`
可交给 CI。把 `--context-json` 中的 ID 改成另一个值后重跑，报告应显示期望与实际值的差异，
CLI 退出码为 1。数据库用例还需提供节点和用户上下文，例如用
`--node "primary=<主机>:<端口>"` 和 `--user "<测试用户>"` 注入。

### 在 Web 产品测试中心接入

1. 在目标产品的 `cases.py` 把实现加入 `CASES["<suite>.<case>"]`。
   若产品有独立目录（如 fbasecman 的 `regression/catalog.json`），同步新增相同
   target 的条目、标题、目的和启用状态。
2. 在产品 `product.yaml` 声明 `regression_sdk: "2"`，确认套件属于覆盖目标
   拓扑的 `test_profiles`，且产品声明了对应回归动作。Provider 应发现该 target、
   接受它的校验，并在执行时将同一个 target 交给回归 CLI。
3. Provider 的结果发布应包含该 target，按本次任务与执行身份关联。产品如需前端扩展，
   在产品的前端注册文件声明入口后重新构建。可参考 [Demo Provider](products/demo/provider.py)
   和 [fbasecman Provider](products/fbasecman/provider.py)。
4. 在「产品测试中心」给相应测试 profile 绑定环境，运行新增 target。结束后在该用例
   的结果里打开报告和附件。Demo 产品是接入参考，当前菜单隐藏其入口。

环境级导出可用
`/api/v1/environments/<环境ID>/reports/html` 和
`/api/v1/environments/<环境ID>/reports/junit`。
用例前置条件、资源恢复、断言和证据规则见 [回归 SDK](backend/platform_regress/SDK.md)。

## 常用工作台

| 入口 | 操作 |
| --- | --- |
| 数据库部署管理 | 新建或接管集群，审阅变更，必要时继续原执行计划。 |
| 数据库监控 | 查主机、实例、复制和 MMR 状态。采集能力因产品和权限而异。 |
| 数据库管理 | 用 Studio 查看对象、运行查询及检查会话、锁和复制。 |
| 数据库压测 | 选择 pgbench／JDBC 负载，查看吞吐、延迟和已配置的性能阈值。 |
| 平台运营 | 管理流水线、定时任务、通知、产品包和知识检索。 |

### 部署方案与数据库管理

部署方案可从产品模板新建、接管已有实例或导入 pgcluster YAML。先选主机、数据库
安装和节点参数，保存草稿，再做只读检查、生成并审阅计划。预检验证安装、所需插件
和授权、端口及数据目录。新建会拒绝端口冲突或已有数据，接管和导入通过登记与健康
验收。自动接管面向同一主机上的实例组，跨主机配置可导入 YAML。

环境 ID 随部署登记保留，草稿修改后应重新生成计划。任务使用审阅时的配置快照，
执行前重新核对配置摘要与节点前提。初始化失败时保留现场，排查后重新检查再提交。
部署草稿和计划位于 `data/deployment-drafts/` 与 `data/deployment-plans/`。

从部署拓扑点节点可查看实例状态，节点的 SQL 入口进入同一套 Studio 工作区，
目标实例按环境和节点 ID 解析。测试绑定在「产品测试中心」设置。
Studio 浏览器验收使用 `frontend/tests/studio-workspace.mjs`。

### 部署与监控的操作边界

- 物理备库升主须明确选中目标备库，并核对当前主库和 WAL 回放。旧主无法确认已停止时，
  停在当前阶段，先确认角色和状态。重建旧主涉及替换已登记的数据目录，须核对页面提示的
  目录和受管归属。计划内 MMR 角色操作还需核对前置健康状态，并在切换后复检。
- 主库跨主机或跨目录迁移前，核对逻辑槽能否延续、备用节点的有效位置及所需 WAL。
  缺少所需位置或 WAL 时保持源主库，先处理复制前提。
- Prometheus 使用 `GET /api/v1/environments/<环境ID>/monitoring/metrics`。
  接口返回缓存样本，可结合 `platform_monitor_sample_available` 和
  `platform_monitor_sample_age_seconds` 判断是否新鲜。导出的速率已经过计算，
  抓取侧直接使用对应的 gauge。权限不足或采样失效时，页面和出口按可观测状态展示。

### fbasecman 稳定性测试

在「产品测试中心 → fbasecman → 稳定性测试 → 启动测试」配置负载和时长。
标准套件默认勾选 12 项，正式时长为 600 秒。执行使用上游用例与隔离的六节点 MMR 环境。
控制机需能访问源代码、FBase 安装及相关授权。`PRODUCT_PLATFORM_CMAN_SOURCE_ROOT`
可指定源码根目录，`PRODUCT_PLATFORM_FBASECMAN_STABLE_PG_HOME` 可指定数据库安装。
被测构建在环境设置里登记。

在同一个配置弹窗可选 **jemalloc** 或 **ASan**。预热复用本轮负载和代理，额外计算时长。
启动后打开「稳定性测试 → 内存诊断」或任务的「查看内存诊断」。

- **jemalloc** 查看 RSS、存活堆、完整分配栈排行、调用树和火焰图。开启预热后，
  对比默认从预热后的第一份完整快照开始。控制机需安装支持 heap profiling 的
  jemalloc、`jeprof` 和 Graphviz 的 `dot`。平台从 `runtime/jemalloc/install` 或系统
  工具路径查找，也可用 `PRODUCT_PLATFORM_JEMALLOC_SO`、`PRODUCT_PLATFORM_JEPROF` 指定
  库和 `jeprof`。
- **ASan** 选用带 AddressSanitizer 插桩的已登记构建，平台校验插桩并记录指纹。
  页面展示可解析的非法访问摘要，可复制路径、复制原文或下载完整报告。
  原文位于本轮产物的 `memory/asan/`。控制机需有 `readelf` 以检查插桩符号，
  可安装 `llvm-symbolizer` 以解析源码位置。

### 故障定位

任务失败时先看任务详情和 `logs/operations/<任务ID>.log`。回归失败再看同次执行的
`result.json`、步骤和附件，常稳再看 `longrun/` 和 `memory/`。
部署中断时，从页面查看失败步骤和恢复状态，按原审阅计划继续。

## License 默认设置

在「License 授权管理 → 默认设置」维护厂商、默认密钥、有效期、用途、常用 MAC，以及每个产品的默认授权、版本和独立有效期。设置保存到 `config/license/defaults.json`，生成页下次打开时预填；日期保存为相对规则，默认当天生效、十年到期。单次签发修改不覆盖默认设置，默认密钥口令可配置，用于表单预填，不改变实际密钥口令。产品版本可填写新版本，需与实际二进制匹配。

生成页可勾选「同时保存到服务器目录」，选择平台服务器上的目标目录。目录与开关也保存在 `config/license/defaults.json`。保存文件为 `license.dat`，同名文件会替换，浏览器仍可下载。配置根目录可通过 `PRODUCT_PLATFORM_CONFIG_DIR` 指定。
