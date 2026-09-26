# fbasecman 回归测试框架

本目录提供 fbasecman 的共享测试环境管理、套件化回归用例、标准报告、常稳测试和 Web 控制台。框架修改只在本目录进行；产品源码位于上一级目录。

## 快速开始

```bash
./run.sh doctor
./run.sh env setup --adopt-existing
./run.sh env status
./run.sh show high_availability
./run.sh run high_availability.core_13_monitor_confirm
./run.sh run ha_commands
```

默认运行前执行环境 `heal` 预检。共享环境中如需只告警或跳过自动修复，显式指定：

```bash
./run.sh run <suite-or-case> --preflight warn
./run.sh run <suite-or-case> --preflight off
```

`heal` 可能启动、停止或重建测试数据库节点；执行前确认当前环境允许框架接管。

## 套件

套件由 `suites/<suite>/plugin.py` 注册到统一注册中心。当前套件包括：

| 套件 | 内容 |
| --- | --- |
| `high_availability` | 高可用故障切换与 monitor 探测 |
| `ha_commands` | 高可用控制台命令和持久化 |
| `outstanding` | outstanding 队列与后端 PS 缓存一致性 |
| `global_cache` | 全局 PreparedStatement 缓存 |
| `handover` | 转测文档业务用例，默认跳过 `[LONG-TIME]` 用例 |
| `sql_parse` | SQL_PARSE 扩展协议 |
| `rw_toggle` | 读写切换与读写分离 |
| `guc` | GUC 同步和连接复用 |
| `common` | 通用能力、Locale 和错误统计 |
| `tmp` | 临时或特定缺陷复现 |

套件运行会逐例输出进度和结果；handover 还会输出阶段级进度。新增套件请参阅 [新增套件](docs/adding_suite.md)。

## 环境管理

```bash
./run.sh env status
./run.sh env start
./run.sh env restart
./run.sh env stop
./run.sh env heal
./run.sh env clean --dry-run
./run.sh env clean
```

`env setup` 和非 dry-run 的 `env clean` 会操作远端测试数据目录。执行清理前先用 `--dry-run` 检查目标范围。

## Web 控制台

```bash
./run.sh web --host 0.0.0.0 --port 8080
```

Web 提供套件发现、任务进度、环境状态和报告查看：

```text
tools/web_server.py       HTTP 路由、响应和服务启动
tools/web_environment.py  环境状态、节点和 SQL 服务
tools/web_reports.py      报告与拓扑解析
tools/web_tasks.py        后台任务和运行元数据
```

## 测试产物

每个用例的产物位于 `output/runs/<suite>/<case>/`，常见文件包括：

- `report.txt`：业务验证报告；
- `summary.json`：结构化结果摘要；
- `steps.json`：步骤记录；
- `fbasecman.log` 和 `logs/`：产品及命令日志。

框架单元测试与真实回归用例是两类不同测试：

```bash
./run.sh test
python3 -m unittest discover -s unit_tests
```

单元测试验证框架和适配层；真实产品行为必须通过 `./run.sh run ...` 验证。

## 其他入口

```bash
./stable.sh run <target>
./stable.sh top
./run.sh clean
./run.sh output clean
```

## 文档索引

- [新增套件](docs/adding_suite.md)
- [高可用自动化测试方案](docs/fbasecman第4章高可用自动化测试设计方案.md)
- [转测前核心功能测试方案](docs/fbasecman转测前核心功能测试执行方案.md)
- [框架重构会话交接](docs/框架重构会话交接_2026-09-23.md)

产品缺陷和文档偏差记录在仓库根目录的 `*_ISSUES.md` 文件中，不在 README 中重复维护详细问题单。
