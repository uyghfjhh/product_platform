# 公司产品公共管理平台：纯文件存储、CLI 体验、Report 体系与前端 IA 全面重构设计规范

- 版本：v2.0 · 2026-09-28
- 状态：评审通过 · 最终实施基准
- 适用范围：`backend/`、`frontend/`、`products/`、CLI 工具链与配置体系

---

## 1. 核心设计原则与哲学转型

### 1.1 摒弃数据库，回归 Unix 哲学（Configuration & Data as Files）
平台定位为**本地单机版开发与测试工作台**，核心用户为底层数据库与中间件工程师。原先引入的 SQLite 关系型数据库（`platform.sqlite3`）在单机研发场景中构成了“黑盒”，破坏了终端 CLI 脚本的独立性与透明度。

本次重构确立核心原则：
> **“能不用数据库的，就坚决不用数据库；全面采用结构化文件存储（YAML / JSON / Text）。”**

* **透明可查**：任何配置直接 `cat / vim` 查看与修改；
* **终端第一（CLI-First）**：命令行脚本直接读写文件，不依赖 Web 后端服务或 SQLite 驱动；
* **零外部状态与自包含**：整个工作台的数据就是普通的目录文件，`cp -r data/` 即完成备份与迁移，彻底废除 SQLite 数据库文件与版本迁移脚本（`migrations.py`）；
* **POSIX 原子写入**：多进程并发写入采用“写入临时文件 + `os.replace` 原子重命名”机制，辅以标准 `fcntl.flock` 文件锁，确保零并发损坏风险。

---

## 2. 纯文件存储体系架构（File-Based Architecture）

### 2.1 整体目录规范
彻底移除 `platform.sqlite3`，所有运行态元数据统一收敛至 `data/` 目录：

```text
data/
├── environments/               # 【环境配置库】一套环境对应一个独立 YAML
│   ├── mmr_cluster.yaml        # 2中心4节点 MMR 集群配置
│   └── mac_streaming.yaml      # 等保流复制备库集群配置
│
├── bindings.yaml               # 【测试-环境映射表】记录各产品测试绑定的环境 (M:1)
│
├── tasks/                      # 【操作任务】每个任务一个独立目录
│   └── <task_id>/
│       ├── meta.json           # 任务状态、PID、动作、目标、起止时间
│       └── output.log          # 任务实时标准输出与错误日志
│
├── regression/                 # 【回归测试产物与 Report】
│   └── <environment_id>/       # 按环境归档的最新回归全量产物
│       ├── report.html         # 🌟 自包含全景 HTML 测试报告（可离线双击打开）
│       ├── junit.xml           # 🌟 标准 JUnit XML 报告（供 CI/CD 读取）
│       ├── suite-result.json   # 全套用例判决事实汇总
│       └── <case_target>/      # 单个用例的独立证据包
│           ├── result.json     # 单用例事实与裁决
│           ├── steps.json      # 步骤快照与拓扑时序数据 (供 3D 回放)
│           ├── report.txt      # 单用例纯文本报告 (终端直接 cat)
│           └── logs/           # 节点运行日志 (fbasecman.log 等)
│
└── keys/                       # 【License 密钥库】
    ├── 1.1.json                # ECC 密钥对、公钥指纹与撤销标记
    └── 1.2.json
```

### 2.2 核心配置文件规格

#### 1. 环境定义 (`data/environments/<env_id>.yaml`)
```yaml
id: mmr_cluster
title: "2中心4节点MMR集群"
product_id: fbasecman
host: "127.0.0.1"
port: 5432
database_name: postgres
database_user: postgres
deployment_config: "/home/postgres/pgcluster/pgcluster.yaml"
deployment_target: "mmr.fbasecman_regress"
created_at: "2026-09-28T10:00:00Z"
```
* **终端操作**：工程师修改端口直接 `vim data/environments/mmr_cluster.yaml`；新增环境直接 `cp mmr_cluster.yaml test.yaml`。

#### 2. 测试绑定表 (`data/bindings.yaml`)
记录每个测试场景当前绑定的环境（M:1 关系）：
```yaml
# 规则: <product_id>.<test_profile_id>: <environment_id>
fbase-database.mmr: mmr_cluster
fbase-database.mac: mac_streaming
fbasecman.cman: mmr_cluster
fbasecman.stability: mmr_cluster
```
* **多测试复用**：`mmr_cluster` 同时承载了 FBase 多活测试、cman 回归测试与稳定性测试。
* **CLI/Web 统一读取**：切换绑定只需更新该文件中的一行。

#### 3. 任务元数据 (`data/tasks/<task_id>/meta.json`)
```json
{
  "id": "task_20260928_153000",
  "environment_id": "mmr_cluster",
  "action": "tests.fbasecman",
  "target": "ha_commands",
  "status": "RUNNING",
  "process_id": 49201,
  "created_at": "2026-09-28T15:30:00Z",
  "started_at": "2026-09-28T15:30:01Z",
  "finished_at": null,
  "reason": null
}
```

---

## 3. 命令行（CLI）极致体验保障

### 3.1 零配置一键执行（回归老代码便捷习惯）
彻底解除对 `PRODUCT_PLATFORM_PGCLUSTER_CONFIG` 等环境变量的强校验，脚本启动时**自动按层级解析上下文**：
1. **优先读取指定参数**：若传入 `--env <env_id>`，使用指定环境；
2. **缺省自动感知绑定**：未传环境参数时，直接读取 `data/bindings.yaml` 匹配当前测试所绑定的环境 YAML；
3. **自动装配连接参数**：自动提取 `deployment_config`、`host`、`port`，实现**零配置即敲即跑**。

```bash
# 1. 运行整套套件（最常用，秒级启动）
./cli/run.sh run ha_commands

# 2. 运行单条用例
./cli/run.sh run ha_commands.001

# 3. 仅重跑上次失败的用例
./cli/run.sh run failed

# 4. 运行全量用例
./cli/run.sh run all

# 5. 辅助自检与维护
./cli/run.sh show ha_commands      # 列出用例清单
./cli/run.sh doctor                # 环境健康体检
./cli/run.sh clean                 # 清理日志与临时产物
```

### 3.2 终端输出与 Report 交互流
执行过程中终端输出实时步进信息，完成后自动打印便携式 HTML 报告路径：

```text
$ ./cli/run.sh run ha_commands

[INFO] 加载绑定环境: mmr_cluster (127.0.0.1:5432)
[INFO] 开始执行套件: ha_commands (48 项用例)

  [01/48] ha_commands.001  基础路由与主备探测 ....................... PASS  (0.3s)
  [02/48] ha_commands.002  主节点宕机与自愈选举 ..................... PASS  (1.2s)
  [03/48] ha_commands.003  跨中心读写分离切换 ....................... PASS  (0.5s)
  ...
  [48/48] ha_commands.048  长连接保活与断线重连 ..................... PASS  (0.4s)

========================================================================
✔ 测试通过: 48 | ✘ 失败: 0 | 耗时: 16.8s
📄 交互式 HTML 报告: data/regression/mmr_cluster/report.html
📊 标准 JUnit XML:   data/regression/mmr_cluster/junit.xml
========================================================================
```

---

## 4. 全景 Report（测试报告）核心价值体系

Report 是系统最核心的交付凭据与研发资产，全部基于纯文件体系构建：

### 4.1 四大 Report 形态与交付能力
1. **全景交互式 HTML 报告 (`report.html`)**：
   * 单文件自包含，内嵌完整 CSS 与 JS；
   * 包含测试通过率仪表盘、执行耗时排名、用例分类检索、失败堆栈与诊断信息；
   * **离线即开即看**：不依赖任何本地服务，在任意计算机上双击即可用浏览器查看。
2. **单用例 2D/3D 拓扑时序回放报告 (`ReportViewer`)**：
   * 针对复杂的数据库集群主备倒换、网络分区与自愈场景；
   * 读取用例目录下的 `steps.json`，在 Web 界面呈现 **2D/3D 拓扑动态回放**（支持播放、暂停、1x/2x/4x 调速、断言表格对比）；
   * 日志穿透：右侧面板实时分页阅读 `fbasecman.log`、`proxy.log`。
3. **单用例纯文本报告 (`report.txt`)**：
   * 专为终端工程师打造，在控制台直接 `cat report.txt` 或 `grep FAIL report.txt` 即可秒级定位断言失败步骤。
4. **CI/CD 标准产物 (`junit.xml`)**：
   * 输出标准 JUnit XML 格式，Jenkins、GitLab CI 零配置直接抓取生成测试趋势曲线。

---

## 5. 前端信息架构（IA）与导航彻底重构

### 5.1 左侧固定导航树（彻底剥离动态环境）
左侧侧边栏严格保持固定，杜绝任何随数据增删而跳变的动态子菜单：

```text
┌────────────────────────────────────────────────────────┐
│  F 产品工作台 (内部管理平台)                            │
├────────────────────────────────────────────────────────┤
│  🛠️ 数据库部署管理          [deployment]                │
│                                                        │
│  ▶️ 产品测试中心                                       │
│     ├─ 🐬 FBase 数据库                                 │
│     │    ├─ 多活测试        [tests:fbase-database:mmr] │
│     │    └─ 等保测试        [tests:fbase-database:mac] │
│     └─ 🛡️ fbasecman                                    │
│          ├─ 回归测试        [tests:fbasecman:cman]     │
│          └─ 稳定性测试      [tests:fbasecman:stability]│
│                                                        │
│  🔐 License 授权管理                                   │
│     ├─ 🔑 密钥管理          [license:keys]             │
│     └─ 📝 License 生成      [license:generate]         │
├────────────────────────────────────────────────────────┤
│  ⚙️ 系统设置 (本地单机版)    [唤起 SettingsModal]       │
└────────────────────────────────────────────────────────┘
```

### 5.2 Header 顶栏极简设计（去除下拉框）
* **左侧**：移动端折叠按钮 + 面包屑/当前页面标题；
* **右侧**：
  * 后台任务状态 Tag（仅在存在执行中任务时展示，点击打开日志抽屉）；
  * `⚙️ 设置` 按钮（点击唤起系统设置模态框）；
  * **彻底移除原有的「当前环境下拉框」与「风格切换下拉框」**。

### 5.3 风格切换下沉至「系统设置」模态框 (`SettingsModal`)
* 承载 4 种界面风格的卡片式选择：
  * 🌌 **极客夜蓝 (`cman`)**（暗黑冷蓝荧光）；
  * 🌑 **深色石墨 (`dark`)**（专业炭黑雅绿）；
  * 🍵 **柔和灰绿 (`soft`)**（低对比度浅色护眼）；
  * 🍂 **暖灰护眼 (`warm`)**（暖灰纸质护眼）。
* 展示本地单机版运行环境信息与产品清单。

---

## 6. 页面内部就地环境管理与绑定

### 6.1 数据库部署管理 (`DeploymentPage`)：多集群运维闭环
* **集群控制卡片**：直接在部署页面顶部提供环境切换器，展示名称、IP、端口及绑定用途；
* **新建环境弹窗 (`EnvironmentModal`)**：内嵌原 `EnvironmentsPage` 的完整 CRUD 能力，创建后直接落盘为 `data/environments/<id>.yaml`；
* **即席 SQL 探测抽屉 (`SqlWorkbenchDrawer`)**：拓扑节点点击直接拉起抽屉执行 SQL 探测，修复原有 `navigate('database')` 死链。

### 6.2 测试页面内部：测试执行就地绑定栏
在 `TestsPage.tsx` 与 `StabilityPage.tsx` 页面顶部内置绑定控制栏：
* **已绑定状态**：`🎯 执行环境: [ mmr_cluster (127.0.0.1:5432) ▼ ] 🟢 已连通 (共用: fbasecman回归)`；
* **就地切换**：下拉仅展示与当前测试拓扑兼容的环境，选择后立即原子更新 `data/bindings.yaml`；
* **未绑定状态**：醒目横幅引导 `⚠️ 当前测试尚未绑定环境，请选择一套环境以开始测试`。

---

## 7. License 拆分为独立双页面

* **路由 1：密钥管理 (`license:keys` / `LicenseKeysView.tsx`)**：
  * 密钥版本列表（版本号、公钥一键复制、SHA256 指纹、状态）；
  * 生成新版本（输入版本与私钥口令）；
  * 密钥维护（口令轮换、安全撤销、安全删除）。
* **路由 2：License 生成 (`license:generate` / `LicenseGenerateView.tsx`)**：
  * 选择有效密钥版本与生效日期；
  * 动态多产品授权矩阵（添加 FBase/cman 及其到期日）；
  * MAC 地址多网卡换行录入与正则校验；
  * 输入口令一键签名并触发浏览器下载 `license.dat`。

---

## 8. 前端代码审查缺陷专项治理清单

1. **消除无节制短轮询**：
   * `TestsPage.tsx` 移除固定的 `setInterval(2500ms)` 死循环，改为仅在存在 `RUNNING` 任务时轮询，静态查阅时单次加载并提供刷新按钮。
2. **清理绝对路径与硬编码端口**：
   * 移除 `DeploymentPage.tsx` 中写死的 `15011` 与 `/home/postgres/license/license.dat`，改为动态从当前环境对象派生。
3. **消除主题色彩撕裂**：
   * 拔除 TSX 行内写死的深色样式（如 `background: '#111726'`），统一切换为 Ant Design Token 与 CSS 语义变量。
4. **增设全局 React 错误边界 (`PlatformErrorBoundary`)**：
   * 包裹主内容区，防止 3D WebGL 异常或渲染错误导致全页白屏崩溃。

---

## 9. 实施路线图（分阶段落地）

```text
阶段一：纯文件存储引擎落地 (P0)
  ├─ 实现 platform_app.filestore，替代基于 sqlite3 的 storage.py
  ├─ 环境与绑定落盘到 data/environments/*.yaml 与 data/bindings.yaml
  └─ 废除 migrations.py 与 platform.sqlite3

阶段二：CLI 命令行零配置打通 (P0)
  ├─ 改造 cli/run.sh 与 platform_regress.cli，支持直接读 YAML 环境
  ├─ 彻底恢复 ./cli/run.sh run ha_commands 免配置直跑
  └─ 确保执行完毕自动打印离线 report.html 与 junit.xml 路径

阶段三：前端外壳与导航解耦 (P0)
  ├─ 编写 SettingsModal.tsx（4 款主题与系统信息）
  ├─ 改造 PlatformShell.tsx：菜单固定化，移除环境子项；Header 移除下拉框
  └─ 增加 PlatformErrorBoundary 容错保护

阶段四：部署管理与测试就地绑定 (P1)
  ├─ DeploymentPage 内嵌 EnvironmentModal 与 SqlWorkbenchDrawer
  ├─ TestsPage / StabilityPage 顶部增加就地测试绑定栏
  └─ 优化 2.5s 轮询为按需感知轮询

阶段五：License 双页面彻底拆分 (P1)
  ├─ 拆分为 LicenseKeysView 与 LicenseGenerateView
  └─ PlatformShell 配置独立子路由

阶段六：构建验证与回归验收 (P2)
  ├─ 运行全量 pytest（226 项平台单测 + 产品测试）
  ├─ 运行 npm run build 验证前端生产打包
  └─ 验证 CLI 与 Web 全流程闭环
```

---

## 10. 极客研发杀手级特性规划 (Developer Delight Features)

为极大提升底层数据库/中间件研发日常调试与交付体验，在基础架构稳固后同步推进以下 6 项高价值特性：

### 10.1 失败断言智能差分高亮器 (Visual Diff Inspector)
* **场景**：复杂 JSON 状态表、GUC 参数、大段 SQL 查询结果集比对失败。
* **特性**：在 Web 失败抽屉与离线 HTML 报告中引入 Monaco/Git 风格双栏对比（左 Expected vs 右 Actual），自动红绿高亮具体字符/键值差分，1 秒精确定位断言 Bug。

### 10.2 一键故障分析包导出 (One-Click Bug Bundle Zip)
* **场景**：测试跑挂后，将案发现场完整提供给研发同事或上级复现。
* **特性**：
  * Web 端一键点击 `[📦 导出故障分析包]` 或 CLI 执行 `./cli/run.sh pack <case_id>`；
  * 自动打包该用例的 `report.html`、`result.json`、`steps.json`、案发时段日志与环境配置，生成独立自包含的 `issue_<case_id>.zip`，解压双击直接复现场景与 3D 拓扑回放。

### 10.3 CLI 极客专属 `--watch` 监听热重跑模式
* **场景**：单用例密集调试与 TDD（测试驱动开发）。
* **特性**：
  * 执行 `./cli/run.sh run ha_commands.001 --watch`；
  * 自动监听用例文件、配置或构建产物变化，保存即自动触发秒级重跑，双屏开发心流不被打断。

### 10.4 破坏性测试的“一键基线自愈与重置” (Fast Cluster Reset)
* **场景**：高可用故障注入（杀主库、断网、裂脑）后集群变脏，导致后续测试连续误报。
* **特性**：
  * CLI 执行 `./cli/run.sh reset` 或 Web 点击 `[🔄 一键基线重置]`；
  * 杀掉残留孤儿代理进程、恢复主备路由、清理临时脏表，3 秒内将集群拉回纯净基线状态。

### 10.5 跨次执行“偶发失败用例追踪” (Flaky Test Hunter)
* **场景**：分布式数据库时序竞态、偶发超时、“薛定谔用例”。
* **特性**：
  * 纯文件记录最近 5 次执行历史，自动识别偶发失败用例并在看板标记 `⚠️ 偶发不稳定 (Flaky: 4 Pass / 1 Fail)`，提示研发排查并发锁或 sleep 等待时限。

### 10.6 Web 端后台长任务“浏览器桌面提醒” (Desktop Notification)
* **场景**：全量回归耗时较长，工程师切换到其他工作窗口等待。
* **特性**：
  * 利用原生 Web Notification API，在后台任务结束时由操作系统右下角推送系统级通知：`🎉 fbasecman 回归测试完成：48 通过，0 失败`，点击直接切回工作台。

---

## 11. 数据库与分布式系统高阶进阶特性规划 (Advanced Database & HA Tooling)

面向底层数据库（FBase / PostgreSQL）与集群管理高可用（fbasecman）深度研发场景，规划以下 6 项进阶工程利器：

### 11.1 CoreDump 自动抓取与 GDB 堆栈秒级解析 (GDB Auto-Backtrace)
* **痛点**：底层 C 语言代码段错误（SIGSEGV / Crash）时，传统方式需手动登机定位 `core` 文件并执行 `gdb`。
* **特性**：
  * 用例执行一旦发生 Crash，平台取证模块自动检测并后台调用 `gdb --batch -ex "bt full"`；
  * 提取结构化崩溃堆栈，并在 Web 失败报告的【崩溃调用栈】Tab 中直接高亮呈现源码行号与函数调用链，免去手动 GDB 繁琐操作。

### 11.2 混沌工程与一键故障模拟注入器 (Chaos & Fault Injection Tooling)
* **痛点**：高可用（HA）故障测试依赖复杂的人工命令行或测试脚本注入，缺乏即时直观的交互手段。
* **特性**：
  * 在部署管理页面的 2D/3D 拓扑图上，支持右键节点或通信链路一键注入故障：
    * 💥 **硬宕机**：`SIGKILL` 杀死实例；
    * ⏸️ **假死卡顿**：`SIGSTOP` 模拟高负载/GC 停顿，随后 `SIGCONT` 唤醒；
    * 🌐 **网络分区（Split-Brain）**：阻断跨中心主备通信流；
    * 🐌 **网络抖动**：注入 100ms 延迟或 10% 丢包率；
  * 拓扑连线实时变红断开，研发可直接目击 cman 是否在 SLA 时限内完成故障转移与路由重定向。

### 11.3 用例耗时异常飙升高亮与性能衰减探测 (Performance Regression Alert)
* **痛点**：加锁机制或内部逻辑微调导致功能全绿但执行变慢，传统回归无法察觉潜藏的性能劣化。
* **特性**：
  * 纯文件记录历史执行基准耗时（Duration Baseline）；
  * 当某次执行耗时超过历史平均值的 **200%** 时，报告自动打上高危标牌：
    `⚠️ 耗时异常激增 (+320%, 0.2s -> 0.8s)`，预警锁竞争、缺少索引或全表扫描。

### 11.4 集群 GUC 参数跨环境对比与体检 (GUC Inspector & Diff)
* **痛点**：PostgreSQL/FBase GUC 参数浩繁，开发、测试与生产环境行为不一致通常源于配置偏差。
* **特性**：
  * 在部署管理中提供 GUC 快速体检与双栏差分比对；
  * 选中两套环境或两个节点，自动拉取 `pg_settings` 并高亮不一致参数（如 `synchronous_commit`, `wal_level`）；
  * 标记偏离安全基准的危险参数。

### 11.5 新用例“一键模板化脚手架生成” (Case Scaffolding CLI)
* **痛点**：修复 Bug 后补充回归用例，手动复制旧脚本、修改类名与目录繁琐易错。
* **特性**：
  * CLI 命令支持：
    ```bash
    ./cli/run.sh new-case ha_commands "主备切换重试用例"
    ```
  * 自动在目标套件目录生成标准用例类、生命周期钩子、断言模板，并自动写入用例目录清单，研发仅需填入核心 SQL。

### 11.6 SQL 真实流量录制与影子回放 (Traffic Capture & Shadow Replay)
* **痛点**：合成测试用例难以覆盖生产现网极其复杂的 SQL 组合与高并发混合读写时序。
* **特性**：
  * 在 cman 代理层提供轻量级抓包录制开关，将真实前端会话捕获为压缩流 `traffic.sql.gz`；
  * 支持命令 `./cli/run.sh replay traffic.sql.gz` 在仿真集群上全保真回放，作为大版本发布前的终极稳定性验证。

---

## 12. 数据库内核与分布式协议硬核深水区特性 (Hardcore Kernel & Protocol Observability)

面向底层通信协议深度调试、性能极端优化与团队工程化提效，规划以下 6 项硬核能力：

### 12.1 PostgreSQL 协议报文泳道时序图 (pgwire Packet Sequence Diagram)
* **痛点**：网关代理排查复杂连接悬挂或协议差错时，文本抓包难以还原完整的客户端与后端主备交互因果。
* **特性**：
  * 用例调试模式下自动捕获并解析 pgwire 握手与消息流（`Startup`、`Parse/Bind/Execute`、`CommandComplete`、`ReadyForQuery`）；
  * 在 Web 端单用例报告中渲染交互式 Mermaid 协议时序泳道图，节点转发流转一目了然。

### 12.2 进程文件句柄与内存泄漏探针 (FD & Memory Leak Hunter)
* **痛点**：隐蔽的 Socket 连接漏关、临时文件未删或微量内存泄漏，常导致长时间常稳测试中途因资源耗尽而崩溃。
* **特性**：
  * 用例执行前后通过 `/proc/$PID/fd` 与系统探针记录 RSS 物理内存与打开的 FD 句柄数；
  * 差分比对：一旦用例执行完毕发现净增残留（如未关闭的 Socket 句柄），直接在报告中打上红色警告：
    `⚠️ 潜在资源泄漏: Socket FD +2 (未正常 close), 内存净增 +1.2MB`。

### 12.3 多节点分布式日志“时间戳纳秒对齐流” (Correlated Multi-Node Log Stream)
* **痛点**：双主多备集群发生故障时，研发需同时在多个终端窗口手动比对主备库与网关日志的时间戳。
* **特性**：
  * 平台自动归并采集所有被测节点（`mmr1`、`mmr2`、`cman`）日志，按纳秒时间戳统一归并排序；
  * 按组件色彩统一渲染单流日志视图，因果时序链路无需人工拼凑。

### 12.4 优化器执行计划回归防退化检查 (Query Plan Regression Check)
* **痛点**：内核大版本升级时优化器决策退化（如原走 Index Scan 的语句突变为 Seq Scan），功能全绿但线上慢查打满。
* **特性**：
  * 核心用例自动保存 `EXPLAIN (COSTS OFF)` 抽象算子树；
  * 跨版本自动比对计划变动，算子一旦发生严重退化（如索引扫描丢失）立即触发高危告警。

### 12.5 双环境/新旧版本 A/B 差分测试 (Differential Testing)
* **痛点**：内核重构后难以 100% 确认新旧实现行为完全一致。
* **特性**：
  * 支持命令：
    ```bash
    ./cli/run.sh ab-test --env-a v17_cluster --env-b v18_cluster ha_commands
    ```
  * 流量双发，逐行、逐列对齐比较两套集群返回的元组集合、错误码与协议通知，捕获细微行为分歧。

### 12.6 开发环境工具链“一键自愈修复” (doctor --fix)
* **痛点**：新机器克隆代码后经常遭遇缺少依赖、缺少动态库软链接、端口占用等“配置地狱”。
* **特性**：
  * 执行 `./cli/run.sh doctor --fix`；
  * 自动补齐 `.venv` 环境、安装缺失依赖、释放冲突端口与锁、补齐 JDBC 探针 jar，5 分钟内达到开箱即跑状态。

---

## 13. 执行加速、交互调试与质量防线特性 (Execution Acceleration & Quality Defense)

围绕测试反馈速度极致压缩、复杂断点就地交互与质量门禁，规划以下 6 项利器：

### 13.1 复杂分布式用例“断点单步调试模式” (--step REPL)
* **痛点**：分布式用例步骤繁多，中途某步断言失败自动清理退出，无法探查挂掉那一刻的存活集群现场。
* **特性**：
  * 执行 `./cli/run.sh run ha_commands.002 --step`；
  * 每步执行完毕自动暂停挂起，提示 `[Enter] 下一步 | [s] 启动交互 psql 探查 | [q] 退出`；
  * 敲 `s` 原地拉起连接到存活主库的 `psql` 交互终端查表，探查完毕退出继续下一环节，告别低效的代码 sleep。

### 13.2 内存盘 (RAM-Disk) 极速运行模式 (--ramdisk 提速 3~5 倍)
* **痛点**：频繁启停节点与物理磁盘 `fsync` 刷盘耗时长，且严重磨损本地 SSD。
* **特性**：
  * 执行 `./cli/run.sh run all --ramdisk`；
  * 自动将临时 PGDATA 与运行目录挂载至 Linux 共享内存 `/dev/shm`；
  * 全套 200 条用例执行耗时从 3 分钟锐减至 30 秒，本地硬盘零损耗。

### 13.3 多活 (MMR) 数据一致性与 LSN 漂移探针 (Data Consistency Validator)
* **痛点**：多活双主或流复制备库容易发生隐蔽的“数据静默不一致”（Silent Inconsistency / 数据漂移）。
* **特性**：
  * 测试完毕后自动触发校验探针：
    * 比对所有节点 `pg_current_wal_lsn()` 与流复制延迟；
    * 采用表级哈希对齐核验两中心业务数据，报告给出 `🟢 数据 100% 强一致 (0 LSN 延迟)` 权威结论。

### 13.4 License 授信健康度哨兵与一键续期 (License Health Sentinel)
* **痛点**：测试中途突然因 License 过期或网卡 MAC 不匹配报错中断，排查费时。
* **特性**：
  * 在 License 页面与全局状态栏增设健康哨兵，实时巡检当前环境 `license.dat` 剩余天数；
  * 临期自动告警并提供 `[⚡ 一键续签 30 天并原地热替换]`。

### 13.5 Git 提交前自动化快速门禁 (git pre-push Hook)
* **痛点**：修改底层代码后遗忘运行单测，导致手误污染团队主干分支。
* **特性**：
  * 支持 `./cli/run.sh install-hook` 自动注入 `.git/hooks/pre-push`；
  * 依据 git diff 仅自动运行改动模块的核心冒烟用例（耗时 < 3s），全绿方可 push。

### 13.6 AI 辅助崩溃日志根因推导与修改建议 (AI Diagnostic Assistant)
* **痛点**：上万行崩溃日志与 Coredump 现场，排查定位耗时费力。
* **特性**：
  * Web 失败报告中提供 `[🤖 AI 智能诊断]`；
  * 结合失败断言、最后 50 行错误日志、GDB 堆栈与最近 git diff，秒级推导可能根因并推荐代码修改点。

---

## 14. 拓扑流量可视化、政企合规与并发排障利器 (Topology Flow & Enterprise Compliance Tooling)

面向可视化交互升级、政企客户现场交付与并发死锁排查，规划以下 6 项核心能力：

### 14.1 2D/3D 拓扑画板“实时动态流量粒子与连接池监控” (Flow Particle Visualizer)
* **痛点**：拓扑画板仅展示静态节点，无法直观观察读写分离转发真实流向与连接池负载。
* **特性**：
  * 通信连线上渲染动态流动发光粒子（Flow Particles），粒子流向代表 SQL 路由目标，密度代表实时 QPS；
  * 节点悬浮卡片展示：`连接池: 24/100, 活跃连接: 8, 读写分布: 70% 读 / 30% 写`；
  * 主备倒换时，亲眼目睹流动粒子瞬间从故障节点切向新主节点。

### 14.2 等保三级 (MAC)“自动化合规自评与报告导出” (Compliance Audit Dashboard)
* **痛点**：政企/金融客户采购国产数据库要求三权分立、审计日志、MAC 强制标签等合规项，人工逐项核验繁琐昂贵。
* **特性**：
  * 在等保测试下增设 `[📋 一键等保合规测评]`；
  * 自动化跑完五大专项规则，生成《FBase 数据库等保三级安全合规自评报告》（可导出 PDF/HTML），直接用于向测评机构交付。

### 14.3 内置实时压测工作台与 TPS/延迟波动折线图 (Stress Workbench)
* **痛点**：做稳定性长稳或性能压测，命令行参数繁琐，且看不到 TPS 实时毛刺与 P95/P99 延迟跳变。
* **特性**：
  * 「稳定性测试」页面提供内置压测工作台，滑块配置并发客户端数与测试时长；
  * 实时渲染 ECharts 动态折线大屏（实时 TPS + P95/P99 延迟散点图）；
  * 图表上红虚线标出“故障注入点”，清晰展示恢复期间 TPS 跌落与自愈回升曲线。

### 14.4 实时死锁与锁阻塞依赖树检测 (Real-time Lock Blocker Tree)
* **痛点**：高并发压测或长稳测试偶发超时卡死，排查半天发现是事务锁死。
* **特性**：
  * 自动读取 `pg_locks` 与 `pg_stat_activity`，直观绘制锁依赖树：
    `PID 14210 (持有 ExclusiveLock) ── 阻塞 ──> PID 14225 (等待 AccessShareLock)`；
  * 附带 `[⚡ 一键终止源头进程 (Terminate)]`，秒级解开死锁恢复测试流动。

### 14.5 基于 Git Diff 的“智能用例精准筛选” (Test Impact Analysis / Smart Run)
* **痛点**：全量回归 200 条用例耗时，微小修改只需验证关联用例。
* **特性**：
  * 执行 `./cli/run.sh run --smart`（或 `--impact`）；
  * 结合 git diff 自动分析改动依赖图谱，精准挑选受影响用例，10 秒完成核心验证，提速 90%。

### 14.6 集群环境配置“模板蓝图与一键克隆” (Cluster Blueprint & Cloning)
* **痛点**：复杂集群环境搭建繁琐，搭建第二套需反复手动敲参数。
* **特性**：
  * 部署管理支持将当前环境“保存为蓝图 (Blueprint)”；
  * 新建时选择蓝图并输入起始端口，自动推导派生节点端口与目录，一键生成新环境 YAML。

---

## 15. 存储深水区透视、离线涉密交付与正交矩阵测试 (Storage Engine, Offline Packaging & Matrix Testing)

面向绝对隔离交付、底层存储流转与极限配置覆盖，规划以下 5 项实战利器：

### 15.1 纯内网隔离环境“一键打包离线自包含安装包” (Air-Gapped Offline Packager)
* **痛点**：政企/银行涉密客户现场严禁连外网，缺少依赖或动态库时现场排查极其被动。
* **特性**：
  * 支持命令 `./web.sh package-offline`；
  * 自动将前端静态资源、Python wheels 依赖包、已装载产品包与运行脚本打包为 `platform_offline_x86_64.tar.gz`；
  * 目标机解压后敲 `./web.sh` 零外部网络依赖瞬时启动。

### 15.2 WAL 复制流水线“X 光深度透视仪” (WAL Pipeline & Lag Inspector)
* **痛点**：流复制与 MMR 多活同步延迟卡顿时，难以直观摸清主库写入、网络发送、磁盘刷盘与备库重放各阶段耗时。
* **特性**：
  * 在部署监控中呈现端到端流水线：
    `Master (Write LSN) ──[发送延迟]──> Standby1 (Receive LSN) ──[重放延迟]──> Standby1 (Replay LSN)`；
  * 实时透视 Replication Slot 活跃度与磁盘 WAL 膨胀自动清理倒计时。

### 15.3 一键测试数据切片与秒级注入播种 (Data Slicer & Instant Seed)
* **痛点**：复杂用例依赖大体量初始数据，在代码中通过循环 `INSERT` 耗时漫长（跑 15 秒有 14 秒在插数据）。
* **特性**：
  * 引入数据切片机制，将初始化现场导出为轻量级压缩包 `fixture.sql.zst`；
  * 用例启动时声明依赖切片秒级播种，单用例执行耗时从十余秒压缩至半秒内。

### 15.4 数据表膨胀与死元组回收健康雷达 (Table Bloat & Vacuum Radar)
* **痛点**：高频更新产生海量死元组导致表与索引膨胀，长稳测试中途性能腰斩往往源于未提交长事务阻碍 Autovacuum。
* **特性**：
  * 一键扫描各表死元组占比与空间膨胀率（Bloat Ratio）；
  * 自动定位并标红阻碍旧版本回收的长事务（展示 PID 与运行时长），防患于未然。

### 15.5 GUC 参数正交矩阵衍生测试 (Parameter Combinatorial Matrix Fuzzer)
* **痛点**：隐蔽的分布式死锁或优化器 bug 往往只在奇异参数组合下暴露，人工测试难以覆盖死角。
* **特性**：
  * CLI 支持矩阵衍生：
    ```bash
    ./cli/run.sh matrix --guc "enable_nestloop=on,off" --guc "jit=on,off" ha_commands.001
    ```
  * 平台自动生成 $2 \times 2 = 4$ 套配置变体并行跑测，交叉比对判定结果与差异。

---

## 16. 三大核心必做特性规范 (Must-Have Core Capabilities)

根据一线研发与测试的高频刚需，以下三项特性确立为**必须优先落地的核心标配能力**：

### 16.1 新建集群时的“本地端口冲突智能探测与可用端口推荐”
* **场景与痛点**：
  在部署管理点击 `+ 新建环境` 输入端口时，如果填写的端口（如 `15432`）已被本地其他进程或旧数据库实例占用，往往直到点了一键部署后才报错中断，排查费时。
* **详细设计规格**：
  1. **防抖探测机制**：
     * 用户在 `EnvironmentModal` 表单中输入或修改“端口（Port）”时，前端防抖 300ms 触发后端轻量探针接口：
       `GET /api/v1/environments/probe-port?host=127.0.0.1&port=15432`；
     * 后端通过非阻塞 Socket 连接尝试绑定探测该端口占用状态；
  2. **智能状态与推荐回显**：
     * **端口可用**：输入框右侧显示绿色徽标 `🟢 端口空闲可用`；
     * **端口被占用**：表单即时高亮标黄警示：
       `⚠️ 端口 15432 已被占用！推荐相邻可用端口: [15433 点击应用]`；
     * 用户点击 `[点击应用]`，自动填入 `15433`，彻底杜绝配置完成后部署起不来的尴尬。

### 16.2 测试报告（Report）一键打包下载 (.zip)
* **场景与痛点**：
  用例跑挂后，研发需要将案发现场完整提供给同事复现，但手动去各个目录下翻找 `result.json`、`steps.json`、日志极为繁琐，极易漏拷关键证据。
* **详细设计规格**：
  1. **按钮布局**：
     * 在 Web 端单用例报告抽屉（`ReportDrawer` / `ReportViewer`）右上角常驻显著按钮：
       `[📥 打包下载证据包 (.zip)]`；
  2. **打包内容自包含**：
     * 后端提供接口 `GET /api/v1/regression/{environment_id}/{target}/bundle.zip`；
     * 流式归档打包该用例的全部上下文：
       * `result.json`（判决事实、耗时、退出码）；
       * `steps.json`（步骤快照与 2D/3D 拓扑时序数据）；
       * `report.txt`（纯文本断言报告）；
       * `report.html`（自包含离线交互式 HTML 报告）；
       * 关联日志文件夹 `logs/`（`fbasecman.log`、`proxy.log`、`postgresql.log` 截断日志）；
  3. **便携使用**：
     * 浏览器一键拉起下载 `issue_<target>_<timestamp>.zip`，解压后双击 `report.html` 即可离线复现 3D 拓扑回放与现场日志。

### 16.3 多组件日志查看器（带关键词过滤、日志级别筛选与语法色彩高亮）
* **场景与痛点**：
  排查故障时需要查看集群各个组件（`fbasecman` 网关、FBase / PostgreSQL 各主备节点）的运行时日志，纯黑白控制台日志量大且难以快速定位关键字。
* **详细设计规格**：
  1. **多日志源快速切换**：
     * 在部署管理与测试诊断中提供统一的【集群日志工作台】：
     * 顶部分段切换日志源：`[ 🛡️ fbasecman.log ] [ 🐬 mmr1-primary.log ] [ 🐬 mmr2-primary.log ] [ 🐬 standby.log ]`；
  2. **强大筛选与检索工具栏**：
     * **日志级别过滤（Level Filter）**：一键切换 `[全部级别]` / `[FATAL/PANIC/ERROR 错误]` / `[WARNING 警告]` / `[LOG/INFO 信息]`；
     * **全文即时搜索**：支持输入关键词或正则表达式实时过滤匹配行，并在行内高亮关键词；
     * **行数选择**：快速选择查看最近 `500行` / `1000行` / `5000行`；
     * **实时滚动跟踪（Tail -f）**：勾选 `[滚屏锁定]`，新日志产生时自动平滑滚动到底部；
  3. **日志语法色彩高亮系统**：
     * 🔴 **高危级别高亮**：`FATAL`、`PANIC`、`ERROR`、`Exception` 采用亮红背景徽标；
     * 🟡 **警告级别高亮**：`WARN`、`WARNING` 采用金黄背景徽标；
     * 🔵 **系统级别高亮**：`LOG`、`STATEMENT`、`DETAIL`、`HINT` 采用冷蓝标签；
     * 🟢 **时间戳与进程 PID**：统一采用暗绿与淡灰区分显示；
     * 💻 **SQL 语法高亮**：对日志中出现的 `SELECT`、`UPDATE`、`INSERT`、`BEGIN`、`COMMIT`、`ROLLBACK` 等 SQL 关键字进行着色渲染。







