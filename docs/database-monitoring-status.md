# 数据库监控功能状态与使用

更新：2026-10-03。本文是当前实现清单，设计参考见 [通用方案](database-observability-plan.md) 与 [MMR 专项方案](mmr-monitoring-design.md)。

## 已实现

| 能力 | 当前行为 |
| --- | --- |
| 独立数据库监控入口 | 六组专题，复用环境和唯一 Studio；实例详情可筛选 |
| 后台共享采集 | 数据库约 15 秒、主机约 60 秒；跨 Web 进程锁和缓存合并 |
| 拓扑与复制诊断 | 实际成员、物理主备、MMR 两个方向分别识别；点击边查看完整证据 |
| 复制进度 | 同源 WAL 阶段差值和推进速率；明确串行配置时显示接收到 origin 的坐标距离 |
| 数据库负载 | 事务、连接、缓冲、WAL、死锁、临时文件、阻塞与长事务 |
| 会话与锁 | 阻塞图、PID／backend_start 身份核对、SQL 和等待事件 |
| 多活诊断 | 原始冲突行对比、策略与结果标记、成员状态对照、错误恢复、复制范围 |
| 主机资源 | CPU、IO 等待、内存、分设备吞吐、PGDATA／WAL 容量；同机不重复累计 |
| 查询和表维护 | 按需读取现有 pg_stat_statements，表维护估计与 Vacuum 进度；不自动安装或维护 |
| 告警 | 槽、链路、成员分歧、主机、连接、阻塞、长事务；连续确认与恢复滞后 |
| 历史与事件 | 原始 24 小时、分钟聚合 7 天，保留均值与峰值；联合部署任务事实 |
| Prometheus 出口 | 0.0.4 文本格式，只读缓存，不增加采样或数据库查询 |

## 使用

在「数据库监控」选择已配置部署拓扑的环境，点击「启用后台采集」。首个实例样本即可查看角色、连接和配置证据；速率至少需要两个有效样本，主机 CPU／IO 需建立约 60 秒间隔的基线。暂停后台采集保留历史。当前没有自动启用实际环境的采集开关。

查询排行和表维护需选实例后手动读取。扩展缺失显示不可用；查询调用率／耗时为数据库统计口径，不能冒充业务去重 TPS 或 P95。

## Prometheus 接入

每环境出口：`GET /api/v1/environments/{environment_id}/monitoring/metrics`。页面「Prometheus 指标」可打开原始文本。遵循 [Prometheus 官方文本格式](https://prometheus.io/docs/instrumenting/exposition_formats/)，返回 `text/plain; version=0.0.4; charset=utf-8`。

配置示例（已有 Prometheus 服务自行配置，不由平台安装）：

```yaml
scrape_configs:
  - job_name: product-platform-mmr
    scrape_interval: 15s
    metrics_path: /api/v1/environments/env-ccb27e809724485c/monitoring/metrics
    static_configs:
      - targets: ["192.168.0.12:8080"]
```

出口从共享 SQLite 最新快照读取，不启用采集、不查询数据库、不读取任务日志。`platform_monitor_enabled` 表示采集开关；`platform_monitor_sample_available` 表示存在一分钟以内样本；`platform_monitor_sample_age_seconds` 表示缓存年龄。未采集或过期时不输出数据库负载等当前数值，缺失指标省略而不是填零。暂停后的最近有效快照可在一分钟内保留，仪表盘同时检查 enabled 和 sample_available。

主要指标：`platform_postgres_commits_per_second`、`platform_postgres_instance_clients`、`platform_postgres_buffer_hit_percent`、`platform_replication_stage_bytes`、`platform_replication_progress_bytes_per_second`、`platform_replication_slot_retained_bytes`、`platform_replication_link_state`、`platform_host_cpu_percent`、`platform_host_filesystem_available_bytes`、`platform_monitor_alert_state`。

全部为缓存观测 gauge，速率是已计算的速率，不再对它调用 rate。原始 LSN 不转浮点导出，避免精度丢失；SQL、冲突行、错误详情、凭据和采样位置不作标签。标签仅使用环境／节点／资源身份及状态，文字按格式转义。

PromQL 示例：

```promql
platform_postgres_commits_per_second
  and on(environment_id) (platform_monitor_enabled == 1)
  and on(environment_id) (platform_monitor_sample_available == 1)
```

## 仍有明确边界

- 并行应用的连续提交水位、精确多活应用时间延迟和追平估计没有可靠公开接口，当前不虚构。订阅统计会跳过并行 worker；实例进程可读但不能据此归属所有 writer。
- 外部通知渠道、远程写入和自动修复未接入。Prometheus 出口是被动抓取，没有向外部系统发送消息。
- 真实多活环境只做只读核验；真实断连、重启、提升、行锁与扩展安装测试均在隔离临时数据库执行。
- 当前采集间隔、保留期限与结果上限在方案中明确；分钟峰值是有效采样值的峰值，不承诺捕获采样间隔内的瞬时峰值。
