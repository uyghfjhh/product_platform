# 金仓 KingbaseES 现有环境接入

首批实测环境：KingbaseES V009R001C010，Oracle 兼容模式，主库 54321、物理备库 54322。安装目录 `/opt/Kingbase/ES/V9/Server`，实例由 `kingbase` 操作系统用户运行。

## 能力与范围

- 产品目录发现、单机或现有主备环境登记。
- 保存前检查真实版本、数据目录、主备角色、数据库 system identifier、备库接收来源和复制发送状态；登记不初始化实例。
- 统一拓扑、实际角色及连接状态，连接失败显示不可判定，不据此认定实例已停。
- Studio 元数据与表浏览、SQL、参数绑定和事务，主备节点选择，以及会话、锁、复制、参数只读运维查询。
- 共享监控采样、历史和基础指标，金仓物理链路使用系统标识、接收上游、物理槽及发送状态确认。
- 支持当前本机安装的原生启停、重启和状态检查，通过公共任务队列及资源锁执行，使用数据目录所属 kingbase 用户调用 sys_ctl。当前安装路径为 /opt/Kingbase/ES/V9/Server。不提供初始化、清理、重建、升主、切换，也不自动套用 PostgreSQL 压测入口。Oracle 全部语法和数据类型未做全量认证。

## 架构

`product.yaml` 是能力来源；`provider.py` 是公共服务入口。`database.py` 管理原生 SQL 和身份观测；`topology.py` 读取平台库存配置并观察节点；`monitoring.py` 覆盖 V9 统计差异；`router.py` 登记已存在的实例；`frontend.ts` 和 `frontend/` 提供登记表单。金仓专有知识只放在本目录。

公共层提供可选的 provider `topology/status`、`studio_inspect_queries`、`validate_environment`、`workload_actions` 扩展。其他产品未声明扩展时沿用原流程。前端通用环境表单通过产品 `environmentRegistration` 扩展提交，不硬编码金仓字段。

库存配置采用公共 `hosts/instances/streaming_clusters` 身份结构，保存于 `data/profiles/<环境ID>/kingbase.yaml`，由资源登记和互斥机制覆盖两个节点的端点及数据目录。它带有 `product_id: kingbase`，不是可交给 pgcluster 执行的 PostgreSQL 部署配置。

## 登记与连接

网页「数据库部署管理 → 新增环境 → 金仓 KingbaseES」，填写主库及可选备库连接。数据库名、用户默认 `kingbase`。主备都必须允许平台通过 TCP 连接；当前环境只对 `127.0.0.1` 的 `kingbase` 用户和同名数据库放行免密认证。

也可调用 `POST /api/v1/products/kingbase/environments`：

```json
{
  "id": "kingbase-v9",
  "title": "金仓 V9 本机主备",
  "host": "127.0.0.1",
  "port": 54321,
  "database_name": "kingbase",
  "database_user": "kingbase",
  "standby_host": "127.0.0.1",
  "standby_port": 54322
}
```

不传 `standby_port` 可登记现有单机。更换集群端点或成员时重新登记；普通环境编辑会检查连接与库存配置一致性。库存配置不保存数据库密码。

## 版本差异

- V9 可通过现有 psycopg 驱动使用 TCP 连接；原生本地 socket 命名与 libpq PostgreSQL 默认 socket 名不同。
- V9 `sys_stat_wal_receiver` 提供 `received_lsn`，不提供 PostgreSQL 新版本的 `written_lsn/flushed_lsn`；不伪造两种持久化进度。
- V9 不提供 `pg_stat_wal`；该统计段显示不可用，其余采样独立有效。
- 原生命令为 `ksql/sys_ctl/sys_basebackup/sys_rewind`，配置为 `kingbase.conf/sys_hba.conf`，WAL 目录为 `sys_wal`。这些差异不能通过把 Kingbase 安装目录伪装成 PGHOME 来解决。

## 验证

```bash
PYTHONPATH=backend:backend/pgcluster:. .venv/bin/python -m pytest tests/test_kingbase.py -q
KINGBASE_LIVE=1 PYTHONPATH=backend:backend/pgcluster:. .venv/bin/python -m pytest tests/test_kingbase_live.py -q
(cd frontend && npm run build)
```

真实主备验证仅在声明的端口存在时运行。独立 schema 的增删改、事务回滚、物理复制与备库写入拒绝，及浏览器验收证据位于本次 `output/diagnostics/kingbase-*`，测试对象结束后清理。
