# 公司产品公共管理平台

这是独立于原回归仓库的内部平台。当前已实现单机 Web、SQLite 元数据、产品/环境登记、`pgcluster` 图形化部署入口、数据库 SQL 工作台、fbasecman 用例执行与报告查看、常稳任务入口和 Python License 生成。

## 已确定的方向

- 面向公司内部的公共平台，统一产品、环境、部署、数据库管理、测试、压测、稳定性、License、知识和 AI。
- Python 3.12 后端与工具模块，TypeScript + React 前端，商务简洁清晰的界面。
- 单机起步：SQLite、文件存储、Huey 本机 Worker，无需单独部署数据库服务或 Redis。
- License 仅由 Python 生成并下载，不移植 C 后台服务，不建设申请/审批队列。生成文件已用旧工具的独立校验命令验证兼容。
- 不建设测试 `run_id` 或批次历史；按产品、环境、用例、参数方案保存当前结果。
- 旧仓库用于了解业务、迁移用例和核对格式；新平台不受旧框架实现约束。
- 可以直接使用成熟开源框架和完整子系统，以接入效果和维护成本决定使用深度。

## 设计文档

| 文档 | 内容 |
| --- | --- |
| [总体架构](docs/architecture.md) | 模块、技术选择、资产迁移、SQLite 部署、Python License、AI 与报告 |
| [接口与扩展契约](docs/contracts.md) | 产品插件、目标身份、动作、事件、结果、错误与接口行为 |
| [前端与测试动画](docs/frontend-and-animation.md) | 商务风格、页面布局、真实事件驱动动画、回放与日志联动 |
| [实施路线与需求核对](docs/implementation-plan.md) | 开发顺序、各阶段验收、用户要求的设计覆盖及当前验证范围 |
| [实现进度](docs/progress.md) | 已实现、已验证、未完成和下次接续位置 |
| [当前设计决策](docs/decisions.md) | pgcluster 部署归属、fbasecman 迁移边界、License 与结果证据规则 |

## 启动

需要 Python 3.12、Node.js 20+ 和 `uv`。首次安装和构建：

```bash
cd /home/postgres/fly_dev/product_platform
uv sync --python 3.12 --extra dev
cd frontend && npm ci && npm run build && cd ..
.venv/bin/product-platform check
.venv/bin/product-platform start --host 0.0.0.0 --port 8765
```

本机打开 `http://127.0.0.1:8765`；其他机器使用本机局域网 IP 和 8765 端口。前端构建后由 Python API 服务直接提供，不需要常驻 Node.js 进程。平台元数据位于 `data/platform.sqlite3`，Huey 队列位于 `data/queue.sqlite3`；数据与日志目录已加入 `.gitignore`。

可用 `PRODUCT_PLATFORM_DATA_DIR` 改变平台数据目录，`PRODUCT_PLATFORM_PGCLUSTER_ROOT`、`PRODUCT_PLATFORM_CMAN_REGRESS_ROOT`、`PRODUCT_PLATFORM_FBASE_REGRESS_ROOT` 和 `PRODUCT_PLATFORM_LICENSE_KEYS` 指向已有产品仓库。详情见 [配置说明](docs/usage.md)。

## 当前可用

| 功能 | 操作入口 | 验证范围 |
| --- | --- | --- |
| 产品/环境 | 产品与环境页 | 登记、修改和 SQLite 持久化已测 |
| 图形部署 | 部署管理页 | 从 `pgcluster` 配置生成二维拓扑、节点详情、校验/创建/启停任务；配置与图模型已测，尚未真实部署 |
| fbasecman 回归配置 | 部署管理页“生成方案” | 两组 MMR、14 个节点、端口/PGDATA 隔离；`pgcluster` 校验已测 |
| fbasecman 夹具 | 部署管理页“准备测试夹具” | 代码已接入，尚未在新部署环境执行 |
| 自动化测试 | 自动化测试页 | 用例清单、单例/整套件/失败重跑、结果/报告/日志/拓扑回看；新部署环境的真实用例待跑 |
| 报告导出 | 自动化测试页 | JUnit/HTML 采用现有报告生成器读取当前环境产物，静态测试待补 |
| SQL 工作台 | 数据库管理页 | 单次 SQL 执行、结果展示与超时限制；真实数据库查询待验收 |
| 常稳 | 压测与常稳页 | 调用现有 `stable.sh` 并显示进度/日志；正式时长未验收 |
| License | License 生成页 | Python 生成和下载；独立测试密钥与旧 C 校验器互通已测 |

**部署归属**：新平台不会调用旧框架的 `env setup/start/stop`。`pgcluster` 管理数据库节点；旧 fbasecman 用例作为过渡期测试实现，在平台生成的环境配置和独立产物目录中执行。旧框架需要的角色、表和上下文由独立夹具准备步骤创建。

## 验证

```bash
cd /home/postgres/fly_dev/product_platform
.venv/bin/pytest -q
.venv/bin/ruff check --select F backend tests
cd frontend && npm run build
cd /home/postgres/fly_dev/pgcluster && python3 -m unittest discover -s tests -q
```

当前平台框架测试与配置校验不能替代新隔离环境的真实部署、所有 fbasecman 用例和正式常稳时长验收。每次涉及真实环境的操作都应从 Web 中明确提交，页面读取和方案生成不会启动数据库。
