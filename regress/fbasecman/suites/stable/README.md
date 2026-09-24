# Stable 常稳测试

`stable.sh` 将原 `fbasecman_stable_v1` 的 7 条 pgbench workload、JDBC
PreparedStatement workload、后台生命周期、资源采集、诊断、归档和内存图迁入当前
框架。stable 与回归测试分别维护 5 节点 MMR 和 3 节点 REP 拓扑，可并行运行；两者的
PGDATA、数据库端口、fbasecman 监听端口、PID/lock、日志和环境状态文件均不共享。

## 前置和时长

```bash
./stable.sh env setup
./stable.sh env status
```

- 所有 pgbench workload 使用正式时长 40 分钟。
- JDBC `prepared_leak` 使用正式时长 20 分钟。
- 没有短门禁、`[LONG-TIME]` 或重复 workload。
- stable fixture 幂等创建 `table_test`、`test_prepare`、四个登录角色和授权；不会
  改变 MMR/REP 拓扑。

## 配置

每台机器只维护自己的未纳管 `stable.local.yaml`；`stable.sh` 的所有环境、doctor 和
workload 命令都会自动加载该文件，且不会读取 `regress.local.yaml`。固定优先级如下：

1. `stable.yaml`：已纳管的 stable 基准配置。
2. `stable.local.yaml`：未纳管的本机覆盖；模板为 `stable.local.yaml.example`。
3. `--config /path/override.yaml`：临时覆盖，可重复指定。

`stable.yaml` 包含 fbasecman、数据库连接、本地依赖和 workload 参数，以便 stable
独立运行。默认隔离边界如下：

- stable PGDATA：`/home/postgres/fbasecman_stable_v2_mmr` 和
  `/home/postgres/fbasecman_stable_v2_rep`。
- stable 数据库端口：`12011` 至 `12053`；回归保留 `regress.yaml` 中的
  `10011` 至 `10053`。
- stable fbasecman 首选监听端口：`26432`、`27432`；回归使用 `16432`、`17432`。
  首选端口被占用时 stable 自动分配一个空闲端口组，并把实际端口记录到 run state。
- stable 环境状态和日志：`output/stable/env/`；回归环境状态和日志：`output/env/`。

`./stable.sh env clean` 只停止并删除 stable 配置的两个 PGDATA 根目录，绝不会删除
回归的 `/usr/local/pgsql15.3-mmr/test_*` 目录。每个受控根目录都保存 ownership marker，
缺少或不匹配时清理会拒绝执行。首次迁入既有 PGDATA 时，先执行
`./stable.sh env clean --dry-run` 核对计划，再显式使用
`./stable.sh env clean --adopt-existing` 清理残留环境，或使用
`./stable.sh env setup --adopt-existing` 清理后直接重建。
不要将 stable 的 `mmr_data_root`、`rep_data_root` 或数据库端口改成回归的值；
`./stable.sh doctor` 会校验两套配置的隔离。

## 测试流程

先确认 `stable.yaml` 中的 fbasecman 路径、许可证目录和数据库连接正确，随后搭建并
检查 stable 自己的环境：

```bash
./stable.sh env setup
./stable.sh env status
./stable.sh show
./stable.sh render-conf
```

单独执行一条正式 workload，便于先验证某条路由和 SQL：

```bash
./stable.sh run pgbench.mmr_hint_long
./stable.sh run jdbc.prepared_leak
```

执行完整常稳时，8 条业务/Reload pgbench 与 4 条高可用命令 pgbench 会并发运行 40 分钟，JDBC 会并发运行 20 分钟；前台命令
等待全部 workload 完成，并自动生成报告：

```bash
./stable.sh run
```

后台运行、实时观察和收尾操作如下：

```bash
./stable.sh start
./stable.sh top
./stable.sh status
./stable.sh archive
./stable.sh stop
```

`start` 会启动独立 supervisor；它负责等待 workload、采集证据、压缩已关闭 PG 日志、停止
本轮 fbasecman/monitor 并生成报告。`status`、`inspect` 和 `top` 只读 state，不会改变运行
结论。若机器重启或 supervisor 异常退出，`status` 会显示 `supervisor_alive: no`，执行
`./stable.sh recover` 可接管并完成收尾。`top` 用 Ctrl-C 退出，不会停止 workload。中断后台
运行时执行 `stop`，随后按需执行 `diagnose`、`pg-log-check` 和 `report` 保存补充诊断。

短时覆盖配置只用于框架联调，不能代替正式 40/20 分钟结论。例如可将时长通过
`--config /path/smoke.yaml` 覆盖为秒级后运行单条或全量 workload。
仓库提供的真实环境低负载 smoke 覆盖为
`suites/stable/assets/config/stable.smoke.yaml`：

```bash
./stable.sh --config suites/stable/assets/config/stable.smoke.yaml run pgbench.mmr_hint_long
./stable.sh --config suites/stable/assets/config/stable.smoke.yaml run jdbc.prepared_leak
```

## 命令

```bash
./stable.sh env setup [--adopt-existing]
./stable.sh env clean [--dry-run] [--adopt-existing]
./stable.sh env status|start|restart|stop
./stable.sh show
./stable.sh doctor
./stable.sh run [workload]
./stable.sh render-conf
./stable.sh reload
./stable.sh start [all|fbasecman|monitor|pgbench|jdbc|jdbc:prepared_leak]
./stable.sh restart [target]
./stable.sh stop [target]
./stable.sh status
./stable.sh inspect
./stable.sh recover
./stable.sh top [--refresh 2] [--once] [--files] [--text]
./stable.sh tui [--refresh 2]
./stable.sh diagnose
./stable.sh archive
./stable.sh memory-plot [--x-interval 1m]
./stable.sh pg-log-check
./stable.sh report
./stable.sh clean
```

`run` 前台等待 workload 结束并自动生成报告。`start` 后台运行，`reload` 使用当前
run 的端口重渲染配置后仅执行一次 console `RELOAD;`，不会重启 workload。状态可能为
`running/finalizing/completed/failed/degraded/stopping/stopped`；`report` 根据当前 state 生成报告。
`stop` 仅停止 state 中命令指纹与本次 stable 目录匹配的进程，不停止 PostgreSQL。
PostgreSQL 的启停和清理使用 `./stable.sh env start|stop|clean`；这些命令只作用于
stable 的 PGDATA 和 `output/stable/env/`，不调用 `./run.sh env ...`。

`top` 是无依赖的文本仪表盘，展示运行状态、workload PID/状态、资源采样和 fbasecman
日志尾部；`--files` 改为展示 fbasecman 打开的常规文件。`tui` 是可选的 Textual 全屏
界面，展示 RSS、VSZ、CPU、写入量四张实时图，支持 `f` 查看打开文件、`g` 返回图表、
`r` 立即刷新、`q` 退出。安装命令为
`PYTHON_BIN=/path/to/python3.8 ./install_stable_top_deps.sh`；依赖缺失时 `tui` 会报错，
文本信息仍使用 `top`。`pg-log-check` 会先
复制本轮 PG 日志证据，再压缩本轮已关闭的 `.csv`/`.log` 日志到远端 `stable_archive/`；
仍被 PostgreSQL 打开的文件和早于本轮运行的文件不会移动。

`render-conf` 将当前 `stable.yaml`（以及可选覆盖文件）渲染到
`output/stable/rendered/fbasecman.conf`，不创建 run、不启动 fbasecman。完整可加载的
配置覆盖样例在 `suites/stable/assets/config/stable.example.yaml`。

## 启动排障

### console 端口在 30 秒内未就绪

若 `./stable.sh start` 报出以下错误：

```text
fbasecman console not ready within 30.0s
connection to server at "localhost", port 26432 failed: Connection refused
```

不要先按端口冲突或 PostgreSQL 未启动处理。先检查本轮目录
`output/stable/runs/<run-id>/`：若 `logs/fbasecman.log` 已出现
`Fbasecman started successfully` 和 `listening on ...:27432`，却没有 console 端口
`26432` 的监听记录，同时目录中存在 `core.system.*`，则 fbasecman 在创建第二个 listener
时发生了协程栈溢出。

stable 渲染配置必须显式使用：

```conf
coroutine_stack_size 16
```

该参数的单位是内存页：默认值 `4` 在 4KB 页系统上只提供 16KB 协程栈；listener 启动路径中
的 `vfprintf()` 可能耗尽该栈并触发 `SIGSEGV`。`16` 提供 64KB，与
`new-config/fbasecman-all-new.conf` 的已验证配置一致。`--console` 前台启动偶尔正常不代表
该配置可用于 stable 后台运行；必须检查实际渲染出的配置与本轮 core、listener 日志。

## Workload

| workload | 内容 | 时长 |
|---|---|---|
| `pgbench.mmr_hint_long` | MMR hint 长连接读写切换 | 40 分钟 |
| `pgbench.mmr_hint_short` | MMR hint 短连接读写切换 | 40 分钟 |
| `pgbench.mmr_port_read` | MMR port 读端口 | 40 分钟 |
| `pgbench.mmr_port_write` | MMR port 写端口 | 40 分钟 |
| `pgbench.rep_hint` | REP hint 读写切换 | 40 分钟 |
| `pgbench.balance` | 三个 MMR primary balance | 40 分钟 |
| `pgbench.console` | `SHOW NODE_STATUS/THREAD_STATUS/POOLS` 与 `RELOAD` | 40 分钟 |
| `pgbench.reload_status_toggle` | 配置中反复切换 `pg_240` 的 `active/parted` 并 `RELOAD`，同时在长连接中反复切换事务只读/读写属性 | 40 分钟 |
| `pgbench.ha_node_state` | `SET NODE ACTIVE/PARTED pg_240` | 40 分钟 |
| `pgbench.ha_group_route` | `SET NODE WRITE/PROMOTED` | 40 分钟 |
| `pgbench.ha_weight` | `SET NODE WEIGHT` | 40 分钟 |
| `pgbench.ha_cluster` | `SET CLUSTER ACTIVE/PARTED mmr_cluster_2` | 40 分钟 |
| `jdbc.prepared_leak` | PreparedStatement、GUC、heartbeat、读写切换 | 20 分钟 |

pgbench 必须正常退出、事务数大于 0 且失败事务为 0。JDBC 的 failure、读写切换、
heartbeat 和 GUC failure 计数必须全部为 0；任一失败时 Java 返回非零。启动后会先以
短路由预检捕获 fbasecman 的目标 cluster 证据（balance 建立新连接，直到三个 primary
都有日志证据，达到有界诊断上限才失败），
再关闭 debug 日志运行正式负载。pgbench 原始 debug 消息仅保留错误、进度和最终汇总；
运行窗口内的实际 SQL 由 PostgreSQL `log_statement=all` 保存，避免同一 SQL 在两侧重复
记录而造成小时级运行日志膨胀。

`pgbench.reload_status_toggle` 默认每 0.2 秒切换一次 datasource 状态，每次修改后立即
执行 console `RELOAD;`。脚本退出时会恢复该 datasource 的原始状态；参数可通过
`stable.reload_status_toggle` 的 `enabled`、`datasource` 和 `interval_seconds` 覆盖。

## 配置与输出

运行配置只使用新字段：datasource 的 `cluster_name/application_name/status`，group
的 `storage_db/backend_clusters/write_cluster/promoted_cluster`。渲染器禁止旧的
datasource 列表和 `primary_replica_maps`。

```text
output/stable/runtime/state.json
output/stable/env/{env_state.yaml,test_context.yaml,logs/}
output/stable/runs/<run-id>/report.txt
output/stable/runs/<run-id>/workloads/<name>/report.txt
output/stable/runs/<run-id>/{config,logs,monitor,diagnostics}/
```

中断后先执行 `./stable.sh stop`；`diagnose` 检查本轮日志和新增 core，`archive`
归档整个 run。`memory-plot` 从原始 CSV 生成无需额外 Python 依赖的 SVG 图表。

## 代码位置

```text
stable.sh                         stable 命令入口
stable.yaml                       stable 独立配置
suites/stable/manifest.py         8 条 workload 定义
suites/stable/runtime.py          生命周期、断言、PG 日志采集和压缩
suites/stable/assets/             pgbench SQL 与 JDBC driver
tools/stable_cli.py               stable 命令编排
tools/stable_top.py               实时终端仪表盘
tools/stable_top_textual.py       Textual 全屏 TUI
install_stable_top_deps.sh        TUI 可选依赖安装脚本
unit_tests/suites/stable/         stable 单元与脚本行为测试
```
