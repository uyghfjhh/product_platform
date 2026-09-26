# fbase_regress_v2

FBase 数据库插件回归测试框架。目前支持等保 `mac` 集群和多活 `mmr` 集群；框架管理测试
环境、执行结构化用例，并为每个用例保留独立报告和证据。

- 查看等保与多活转测文档的迁移进度：见 [PROGRESS.md](PROGRESS.md)。
- 设计约束与架构说明：见 [DESIGN.md](DESIGN.md)。

框架只操作含自身 marker 的 PGDATA。`env clean` 只会删除已验证 marker 的测试目录，外部数据库
目录会被拒绝操作。

## 使用前准备

1. PostgreSQL 与所需插件已完成安装。
2. 准备可用 license 文件；setup 会复制到集群每个节点的数据库目录。
3. 配置 `regress.yaml` 中的 PostgreSQL 安装目录、license 路径、节点端口和 PGDATA。
4. 使用的操作系统用户能够创建配置中指定的 PGDATA，并可启动 PostgreSQL。

隔离 MMR 用例会自动分配本机可用端口，直到对应临时实例退出。无需修改系统端口范围。

默认配置使用当前环境变量 `PGHOME`：

```bash
export PGHOME=/usr/local/fbase15.15
cd /home/postgres/fly_dev/postgresql_for_fbase_dev/fbase_regress_v2

```

## 配置

所有配置位于根目录的 `regress.yaml`，支持环境变量：

```yaml
postgres:
  home: ${PGHOME}
  license_file: /home/postgres/license/license.dat
```

每个 cluster 的主要字段：

- `plugins`：创建的扩展；`preload: true` 的插件加入 `shared_preload_libraries`。
- `postgresql.settings`：每个节点写入的 PostgreSQL 参数。
- `nodes`：节点名、地址、端口和 PGDATA。
- `groups`：`streaming` 物理主备、`logical` 逻辑复制、`mmr` 多活复制关系。

`mac` 默认创建 `fb_license` 和 `fbase_mac`。TDE、TLCP、SSL、GB18030 专项配置位于
`plugins.fbase_mac`；GB18030 全文检索还要求已经安装 `zhparser`。

## 环境管理

```bash
./run.sh doctor --cluster mac
./run.sh env setup mac
./run.sh env status mac
```

```text
./run.sh env list
./run.sh env show <cluster>
./run.sh env setup|start|stop|reload|restart|clean <cluster>
./run.sh env status [<cluster>]
```

`reload` 用于 SIGHUP 生效的参数，预加载插件等启动参数必须使用 `restart`。`settings` fixture 会保存
旧值、恢复旧值，并选择相同的 reload/restart 动作。MMR setup 会验证所有 member 的双向数据同步。

## 查找和运行用例

```bash
./run.sh show
./run.sh show mac.separation_of_duties
./run.sh show longtime
```

```text
./run.sh run <cluster> [<target>] [--all|--longtime] [--enable-run-id]
```

省略 `target` 时，框架按 cluster 启用插件选择兼容 suite：`fbase_mac` 对应 `mac`，`fdd_mmr`
对应 `mmr`；同时启用时执行两者。无已注册 case 的插件会报错，不会静默跳过。

```bash
# 一个 case，唯一叶子名也可简写
./run.sh run mac mac.separation_of_duties.sso_system_privileges
./run.sh run mac sso_system_privileges

# 一个分组，或逗号分隔的多个 case
./run.sh run mac mac.separation_of_duties
./run.sh run mmr mmr.streaming_conflict.update_missing_insert_or_skip,mmr.streaming_conflict.update_missing_insert_or_error

# 默认兼容用例；包括默认跳过的长期用例；保留历史 run
./run.sh run mac
./run.sh run mmr --all
./run.sh run mmr --longtime
./run.sh run mmr --enable-run-id
```

名称带 `[LONG-TIME]` 的用例默认跳过。`./run.sh show longtime` 列出这些用例、文档来源和精确手动
执行命令；`--all` 将其纳入完整回归，`--longtime` 只运行这些用例。显式指定分组或 case 时，该 target
也会执行。

`run` 不隐式执行 setup、restart 或 clean；已 setup 但停止的受管 cluster 或受管节点会先补启动。集群
不存在、拓扑不匹配、插件或专项外部前置条件缺失时，case 为 `BLOCKED`。退出码：`0` 全部成功，`1` 存在
`FAILED` 或 `BLOCKED`，`2` 是参数、target、配置或环境标识错误。

同一 run 中相同 `session.key` 的 case 可共享会话级 fixture。默认回归会将相同 key 合并为连续块，首条前
初始化一次、块结束后回收；显式 target 保持给定顺序。每条 case 仍须声明 reset fixture。

## 报告和证据

默认运行固定覆盖当前结果：

```text
output/<cluster>/<suite>/<group>/<case>/report.txt
```

`--enable-run-id` 才保留历史：

```text
output/runs/<cluster>/<run_id>/<suite>/<group>/<case>/
├── report.txt
├── execution.log
├── postgresql.log
└── postgresql.csv
```

每个 run 根目录另有 `summary.json` 和 CI 可读取的 `junit.xml`；`BLOCKED` 映射为 JUnit skipped，
`FAILED` 映射为 failure。

```bash
./run.sh report
./run.sh output clean       # 不影响 output/envs 或 PGDATA
./run.sh output clean mac
```

`report.txt` 包含文档来源、运行拓扑、有效配置、每一步 SQL/命令、psql 原始输出、预期、判定、失败原因、
新增服务端日志与 core 路径；存在 core 时同时显示可直接执行的 gdb 命令。

## 新增用例

每个 case 是 `suites/<suite>/cases/<group>/<case>.py` 中导出的 `CASE` 字典；框架自动发现所有非私有
case 模块，辅助模块文件名使用 `_` 开头。case 只声明来源、前置条件、fixture、步骤、预期和断言：

```python
from framework.assertions import rows_equal
from framework.steps import sql_step

CASE = {
    "id": "mac.<group>.<case>",
    "document": "转测文档名称.md",
    "section": "章节号",
    "fixtures": ["cluster"],
    "requirements": {"plugins": ["fbase_mac"]},
    "steps": [
        sql_step("检查项", "postgres", "SELECT 1", "返回 1",
                 rows_equal([["1"]])),
    ],
}
```

优先复用 `sql_step`、`command_step`、`wait_sql_step`、公共断言和 fixture。新增用例必须映射到对应转测
文档测试点。

```bash
python3 -m unittest discover -s unit_tests -v
```
