# 公司产品公共管理平台 (product_platform)

公司产品公共管理平台是统一面向公司各数据库与中间件产品的综合管理与自动化验证中枢。平台深度融合 `fbasecman_regress_v2` 全量自动化测试套件,采用 `pgcluster` 引擎统一实现底层 14 节点双 MMR 高可用集群的拓扑编排与生命周期管理。

**北极星**:一个产品接入零成本(适配器包、核心零修改)、功能覆盖测试到报告全链条、每条结论都有证据支撑的公司级产品平台。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/design.md](docs/design.md) | v3 目标架构:平台公共部署/回归/License 内核、单目录产品包、数据与事件契约、实施路线 |
| [docs/progress.md](docs/progress.md) | 当前代码进度、旧方案历史记录与工作区约束;旧 P1–P8 不再是实施路线 |
| [products/demo/README.md](products/demo/README.md) | 最小产品接入样例:manifest、Provider、SDK 用例和前端注册 |

## 核心特性

1. **多环境图形化编排(pgcluster 引擎)**:自动规划主从端口、数据目录与复制关系;一键创建/启动/停止/重启/状态/清理/体检/恢复集群;自动初始化角色认证、库表视图、MMR 拓扑与 `test_context.yaml`。
2. **自动化测试与多环境绑定**:全局切换"当前产品"与"绑定环境";支持单用例、整套件、失败项快速重跑;导出标准 JUnit XML 与 HTML 报告。
3. **Web 任务链主入口**:`web.sh` Web 控制台默认 8080,后台守护运行;回归目标(单用例/套件/`failed`/`all`)统一经平台 `RegressionEngine` 执行,产物与判定由平台落库。产品 `cli/run.sh` 为平台用例执行的薄壳(等价 `platform_regress.cli`,自动注入环境上下文)。
4. **License 签发**:Python 重写的生成/下载/密钥管理,兼容既有产品格式,无后台申请队列。
5. **AI 失败诊断**:证据溯源式诊断,引用不存在的证据即拒绝;AI 不修改确定性判定。

## 快速开始

```bash
# Web 控制台(默认 http://<IP>:8080)
./web.sh setup      # 首次:初始化虚拟环境并安装依赖
./web.sh build      # 编译前端静态资源产物 (生成 frontend/dist)
./web.sh start      # 启动(可带参数:./web.sh start 9000 0.0.0.0)
./web.sh status | logs | restart | stop

# 回归测试:Web 控制台操作(推荐)或 REST API
#   POST /api/v1/operations  {"environment_id":"cman-mmr","action":"tests.fbasecman",
#                            "target":"all|failed|<suite>|<suite.case>","acknowledge_change":true}
# 任务进程统一调用平台引擎,例如:
.venv/bin/python -m platform_regress.cli \
  --product-dir products/fbasecman --output-dir <输出目录> \
  --context-json '<环境上下文 JSON>' [target|--suite <套件>|failed]

# fbasecman 单用例薄壳(注入环境上下文后调平台引擎)
cd products/fbasecman
./cli/run.sh --environment cman-mmr ha_commands.set_node_write_in_groups_roundtrip
./cli/stable.sh show                           # 常稳命令
```

## 平台使用要点

- **环境登记**:选择产品,填写环境 ID、主机、端口、数据库与用户;部署另填 pgcluster YAML 路径与目标(如 `mmr.fbasecman_regress`)。
- **生成方案**:部署页生成 `data/profiles/<环境>/pgcluster.yaml` + `regress.override.yaml`,只写本地文件并校验;`.pgcluster-managed` 标记是清理与实例管理边界。
- **测试夹具**:部署后"准备测试夹具"创建测试库、角色、多活组与 `test_context.yaml`(会修改数据库,仅在专用测试环境执行);旧代码在隔离进程内运行,平台不导入旧框架 `framework` 包。
- **License**:密钥库默认在 `data/license/keys`(`v1.N` 目录,厂商信息在同级 `config.json`);`PRODUCT_PLATFORM_LICENSE_KEYS`/`PRODUCT_PLATFORM_LICENSE_VENDOR` 可覆盖,迁移期可把旧 `fd_licenser` 密钥目录整体指入或拷入。签发为 Python 实现(Ed25519/Argon2id/XChaCha20),格式与签名由平台自验证,不再调用旧 C 校验器;自动测试只用临时密钥。
- **API 与数据**:接口文档见服务 `/docs`,前缀 `/api/v1`;任务日志在 `data/operations/`,用例报告与证据在 `data/legacy_cman/<环境>/output/runs/`。元数据已全量落 `FileStore` 文件存储,备份即文件级拷贝;旧 `platform.sqlite3` 仅作 `product-platform migrate-sqlite` 的一次性迁移源。

## 目录结构

```text
product_platform/
├── backend/platform_app/          # 平台服务(FastAPI + FileStore 文件存储 + Huey 文件队列)
│   ├── api.py                      #   全部 HTTP API(待按领域拆分)
│   ├── actions.py                  #   任务执行器:环境锁/子进程/取消/事件/结果发布
│   ├── providers.py                #   产品提供者分发(待全部迁入适配器)
│   ├── filestore.py                #   文件元数据存储(环境/任务/事件/结果,原子写 + flock)
│   ├── queue.py + cli.py           #   FileHuey 队列;启动入口(API + consumer + migrate-sqlite)
│   ├── config.py + catalog.py      #   全局配置;产品目录
│   ├── license.py                  #   License 生成与密钥管理(Python 重写)
│   ├── diagnostics.py              #   AI 失败诊断(证据捆绑 + 引用防幻觉校验)
│   ├── database.py + topology.py + scene.py   # SQL 查询;拓扑驱动分发(驱动见 pgcluster_topology.py);场景动画事件
│   └── product_catalog.py          # 产品 manifest 发现与契约校验
├── frontend/src/                   # React 19 + TS + AntD 前端
│   ├── views/                      #   部署/测试(多活·等保·fbasecman)/License 页面
│   ├── components/                 #   TaskDrawer/LogViewer/ThreeTopologyView 等
│   └── product-adapters/fbasecman/ #   产品专属组件(报告/2D 拓扑/回归终端)
├── products/                       # 每个产品一个代码目录
│   ├── fbase-database/             # FBase 适配、CLI、cases.json 声明式用例目录
│   ├── fbasecman/                  # fbasecman 适配、CLI、用例和 regression/
│   └── demo/                       # 无数据库依赖的产品接入样例
├── tests/                          # 平台自身测试(250+ 项 pytest)
├── data/                           # 控制面与本机历史资源（数据库实例不属于平台状态）
│   ├── platform/                   # FileHuey 队列、锁、操作/Web 日志；旧 platform.sqlite3 仅为迁移源
│   ├── environments/               # profile、fixture 上下文、回归证据
│   └── <legacy-pgdata>/            # 迁移期旧实例资源；由 pgcluster 管理，不是平台数据库
├── docs/                           # design.md(设计文档)+ progress.md(进度)
├── web.sh                          # Web 控制台管理(默认 8080)
└── pyproject.toml + uv.lock         # Python 工程(uv 管理)
```

## 开发与构建

```bash
.venv/bin/python -m pytest tests/
(cd frontend && npm run build)
```

全量验证 = 平台测试 + 前端构建 + `git diff --check`。

- 脚本向下兼容探测虚拟环境与 python3.12→3.8;业务夹具用系统 `psql` 管道,避免驱动冲突。
- 前端改动需在 `frontend/` 执行 `npm run build`,产物输出 `frontend/dist/` 由 FastAPI 静态托管。
- 新增依赖按 [设计文档](docs/design.md) 的实施阶段安装，不引入浮动版本。

## 会话工作规则(AI/开发必读)

1. 工作树有大量未提交修改与未跟踪产物;**禁止 `git reset`、批量清理、删除 core/锁文件**;只编辑明确涉及的文件,编辑前先核对当前内容。
2. **目标产品代码归 `products/<product_id>/`**;平台核心不得新增产品分支。旧回归工程已归入对应产品的 `regression/`，不再保留顶层 `regress/` 或平台产品适配器目录；目标协议见 [docs/design.md](docs/design.md)。
3. AI 不修改确定性测试判定;报告解析不从展示文本猜测结论。
4. 回归测试、数据库集群部署和 License 通用能力归平台；迁移期可隔离调用旧资产，但新实现不继续复制旧框架。部署统一数据库集群引擎，不回退旧 `env setup/start/stop/heal`。
5. `http://192.168.0.12:8081` 仅为视觉参考,禁止 iframe 嵌入或依赖其进程;平台本体在 8080。
6. 可能有**并行会话**同时修改本仓库;编辑前重新读文件,以当前内容为准。
7. 每完成一个阶段:更新 `docs/progress.md`,跑全量验证;文档主张必须与代码事实核对。
