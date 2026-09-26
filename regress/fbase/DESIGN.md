# FBase 统一回归测试平台设计

## 1. 目标与边界

`fbase_regress_v2` 是 PostgreSQL FBase 的统一回归测试平台。`fbase_mac`（等保）和
`fdd_mmr`（多活）均按数据库插件接入，不作为独立产品。测试环境的主体始终是 PostgreSQL
集群；集群按需启用一个或多个插件能力。

平台负责：

- 管理自身创建的单机、流复制、逻辑复制和多活 PostgreSQL 测试环境，节点可跨主机；
- 执行结构化测试步骤，生成可交付的逐用例报告；
- 参考已有 `fbase_mac/src/test/regress` 的业务场景并迁移为结构化 case；旧目录保持不变，
  新框架不调用 `pg_regress`；
- 检查 TDE、TLCP、GB18030、license、主备等前置条件。

平台不负责：

- 编译或 `make install` PostgreSQL、fbase_mac、fdd_mmr 或其他扩展；
- 安装操作系统依赖、locale、OpenSSL、zhparser、scws；
- 操作非平台创建的 PGDATA、端口或实例。

## 2. 目录与依赖

```text
fbase_regress_v2/
├── regress.yaml                 # 唯一配置文件
├── run.sh                       # 唯一命令入口
├── requirements.txt             # PyYAML 3.12+；不依赖 pytest
├── framework/
│   ├── models.py                # Execution/Step/Case 统一结果模型
│   ├── context.py               # 用例上下文、访问节点与逆序清理栈
│   ├── runner.py                # 用例生命周期，不包含业务 SQL
│   ├── steps.py                 # SQL、命令、等待和集群动作步骤
│   ├── assertions.py            # 可注册的结构化断言
│   ├── fixtures.py              # cluster/database/roles/settings/topology
│   ├── postgres.py              # 唯一 psql 入口和结构化结果解析
│   ├── transport.py             # 本机/SSH 命令执行边界
│   ├── environment.py           # 受管环境生命周期
│   ├── topology.py              # 流复制、逻辑复制、MMR 节点语义
│   ├── replication.py           # 三类复制关系搭建与 MMR 基线验证
│   ├── health.py                # 节点、复制延迟和 MMR UDF 健康检查
│   ├── evidence.py              # core 证据
│   ├── server_logs.py           # 多节点 PostgreSQL 日志窗口
│   └── reporting.py             # report.txt 渲染
├── templates/                   # cluster 引用的 PostgreSQL/HBA 配置模板
├── suites/
│   ├── mac/
│   │   ├── suite.py              # suite、分组与 case 注册
│   │   └── cases/                # 每个结构化用例一个独立文件
│   └── mmr/                      # 多活 suite，沿用相同 case/report 模型
├── unit_tests/
└── output/                       # 未纳管运行产物
```

依赖只能从 `suites -> framework` 流动。环境层根据 cluster 组合 PostgreSQL、插件和关系组，
不依赖具体 suite；suite 不得直接调用 `subprocess`、`psql`、`pg_ctl` 或 SSH。

运行时兼容 CentOS 8 自带的 Python 3.6 和 PyYAML 3.12，单元测试使用 `unittest`。实现不得依赖
更高版本 Python API；用户无需为运行框架额外创建虚拟环境。

## 3. 配置

平台只读取受版本管理的 `regress.yaml`，不支持 `regress.local.yaml`、`--config`
或自定义输出目录。默认文件按本机运行给出具体值，通常直接修改 YAML 即可；也可把任意字符串
改为 `${NAME}` 或 `${NAME:-default}`，分别表示读取环境变量和“环境变量为空时使用默认值”。
`${NAME}` 未设置时替换为空。YAML 必填字段为空属于配置错误；仅特定用例需要的外部材料缺失
时，对应用例为 `BLOCKED`。

```yaml
postgres:
  # 默认使用当前环境的 PGHOME；也可以直接填写绝对路径。
  home: ${PGHOME}
  # setup 会把此文件复制到每个节点的 PGDATA/license.dat。
  license_file: /home/postgres/license/license.dat

clusters:
  mac:
    # key 存在即在全部节点启用；setup 检查安装文件并生成配置，不负责安装软件。
    plugins:
      fb_license:
        preload: false
      fbase_mac:
        # 返回 TDE 密钥的可执行命令；不用 TDE 时可留空，也可写 ${FBASE_TDE_KEY_COMMAND}。
        tde_key_command: /home/postgres/test.sh
        tlcp_cert_dir: /opt/tlcpcert
        ssl_cert_dir: /opt/sslcert
        gb18030_locale: zh_CN.gb18030
        # 默认测试全部能力；仅需排除时再写 features: { tlcp: false }。

    postgresql:
      # 可省略，分别默认使用以下模板。
      config_template: templates/postgresql.conf
      hba_template: templates/pg_hba.conf
      # 等保转测文档的集群基线；具体用例需要其他值时临时覆盖并在 teardown 恢复。
      # shared_preload_libraries、TDE 和复制参数由插件/关系组自动加入。
      settings:
        max_prepared_transactions: 2
        password_encryption: sm3
        log_statement: all
        log_min_messages: info
        fdb.enable_audit: 1
        fdb.enable_mac: "on"
        fdb.separate_user: "on"
        fdb.password_mode: level
        fdb.password_rule: 4
        fdb.password_change_interval: 15
        fdb.failed_user_auth_times: 5
        fdb.account_auto_thaw_time: 30

    nodes:
      # 每个节点只需 host、port、data_dir；需要跨机器时直接改值或写环境变量。
      # 单节点需要覆盖 PG 参数时，将该节点展开并增加 postgresql.settings。
      mac_primary: { host: 127.0.0.1, port: 15432, data_dir: /home/postgres/pgdata/mac1 }
      mac_standby: { host: 127.0.0.1, port: 15433, data_dir: /home/postgres/pgdata/mac2 }
      logical_subscriber: { host: 127.0.0.1, port: 15434, data_dir: /home/postgres/pgdata/mac3 }

    groups:
      # 只支持 streaming、logical、mmr；值引用上面的节点名。
      streaming:
        # 一个 primary，零到多个物理 standby。
        primary: mac_primary
        standbys: [mac_standby]
      logical:
        # 一个 publisher、一个 publication、一个到多个 subscriber。
        database: postgres
        publisher: mac_primary
        publication_name: fbase_regress_pub
        # key 是 nodes 中的订阅节点名；每个订阅使用独立 subscription 和 slot。
        subscribers:
          logical_subscriber: { subscription_name: fbase_regress_sub, slot_name: fbase_regress_slot }

  mmr:
    # 多活组存在时，fdd_mmr 必须启用；否则 doctor 返回配置错误。
    plugins:
      fb_license:
        preload: false
      fdd_mmr:
        # 在这些数据库中创建并运行 fdd_mmr；值是数据库名，不是节点名。
        enabled_databases: [postgres]
        # fdd.create_node 的集群默认参数；case 可覆盖以测试其他组合。
        node_options:
          failover: true
          streaming: parallel       # off、on、parallel
          two_phase: true
        # fdd.join_group 的集群默认参数。
        join_options:
          wait_for_completion: true
          synchronize_structure: all # none、all、schema-only、data-only
          precheck: table_exist_error # ignore、table_exist_error

    postgresql:
      config_template: templates/postgresql.conf
      hba_template: templates/pg_hba.conf
      # 多活转测文档的建议值；必要参数和随拓扑变化的下限由框架校验。
      # 框架自动加入 wal_level、preload、track_commit_timestamp 和 fdd.running_databases。
      settings:
        log_min_messages: log
        log_rotation_size: 100MB
        log_destination: stderr,csvlog
        log_statement: all
        logging_collector: "on"
        logical_decoding_work_mem: 64MB
        max_logical_replication_workers: 4
        max_sync_workers_per_subscription: 2
        max_replication_slots: 10
        max_wal_senders: 12
        max_worker_processes: 12
        max_prepared_transactions: 200
        fdd.log_conflicts_to_table: true
        fdd.search_dead_tup_time_interval: 30000

    nodes:
      mmr1_primary: { host: 127.0.0.1, port: 10011, data_dir: /home/postgres/pgdata/mmr1 }
      mmr1_standby: { host: 127.0.0.1, port: 10012, data_dir: /home/postgres/pgdata/mmr1_standby }
      mmr2_primary: { host: 127.0.0.1, port: 10021, data_dir: /home/postgres/pgdata/mmr2 }
      mmr2_standby: { host: 127.0.0.1, port: 10022, data_dir: /home/postgres/pgdata/mmr2_standby }
      mmr3_primary: { host: 127.0.0.1, port: 10031, data_dir: /home/postgres/pgdata/mmr3 }
      mmr3_standby: { host: 127.0.0.1, port: 10032, data_dir: /home/postgres/pgdata/mmr3_standby }

    groups:
      mmr:
        group_name: fbase_regress_mmr
        members:
          # key 是传给 fdd.create_node 的节点名；至少两个 member，每个 member 一主多备。
          mmr1: { primary: mmr1_primary, standbys: [mmr1_standby] }
          mmr2: { primary: mmr2_primary, standbys: [mmr2_standby] }
          mmr3: { primary: mmr3_primary, standbys: [mmr3_standby] }
```

约束：

- 插件 key 存在即在 cluster 全部节点启用；框架检查插件已安装，但不执行 `make install`。
- `plugins` 中的扩展会在该 cluster 全部节点创建；`preload: false` 表示不加入
  `shared_preload_libraries`，物理备库从主库继承扩展对象。
- `groups` 只允许 `streaming`、`logical`、`mmr`，引用的节点必须存在且角色兼容；`mmr`
  至少两个 member，并要求启用 `fdd_mmr`。
- publication、subscription、slot、MMR group 和 member 名必须是不超过 63 字节的
  PostgreSQL 标识符，并在各自作用域内唯一；框架不静默截断。
- 框架根据插件、组和成员数生成必需的 PostgreSQL 参数；用户覆盖不得低于必需值，最终值由
  `env show <cluster>` 展示。
- 同一主机端口不得重复，`data_dir` 必须为唯一绝对路径；没有平台 marker 的已有目录禁止操作。
- 节点配置文件默认是 `<data_dir>/postgresql.conf` 和 `<data_dir>/pg_hba.conf`，且不得指向
  `data_dir` 外部。
YAML 结构、节点引用或环境 marker 错误返回退出码 `2`；密钥、证书、locale 等仅某类用例
需要的外部材料缺失时，对应用例为 `BLOCKED`。

### 3.1 集群与用例选择

cluster 是可被 setup/start/stop/reload/restart/clean 的完整测试环境。框架根据插件、组和节点角色判断
case 能否运行。

| 用例 | 集群要求 |
| --- | --- |
| 等保、TDE、TLCP、GB18030 | `plugins.fbase_mac` |
| 故障转移槽 | `groups.streaming` 一主至少一备 |
| 逻辑复制 | `groups.logical` |
| 多活 | `plugins.fdd_mmr` 且 `groups.mmr` 至少两个 member |

`doctor --cluster <cluster> [<target>]` 必须把每项检查结果、实际采用的默认值和受影响的 case
清楚列出。例如：

```text
PASS    | fbase_mac.tlcp.cert_dir | /opt/tlcpcert | 可写
BLOCKED | fbase_mac.tde.key       | /home/postgres/test.sh 不存在或不可执行
        | 受影响用例: mac.tde.*
```

### 3.2 数据库目录、license 与 PostgreSQL 配置文件管理

`env setup` 先校验所有节点路径、端口、SSH、模板和 license，再执行：

1. 对非物理备库节点执行 `initdb`，写入 marker 和节点配置，将 license 复制为
   `<data_dir>/license.dat`，然后启动。
2. 在可写节点初始化已启用插件；框架只验证已安装的 control/SQL/so 文件并执行必要的
   `CREATE EXTENSION`，不执行 `make install`。
3. 物理备库由对应 primary 执行 `pg_basebackup -R` 创建，不再执行 `initdb`；随后写入该
   节点的配置、marker 和 license。扩展目录对象由 basebackup/WAL 继承，不在 recovery 节点执行 DDL。
4. 建立 logical/mmr 关系并验证全部节点状态，成功后记录环境状态。

setup 失败时尽量清理本次创建的内容，无法恢复则将环境标记为 `partial`。已有 data_dir 一律
不由 setup 覆盖：已存在受管环境应使用 start/restart，需重建时先 clean；无 marker 的目录
始终拒绝操作。

setup 后，host、port、data_dir、postgres_home、plugins、groups、TDE 初始化参数和 initdb
参数视为环境不可变属性，修改后必须 `env clean/setup`。reload/restart 只接受 PostgreSQL/HBA
配置变化；检测到不可变属性漂移时拒绝操作并列出差异。

任一节点失败时不得把环境标记为可运行。数据库启动和 restart 始终使用各自 PGDATA 中的
`license.dat`。配置只写入以下受控区块：

```conf
# BEGIN fbase_regress_v2 managed settings
port = <node.port>
listen_addresses = '<框架根据 cluster 节点地址计算>'
shared_preload_libraries = '<框架合并插件和用户配置后的最终值>'
# 其余参数来自“cluster 模板 → cluster settings → 节点 → case 临时覆盖”
# END fbase_regress_v2 managed settings
```

```conf
# BEGIN fbase_regress_v2 managed hba
# 本机 cluster 只允许本机；跨主机 cluster 按节点地址/CIDR生成，不默认开放 0.0.0.0/0
host all all <test_client_cidr> trust
host replication fbase_regress_replication <peer_node_cidr> trust
# END fbase_regress_v2 managed hba
```

`env clean` 仅删除含 marker 的 data_dir，并同时删除对应的 `license.dat`、配置受控区块和
平台创建的日志目录。没有 marker 的目录，即使其路径与配置相同，也必须拒绝清理。

### 3.3 PostgreSQL 配置文件的分层生成

每个节点从模板渲染配置，后层覆盖前层：

1. `clusters.<cluster>.postgresql.config_template` / `hba_template` 模板；
2. 固定 group 推导的最低参数（WAL、sender、slot、worker 等）；
3. 已启用插件适配器提供的必要参数（preload、commit timestamp 等）；
4. `clusters.<cluster>.postgresql.settings` 用户覆盖；
5. `nodes.<node>.postgresql.settings` 节点覆盖；
6. case 声明的临时参数覆盖。临时覆盖必须在 case teardown 中恢复。

用户覆盖不得降低插件或 group 的最低值；`doctor` 显示用户值、最低值和推导来源。
`shared_preload_libraries` 按列表合并去重。节点派生的端口、地址、data directory、HBA 和日志
路径不能被 case 覆盖。所有最终值及来源由 `env show` 展示，并在启动后用 `SHOW` 验证。

`env reload <cluster>` 重新生成并检查所有节点配置，然后执行 reload 并验证结果。失败时报告
具体节点和参数；需要 restart 的参数只提示用户执行 `env restart <cluster>`，不隐式重启。

case 临时修改配置时必须保存旧值，并在 teardown 中恢复。必须重启才能生效的参数由 case
显式执行 restart；恢复失败时环境标记为 `partial`，后续 run 被拒绝。

TDE 比较特殊：setup 时若密钥命令有效，则按 TDE 参数初始化各 seed primary；物理备库继承其
数据。密钥命令缺失时创建普通实例并记录 `tde_ready=false`，只有 TDE case 为 `BLOCKED`。
创建后才补充密钥命令时，
`doctor` 必须提示需要重新 `env clean/setup`，不能在原 PGDATA 上假装已经具备 TDE 初始化条件。

流复制默认异步；`primary_conninfo` 使用真实 host/port，`application_name` 使用稳定节点名。
只有 primary 的 `pg_stat_replication` 与全部 standby recovery 状态正确时 setup 才成功。

MMR 自动启用 `wal_level=logical`、`track_commit_timestamp=on` 和 `fdd_mmr` 预加载，并根据
member、数据库和物理备库数量推导 slot、sender、logical worker 和 worker process 下限。
setup 在 `enabled_databases` 中逐库创建扩展，只有各 member 可写、成员视图一致且复制收敛时
才成功。HBA 仅允许 cluster 节点和测试客户端，不默认开放 `0.0.0.0/0`。

`enabled_databases` 生成 `fdd.running_databases`；member key 作为 `fdd.create_node` 的
`node_name`，`group_name` 传给 `fdd.create_group/join_group`，`node_options` 和
`join_options` 分别提供其余参数。这些是多活建组参数，不属于 PostgreSQL GUC。
streaming/two-phase 专项 case 通过 `fdd.alter_node_info` 修改模式，结束后恢复 YAML 定义的
默认值。创建、加入、离组、删组等破坏性 case 结束后恢复基线多活组。

MMR member 主库故障切换时，框架提升对应备库、更新运行期角色和 MMR 节点连接信息，然后
验证复制状态和数据收敛。故障转移槽 case 在 promote 前必须等待备库和复制槽同步。

### 3.5 Streaming 冲突用例组织

Streaming 冲突用例统一由 `./run.sh run mmr` 发现和调度。需要 `two_phase` 的场景由 case
创建并清理 `/tmp/fbase_regress_*` 临时拓扑；非 2PC 场景使用共享 `mmr` 的可恢复模式切换。
两类事务模型不得共用执行层或以参数切换掩盖差异。

每个文档策略保留独立 case、章节映射、显式 SQL 和断言，以保证可单独运行、报告和追溯。
只有已验证完全相同的临时拓扑和文档前置才可复用。标准同构表、列缺失、目标表缺失、三节点
多唯一键及双端并发等前态必须分别梳理，不能仅以 resolver 参数生成 case。

`fdd.replication_set_add_table` 等会通过独立连接读取关系元数据的 UDF，前置 DDL 必须先提交；
因此 `CREATE TABLE` 与后续 UDF 调用必须使用独立 `psql` 命令，不能合并到同一 `psql -c`。

## 4. 命令行契约

```text
./run.sh doctor --cluster <cluster> [<target>]

./run.sh env list
./run.sh env show <cluster>
./run.sh env setup <cluster>
./run.sh env start <cluster>
./run.sh env stop <cluster>
./run.sh env reload <cluster>
./run.sh env restart <cluster>
./run.sh env status [<cluster>]
./run.sh env clean <cluster>

./run.sh show [<target>]
./run.sh run <cluster> [<target>]
./run.sh report
```

### 4.1 target

target 是语义化路径，不使用数字编号：

```text
mac
mac.audit
mac.audit.statement_rule_management
mac.failover_slot.switchover_continuity
```

路径前缀表示选择其下全部 case。省略 `target` 时，`run` 根据 cluster 启用的插件执行全部兼容
suite；例如 `fbase_mac` 对应 `mac`，`fdd_mmr` 对应 `mmr`，同时启用时依次执行两者。运行单个
case 时允许使用唯一的 case 短名称或部分名称，例如
`run mac sso_system_privileges`。匹配多个 case 时返回退出码 `2` 并要求使用完整名称；
未知 target 同样返回退出码 `2`。

### 4.2 环境命令

| 命令 | 行为 |
| --- | --- |
| `doctor --cluster mac <target>` | 按 cluster 和 target 检查插件、关系组、模板、端口、license、PG 参数及外部前置条件；只读。 |
| `env list` | 列出配置中的 clusters 及已经 setup 的实例。 |
| `env show <cluster>` | 只读展示插件、三类关系组、节点、host、PGDATA、端口、配置文件、SSH 参数和当前角色。 |
| `env setup <cluster>` | 初始化 cluster 全部节点，验证并创建已启用扩展，建立关系组。 |
| `env start/stop/restart <cluster>` | 仅操作指定 cluster 且带合法 marker 的全部节点。 |
| `env reload <cluster>` | 重新渲染并加载全部运行节点的 PostgreSQL 配置，验证实际值；不隐式 restart。 |
| `env status [cluster]` | 统一显示关系组健康和节点详情；无参数时显示全部 cluster。健康视图不展示内部 env_id/state。 |
| `env clean <cluster>` | 仅停止并清理指定 cluster、指定 env_id 的受管节点。 |
| `show [target]` | 无 target 显示 suite/group/case 树；有 target 显示来源、前置条件、拓扑要求和子用例。 |
| `report` | 扫描历史 run 的 summary.json，显示每个 case 的结论和 report.txt 路径。 |
| `output clean [cluster]` | 清理历史测试产物；不操作 output/envs、marker 或 PGDATA。 |

`doctor` 不带 target 时检查整个 cluster，带 target 时再检查该 target 的外部前置条件。
`run` 不隐式 setup、restart 或 clean。已 setup 但停止的受管 cluster，或任一受管节点进程未启动
时，runner 在执行 case 前调用 `start` 补启动；健康检查、拓扑检查和其他前置条件仍按原规则执行。
未知 cluster 返回 `2`；已配置的环境未创建、处于 `partial` 或拓扑不满足时，每条所选 case 生成一份 `BLOCKED` 报告并返回
`1`；配置错误、marker/env_id 不一致等保护性错误在执行前返回 `2`，不得接触数据库目录。

`env list` 固定输出：

```text
CLUSTER  PLUGINS    GROUPS             CREATED  ENV_ID              STATE
mac      fbase_mac  streaming,logical  yes      env_20260713_090000 running
mmr      fdd_mmr    mmr                yes      env_20260712_180000 stopped
```

状态索引保存在控制端 `output/envs/<cluster>/state`，每个 PGDATA 保存相同 env_id 的 marker。
`STATE` 为 `running`、`stopped`、`partial`、`broken`、`config_error` 或 `-`。marker 与状态
索引不一致时为 `broken`，生命周期命令拒绝继续。`env show` 始终只读，展示解析后的节点、角色、
地址、端口、PGDATA、SSH、插件、关系组、复制对象名称和最终 PostgreSQL 参数，环境尚未创建
时也可使用。

### 4.3 终端输出与退出码

成功时终端仅输出横幅和逐用例结论：

```text
----------------------mac---------------------------------
mac.separation_of_duties.sso_system_privileges             SUCCESS
```

失败或阻塞时，在用例结论后显示失败步骤与原因以及该用例在本次 run 中的输出目录；如果本次执行
在本机 PGDATA 或用例输出目录产生 core 文件，还要显示每个 core 文件的绝对路径和可复制的 gdb 命令：

```text
mac.separation_of_duties.sso_system_privileges             FAILED
  失败原因: 第 1 步 检查 SSO 的初始系统权限: 预期 ...；实际 ...
  OUTPUT: output/runs/mac/<run_id>/mac/separation_of_duties/sso_system_privileges/
  CORE: /home/postgres/pgdata/mac1/core.1234
  GDB: gdb /usr/local/fbase15.15/bin/postgres /home/postgres/pgdata/mac1/core.1234
```

- `SUCCESS`：所有步骤的预期与实际一致。
- `FAILED`：实际退出状态、SQLSTATE、返回值或其他结果不符合预期。
- `BLOCKED`：环境、拓扑或外部前置条件缺失，步骤未执行。
- `SKIPPED`：未来可选用例被显式排除；首期不提供 tag 参数。
- 返回 `0`：没有 `FAILED`、`BLOCKED`；返回 `1`：存在 `FAILED` 或 `BLOCKED`；返回
  `2`：CLI、target、配置或环境标识错误。

## 5. 用例模型与分组

等保 suite 固定为：

```text
mac
├── separation_of_duties
├── mac
├── audit
├── password
├── tde
├── tlcp
├── gb18030
└── failover_slot
```

每个 case 在 `cases/` 下的公开模块中声明，suite 自动发现后生成 catalog：

| 字段 | 含义 |
| --- | --- |
| `id` | 语义化完整路径，是 show/run/report 的稳定标识。 |
| `document` | 转测文档文件与章节。 |
| `group` | 上述功能域。 |
| `requirements` | 所需插件、外部材料、group、节点角色及最少 member/standby/subscriber 数。 |
| `fixtures` | 公共 fixture 组合；当前内置 `cluster`、`database`、`roles`、`settings`、`topology`。 |
| `prerequisites` | 人可读的前置条件。 |
| `steps` | 有序业务步骤：标题、SQL/命令、预期、断言。 |
| `teardown` | 反向清理测试角色、数据库、复制槽、证书或数据。 |

`database` 和 `roles` fixture 负责创建临时对象并注册 DROP；`settings` 保存原值、执行
`ALTER SYSTEM` 和 reload/restart，并在用例结束后恢复；`topology` 校验所需关系组。后续
`instance`、`audit_log`、`tde` 和 `certificate` 也必须注册在公共 fixture 层，不能在 case 中
自行管理 PGDATA、配置文件、密钥或证书。

fixture 每完成一项修改就立即向 `TestContext` 注册清理动作。无论业务步骤、执行器或后续
fixture 是否失败，runner 都在 `finally` 中逆序清理；清理失败时 case 最终结论为 `FAILED`，
报告同时保留业务失败和清理失败。

公共步骤类型为 `sql`、`command`、`wait_sql` 和 `cluster_action`。SQL 统一通过
`PostgresClient` 执行；需要断言结果集时使用 CSV 获取结构化列和行，再由框架渲染成对齐的
psql 表格，case 不解析终端文本。步骤通过 `primary`、`standby`、`subscriber`、
`mmr:<member>` 或 `mmr:<member>:standby` 选择节点，不填写实际 host/port。

用例依赖的 PostgreSQL 参数统一声明在 `requirements.settings`，每项包含参数名、
`equals` 或 `contains` 要求、节点选择器和业务用途。公共
`PostgresClient.check_setting()` 使用 psql 执行 `SHOW <参数>`，runner 和 `settings` fixture
都调用该接口，用例不能自行实现参数查询。公共 `inspect_setting_configuration()` 通过
`pg_file_settings` 查询参数来自哪个配置文件及行号，并展示配置内容、是否应用和解析错误。
不满足时用例为 `BLOCKED`。报告先展示配置文件关键项，再按 psql 会话格式展示 `SHOW` 的
运行时实际结果、预期和判定；临时修改参数时还要展示修改前值。

结构化 case 的每条 SQL 都单独执行和断言。现有
`contrib/fbase_mac_dev/fbase_mac/src/test/regress` 仅作为迁移参考：将其中的业务场景、
SQL 和期望结果迁移到 `cases/` 中，拆分为可读的步骤和结构化断言。旧目录保持原样，
但新框架不调用 `pg_regress`、不依赖 `.out` 文件，也不设置 legacy adapter。

完整等保 target 仍选中其下所有 case：启用 fbase_mac 的 cluster 均可执行基础功能；
TDE、TLCP、GB18030 默认执行。TDE 基础场景在隔离集群中生成受控密钥；TLCP/SSL 缺少证书
材料、GB18030 全文检索缺少 `zhparser`、或 locale 缺失时逐条 `BLOCKED`。用户在
`plugins.fbase_mac.features.<name>=false` 时，对应用例逐条 `SKIPPED` 并说明“用户关闭该功能”，
不能静默消失；缺少所需流复制或逻辑复制 group 时，相关 case 逐条 `BLOCKED`。
同一基础功能需要高可用覆盖时，以同一 case 的 `ha` 变体运行，而不是复用单节点端口或 PGDATA。

## 6. 每用例 report.txt

**每个 case 在所属 run 中生成独立报告**；run 根目录生成 JSON 汇总和 JUnit 报告：

```text
output/runs/<cluster>/<run_id>/
├── summary.json
├── junit.xml
└── mac/<group>/<case>/
    ├── report.txt
    ├── execution.log
    ├── postgresql.log
    └── postgresql.csv
```

服务端日志只截取该 case 执行期间新增的内容，不复制完整历史日志。`summary.json` 记录
run ID、环境、目标、状态统计、每 case 耗时、失败/阻塞原因和证据路径；`junit.xml` 将
`BLOCKED` 记录为 skipped、`FAILED` 记录为 failure。`report` 扫描所有历史汇总；
`output clean [cluster]` 仅清理历史测试产物。

报告格式必须逐步骤展示 SQL、预期、实际和失败差异：

```text
用例: mac.audit.statement_rule_management
来源: 审计功能转测（邹雪、陈群友）.md / 5.2
分组: mac / audit
结论: FAILED
开始时间: 2026-07-13 10:15:30
结束时间: 2026-07-13 10:15:32
耗时: 2.0s
集群: mac env_id=env_20260713_090000 plugins=fbase_mac
节点: mac_primary(127.0.0.1:15432, data_dir=/home/postgres/pgdata/mac1)
Fixture: cluster,database,roles,audit_log

PostgreSQL 配置文件关键项:
1. 启用审计功能
   执行节点: mac_primary
   执行用户: postgres
   postgres=# SELECT ... FROM pg_file_settings WHERE name = 'fdb.enable_audit';
    config_file                       | line | configuration              | applied | error
   ----------------------------------+------+----------------------------+---------+------
    /home/postgres/pgdata/mac1/postgresql.conf | 36 | fdb.enable_audit = 'on' | t       |
   (1 row)
   证据: execution.log

PostgreSQL 生效配置:
1. 启用审计功能
   执行节点: mac_primary
   执行用户: postgres
   postgres=# SHOW fdb.enable_audit;
    fdb.enable_audit
   ------------------
    on
   (1 row)
   预期结果: 参数值等于 on
   判定: SUCCESS
   证据: execution.log

前置条件:
  - fbase_mac 已预加载
  - 审计日志目录可写

验证步骤:
1. 以 SAO 创建语句级审计规则
   postgres=> SELECT fdb_audit.create_stmt_rule(...);
   ERROR: permission denied for schema fdb_audit
   预期结果: 创建成功，返回规则名
   判定: FAILED
   失败原因: 预期 SAO 可创建规则；实际 SQLSTATE=42501，权限被拒绝
   证据: execution.log

2. 以普通用户执行被审计语句
   postgres=> INSERT INTO audit_table VALUES (1, 'a');
   未执行
   预期结果: 审计记录数增加 1
   判定: BLOCKED
   阻塞原因: 步骤 1 失败，后续断言不再具备有效前置条件

服务端日志:
  - postgresql.log
  - postgresql.csv

失败/阻塞说明:
  第 1 步权限边界与转测文档不一致；请检查 SAO 角色、扩展安装和权限初始化。
```

规则：

- SQL 使用 `postgres=#`/`postgres=>` 会话格式展示，紧随其后保留 psql 的对齐表格、命令标签
  或错误输出；shell 命令使用 `$ argv` 格式。不得只写“执行测试”。
- `预期结果` 必须可断言，例如返回值、行数、SQLSTATE、日志模式或目录状态。
- 实际输出必须展示真实返回值、SQLSTATE、行数或关键日志摘录，并放在预期与判定之前。
- `FAILED` 必须在 `失败原因` 中解释“预期与实际的差异”。
- 后续步骤因前序失败而不执行时，标记 `BLOCKED` 并说明依赖关系。
- 修改 PostgreSQL 配置的步骤必须列出节点、参数、旧值、新值、生效方式及 reload/restart 后的实际值。
- 完整原始日志不内嵌报告，只通过相对路径引用。
- 框架不做脱敏；报告和日志保留测试实际执行的 SQL、命令、参数和返回结果。

## 7. 验收与实施顺序

按以下顺序实施：

1. 唯一 YAML 配置与环境变量替换；
2. CLI 解析、target 前缀匹配、终端输出和退出码；
3. 带 marker 保护的本机及跨主机环境生命周期；
4. 每 case 独立 `report.txt` 渲染与日志路径；
5. manifest 文档映射完整性测试；
6. 将旧 `src/test/regress` 的场景迁移为结构化 case，但不修改旧目录；
7. 逐步将八份转测文档的每个测试点改写为结构化 case；
8. 最后接入受控主备切换和 GB18030/TLCP 外部依赖场景。

单元测试必须覆盖配置替换、CLI、target、环境标识保护、配置恢复、逐 case 报告中
SQL/预期/实际/失败原因字段，以及八份转测文档的映射完整性。

集成测试覆盖等保单机、case 专属认证/TDE/GB18030 实例、TLCP、故障转移槽屏障、逻辑复制、
多活模式切换恢复及 MMR member 主备切换；依赖条件不足的场景验证 `BLOCKED` 报告，
不伪造成功环境。

TLCP 证书生命周期用例只允许在空的证书元数据存储中执行。框架不得直接删除
`certs_info_achive` 等受产品 ACL 保护的表来清理环境；应使用隔离集群或由产品提供的受支持
清理接口。故障转移槽的延迟 commit 用例必须要求活跃逻辑订阅，订阅 apply 错误不能被忽略。

## 8. 跨主机与多活

控制端运行 `run.sh` 并保存报告，数据库节点运行 PostgreSQL 和已安装插件；两者不要求共享
文件系统。第 3 节同一份 cluster 配置同时支持本机和跨主机，也支持每个 MMR member 配置
一个 primary 和多个物理 standby。

### 8.1 远程执行默认值

- `local`、`localhost`、`127.0.0.1` 和本机地址直接执行，其他 host 默认使用 SSH/SCP。
- SSH 用户默认为当前操作系统用户，端口默认为 `22`，远程临时目录默认为
  `/tmp/fbase_regress`；只有覆盖默认值时才在 node 下写 `transport`、`ssh_user`、
  `ssh_port`、`work_dir` 或 `postgres_home`。
- YAML 不保存 SSH 密码和私钥口令；使用 SSH key、ssh-agent 或 `~/.ssh/config`。
- `doctor` 检查 SSH、远程 PostgreSQL 二进制、目录权限、端口和 license 分发条件。

### 8.2 生命周期与运行期角色

`env setup` 生成独立 `env_id`。控制端状态与每个 PGDATA 的 marker 必须使用同一 env_id。
start、stop、reload、restart、promote 和 clean 只操作 marker 匹配的
节点；部分失败时保留现场并明确列出节点。

cluster 保存初始拓扑；主备提升只更新控制端的运行期角色，不回写 `regress.yaml`。case 通过
member/role 定位节点，不直接写 IP。远程 stdout/stderr 和节点日志直接收集到 case 目录；
多节点用例在文件名中加入稳定节点名，报告同时记录角色、节点名和实际执行命令。

### 8.3 职责边界

| 层 | 职责 |
| --- | --- |
| framework/environment | 受管实例生命周期、配置、marker、复制关系和插件初始化。 |
| framework execution | 节点选择、本地/SSH 命令、SQL、fixture、断言、等待、日志、core 和报告。 |
| suite/case | 声明执行节点、业务步骤、预期、断言和清理；不得自行拼接 SSH 或管理进程。 |

远程节点不可达属于前置条件缺失时为 `BLOCKED`；执行过程中失联、状态错误或断言不符时为
`FAILED`。具体节点、预期、实际和证据必须写入该 case 的 `report.txt`。
