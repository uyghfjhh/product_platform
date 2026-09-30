# 平台 v2 公共能力与产品边界

本批次把产品中可复用的执行能力接入平台公共接口，同时迁移实际调用方。
不合并产品 runtime，不重写 executor 业务步骤。产品继续拥有配置、专项 SQL、
拓扑选择、协议、日志匹配、业务判定及报告解释。

## 能力与调用方

| 能力 | 平台实现 | 已迁移调用 |
| --- | --- | --- |
| 结果发布 | `platform_app.result_publication.publish_regression_results` | demo、FBase、fbasecman 的 Provider |
| 远程执行与日志增量 | `execution.remote`、`evidence.server_logs`、`evidence.log_window` | FBase ServerLogCollector、fbasecman global_cache；handover 继续使用既有平台 SSH/log-window 原语 |
| PG 时间窗口与归档 | `evidence.postgresql_logs` | fbasecman stable |
| 长跑执行与监督 | `execution.longrun.WorkloadGroup/observe_workloads/supervise/finalize_run` | stable foreground、background supervisor、状态机与终态 |
| 进程归属与监控 | `execution.processes`、`execution.monitoring` | stable 停止、身份核对、resource CSV |
| 临时 PostgreSQL 资源 | `environment.disposable.DisposablePostgresResources` | FBase 的隔离实例根目录、端口分配、遗留监听者回收、死 owner 回收 |
| 数据库公共夹具 | `environment.postgresql_fixtures.PostgresFixtures` | FBase roles/database/table/sequence/table_grants/settings |
| PG 节点动作 | `environment.postgresql_lifecycle.PostgresLifecycle` | FBase pg_ctl/cluster_apply；管理环境的部署仍使用平台 pgcluster Provider |
| 恢复守卫 | `environment.guards`、`FixtureManager.set_setting` | HBA 文件、系统时间/NTP、ALTER SYSTEM 配置恢复 |
| pgbench | `clients.pgbench` 与平台 `clients/assets/run_pgbench.sh` | stable 命令与标准结果指标；HA 重试与 SQL 保留产品侧 |
| 二进制指纹 | `evidence.fingerprint` | fbasecman 部署夹具的本地/远端指纹 |
| 产物保留与报告访问 | `evidence.artifacts`、`platform_app.artifact_progress` | fbasecman CLI 清理、附件/日志/summary 索引、步骤进度与 scene 事件 |
| PG 复制观测 | `platform_app.postgresql_observations` | FBase/fbasecman；产品 `pg_common` 实现已退役 |
| 版本化长跑状态 | `persistence.state.JsonStateStore` | stable StateStore，仅产品字段默认值留在产品 |

## 契约与行为变化

### 结果必须能归因

发布结果只接受属于产品 catalog、匹配本次 operation_id 的事实。
汇总状态依据匹配行，不信任聚合 counts，也不根据任务成功回填 PASS。
缺失/陈旧结果产生 ERROR 并使原成功任务变为 FAILED。`failed` 无需重跑时
允许任务成功，但不制造通过用例。suite/all/failed 与单用例共用实现。

### 日志与证据

日志源、节点和过滤规则由产品提供。公共采集器完成本地/SSH 文件快照、
轮转与截断后的偏移处理、幂等 finish 与证据附件。FBase 原先远程 snapshot
直接跳过的问题已修复，现在远程记录文件大小并抓取增量。

PostgreSQL 归档默认只复制关闭的日志；启用 remove_sources 才删除已归档的
关闭文件。运行中打开的文件通过 lsof 排除；缺少 lsof 拒绝归档。stable
明确提供自己的目录名/前缀，并保留其原先移走关闭日志的策略。

超大日志保留原始证据，生成 `.previews/` 派生文本；不再把原文件原地截断。
因此生成预览本身不回收磁盘空间。完整产物删除仍属于显式 CLI 清理操作。

### 恢复与资源归属

修改 ALTER SYSTEM 前注册恢复，保存 auto.conf 的原值/是否存在；reload
失败仍会恢复，RESET 带完整参数名。

SQL 对象 CREATE 成功后才建立自动删除的归属；CREATE 失败不删除同名既有
对象。`setup=False` 是显式仅清理声明。公共夹具既可使用 context 原生 SQL，
也可注入产品的 psql/evidence transport，以保留业务报告语义。

临时集群回收核对根路径、实际解析路径与 postmaster 的数据目录，不停止或
删除归属不明的资源；确认进程退出后才删除目录。产品只负责二进制定位、
专项初始化以及声明端口/目录，平台不从 product 名称分支选择操作。

报告身份拒绝路径穿越，报告目录拒绝 symlink 逃逸。runtime 业务失败与
teardown 失败分别保留，不再用清理异常覆盖业务原因。

### 长跑任务

平台负责负载启动、进程身份、状态落盘、完成观察、finalization claim 与清理。
产品负责健康探针、负载结果判定与专项报告。

版本化 state store 深复制 defaults，避免实例之间共享 workloads 字典。
状态修改持文件锁。终态发布在报告写入尝试之后；报告失败保留失败事实，
finalizer 不复活 stopping/stopped 的任务，不覆盖新 run_id。

独立清理动作全部尝试，某个停止失败不会阻止其他资源回收。

## 验证边界

`tests/test_platform_v2_capabilities.py` 覆盖结果归因、日志轮转/远程增量、
数据库对象归属、reload 失败恢复、symlink 边界、长跑终态、默认值隔离、
负载登记失败回收、资源采样、日志预览保留、指纹，以及实际 shell/tar 的
关闭日志归档测试。平台已有测试继续运行。最终全量 321 项通过，新增能力测试 30 项；前端构建与 diff 检查通过。

本轮没有启动真实数据库回归、长期压测或对实际远程主机执行抓取/归档；
契约与本地测试通过不等于这些环境中的保真验收完成。


## Review 故障修复与快速验证

- 清理使用规范化父目录校验，拒绝 `..` 或父 symlink 越界，允许删除链接本身而不触碰目标。
- 长跑 cleanup 抛异常、返回未回收资源或 errors 时，终态为 failed，并保留 cleanup_error。
- stdin 与 stdout/stderr 同时读写，写入期间也检查超时/取消；后台输入通过独立通信任务处理，finish/cleanup 回收进程。
- 发布合并聚合结果和当前 per-case facts，旧/部分聚合不能屏蔽本次结果。
- 本地日志保留原文件句柄与 inode；同名替换后合并旧尾部和新文件。远端按 device/inode 查找旧文件，找不到时报告采集错误。
- lsof 不存在、超时或权限错误都不能当作空端口；仅明确的空探测结果可视为已释放。
- task meta.json 原子提交状态与 pending_events，JSONL 是可恢复投影；API 合并待投影事实，启动/后续事件幂等重放，修复半条追加记录。

FBase 各用例族共用同次模块加载的声明快照，避免重复解析 5 MB 的 catalog；每次模块加载仍读取当前文件。
测试 fixture 共用声明加载，但深复制运行用例以隔离可变状态。密码学参数、业务断言与超时前提未降低。

`cli/check.sh quick` 运行针对性子集，`cli/check.sh full` 保留全量验收。


Review 修复后的最终验收：348 项全量通过，27 项新增失败路径测试；quick
针对性子集 83 项，实测 5.34 秒，全量 28.58 秒（改动前 321 项/35.61 秒）。
排队取消和恢复终态同样记录原子事件；SSE 读取顺序避免结束竞态漏通知。
前端构建与静态检查通过。运行中的平台进程需要重启后加载新代码。

### 公共报告查看

测试页默认使用平台 ReportViewer；产品可以提供专属组件（fbasecman 保留拓扑报告）。`GET /api/v1/environments/{id}/results/{target}/report` 返回最新归档的结论、耗时、清理信息、步骤及原始报告，结论取自 result.json；`report.txt` 子入口下载原文。环境级 `/reports/html` 和 `/reports/junit` 从同一批最新归档导出，支持单用例及套件批跑目录。缺少步骤时展示归档结论，损坏步骤提供提示；没有归档或读取路径越界返回 404。
