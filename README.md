# 公司产品公共管理平台 (product_platform)

公司产品公共管理平台是统一面向公司各数据库与中间件产品的综合管理与自动化验证中枢。平台已深度融合 `fbasecman_regress_v2` 全量自动化测试套件与执行能力，并采用 `pgcluster` 引擎统一实现底层 14 节点双 MMR 高可用集群的拓扑编排与生命周期管理。

---

## 🌟 核心特性

1. **多环境图形化编排 (pgcluster 引擎驱动)**
   - 全面替代旧式写死端口与硬编码脚本的部署方式。
   - 自动规划主从端口、数据目录与复制关系，支持在平台中维护与部署多个互不干扰的测试/验证环境。
   - 一键执行集群创建 (`create`)、启动 (`start`)、停止 (`stop`)、重启 (`restart`)、状态探测 (`status`) 与清理 (`clean`)。
   - 自动通过 `fbasecman_fixture` 模块初始化角色认证方式、库表视图、MMR 拓扑关系与测试上下文 (`test_context.yaml`)。

2. **自动化测试与多环境绑定**
   - 页面顶部支持全局切换“当前产品”与“绑定环境”，自动化测试任务与选定环境动态挂钩。
   - 支持单用例执行、整套件执行、失败项快速重跑 (`failed`)。
   - 支持导出标准 JUnit XML 与 HTML 交互式测试报告。

3. **双轨使用模式 (CLI + Web)**
   - **CLI 命令行 (`./run.sh`)**：完整保留所有原有回归测试命令，适合自动化流水线或终端工程师直接调用。
   - **Web 控制台 (`./web.sh`)**：开箱即用的 Web 服务管理脚本，后台守护运行，**默认端口 8080**，无需常驻终端窗口。

4. **商务清晰简洁风 (Clean Business Style) 界面**
   - 整体界面采用清爽商务绿与高对比度浅色设计（`#24816c` 主色调，白底卡片，微阴影），视觉层次明朗自然。
   - 页面顶部提供指标统计胶囊（全部 / 通过 / 失败 / 未执行 / 通过率），支持一键点击筛选。
   - 测试套件采用折叠卡片（Accordion）分块展示，各套件标明通过/失败状态标签，支持单组一键执行。
   - 侧滑抽屉（Drawer）实时展示执行终端控制台与历史测试报告，无需跳转页面。

---

## 📁 架构与目录结构

```text
product_platform/
├── backend/                   # 平台核心后端服务
│   └── platform_app/          # FastAPI 路由、SQLite 存储、任务调度、pgcluster 拓扑编排
│       ├── fbasecman_profile.py   # 回归拓扑映射与 pgcluster.yaml 配置生成
│       ├── fbasecman_fixture.py   # 业务夹具与 test_context.yaml 上下文初始化
│       ├── config.py          # 平台全局路径与产品环境参数
│       └── cli.py             # 后端主程序入口 (默认监听 8080)
├── frontend/                  # 平台前端应用 (React 18 + TypeScript + Ant Design)
│   ├── src/                   # 商务清晰简洁风页面与组件 (指标药丸、抽屉、折叠卡片)
│   └── dist/                  # 生产静态资源包 (由 FastAPI 静态托管)
├── products/                  # 平台管理的产品清单与适配器 (fbasecman, fbase-database)
├── regress/                   # 自动化测试与回归体系 (各产品测试工程完全模块化隔离)
│   └── fbasecman/             # fbasecman 产品回归测试全套套件 (原 fbasecman_regress_v2)
│       ├── suites/            # 10 个测试套件 (rw_toggle, global_cache, handover...)
│       ├── framework/         # 回归测试执行引擎与断言器
│       ├── env/               # 拓扑与健康检查工具
│       ├── lib/ & lib_jdbc/   # 依赖库与 JDBC Jar 包
│       ├── tools/             # 测试 CLI 与辅助工具 (cli.py, doctor.py, clean.py...)
│       ├── unit_tests/        # 单元测试 (269 项完整用例)
│       ├── tests/             # rw_toggle 测试工件与脚本
│       ├── output/            # 测试执行产物与报告
│       ├── docs/              # 转测设计方案与历史问题记录
│       ├── regress.yaml       # 回归测试主配置文件
│       └── stable.yaml        # 常稳测试配置文件
├── data/                      # 平台本地持久化数据 (platform.sqlite3, web.log, 部署方案)
├── docs/                      # 平台总体架构与接口设计文档
├── tests/                     # 平台自身服务接口与 License 单元测试
├── run.sh                     # 平台统一 CLI 执行入口 (自动调度至各产品测试工具)
├── web.sh                     # 平台 Web 控制台后台守护管理脚本 (start/stop/restart/setup)
├── requirements.txt           # 平台 Python 核心依赖清单
└── pyproject.toml             # 项目工程描述
```

---

## 🚀 快速上手

### 1. Web 控制台管理 (`./web.sh`)

平台 Web 服务通过独立的 `./web.sh` 脚本进行管理，默认端口为 **8080**：

```bash
# 首次运行: 一键初始化专属虚拟环境并安装平台依赖 (可选，若当前环境缺少 uvicorn/fastapi)
./web.sh setup

# 启动后台 Web 服务 (默认监听 0.0.0.0:8080)
./web.sh start

# 指定端口与 IP 启动 (例如使用 9000 端口)
./web.sh start 9000 0.0.0.0

# 查看服务运行状态与访问 URL
./web.sh status

# 实时跟踪服务运行日志
./web.sh logs

# 重启 Web 服务
./web.sh restart

# 停止 Web 服务
./web.sh stop
```

启动成功后，在浏览器访问：`http://<服务器IP>:8080` 即可进入管理控制台。

### 2. fbasecman 回归测试命令行 (`regress/fbasecman/run.sh`)

回归测试体系已完全模块化封装于 `regress/fbasecman/` 目录下。可通过该目录下的 `run.sh` 执行所有回归测试与环境命令：

```bash
cd regress/fbasecman

# 检查运行环境与工具链依赖
./run.sh doctor

# 查看所有注册的测试套件与用例清单
./run.sh show

# 基于 pgcluster 自动部署 14 节点集群并初始化测试夹具
./run.sh env setup

# 查看集群当前各节点拓扑与健康状态
./run.sh env status

# 启停或重启集群
./run.sh env start
./run.sh env stop
./run.sh env restart

# 运行指定测试套件或单个用例
./run.sh run rw_toggle
./run.sh run rw_toggle.mmr_hint_switch

# 仅重新运行上次执行失败的用例
./run.sh run failed

# 执行 269 项框架单元测试
./run.sh test

# 清理测试产物文件
./run.sh clean --output
```

> 提示：在平台根目录下，也可直接通过相对路径调用：`./regress/fbasecman/run.sh <命令>`。


---

## 💡 开发与构建注意事项

1. **Python 环境兼容性**
   - 脚本 `run.sh` 与 `web.sh` 具备向下兼容探测机制，优先使用虚拟环境，其次依次检测 `python3.12`、`python3.11`、`python3.10`、`python3.9`、`python3.8`。
   - 核心数据库交互（如业务夹具 `fbasecman_fixture.py`）采用系统原生 `psql` 管道驱动，避免外部数据库驱动包的系统库冲突。

2. **前端二次开发与构建**
   - 前端代码位于 `frontend/` 目录。
   - 修改前端代码后，需在 `frontend/` 目录下执行 `npm run build`，编译产物输出至 `frontend/dist/`，由 FastAPI 静态托管。
