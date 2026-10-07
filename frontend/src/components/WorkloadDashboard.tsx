import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Empty, Select, Space, Statistic, Table, Tag, Timeline, Typography } from 'antd';
import { FullscreenOutlined, ReloadOutlined } from '@ant-design/icons';
import { api, statusColor } from '../platform/api';
import MonitoringTrend, { type TrendPoint } from './MonitoringTrend';
import type { HostObservation } from './HostMonitoring';
import './workloadDashboard.css';

type NodeObservation = { node: { id: string; host: string; port: number }; error?: string;
  sections: Record<string, { valid: boolean; error?: string; rows?: Record<string, unknown>[] }> };
type Snapshot = { observed_at: string; hosts?: HostObservation[]; nodes: NodeObservation[];
  database_metrics?: { node_id: string; metrics: Record<string, number | null> }[]; alerts?: { status: string; subject?: string; message?: string; label?: string }[] };
type Series = { key: string; label: string; unit: string; category: string; points: TrendPoint[] };
type Observation = { run_id: string | null; environment_id: string; enabled: boolean; running: boolean;
  range: { start: string; end: string }; interval_seconds: number; sample_count: number; displayed_samples: number;
  latest: Snapshot | null; series: Series[]; notes: string[];
  events: { observed_at: string; kind: string; subject: string; message: string; task_id?: string; source?: string }[];
  capabilities: Record<string, { available: boolean; reason: string }> };
export type ClientObservation = { id: string; title: string; status: string; started_at?: string | null;
  proxy?: { pid?: number; stopped?: boolean; endpoint?: string; cpu_percent?: number | null; rss_mib?: number };
  samples: { elapsed: number; tps: number; latency_ms: number; observed_at?: string }[];
  result_started_at?: number; summary?: { tps?: number; latency_ms?: number; errors?: number } };
const labels: Record<string, string> = { cpu_percent: 'CPU 使用率', memory_percent: '内存使用率', iowait_percent: 'IO 等待',
  instance_clients: '客户端连接', active: '活跃连接', idle: '空闲连接', idle_in_transaction: '事务中空闲连接',
  blocked: '被阻塞会话', long_transactions: '长事务', longest_transaction_seconds: '最长事务时长',
  commit_per_second: '数据库提交速率', rollback_per_second: '回滚速率', buffer_hit_percent: '缓冲区命中率',
  wal_mib_per_second: 'WAL 生成／接收速率', temp_mib_per_second: '临时数据写入速率', deadlocks_per_second: '死锁速率' };
const finite = (value: unknown): number | undefined => typeof value === 'number' && Number.isFinite(value) ? value : undefined;

export default function WorkloadDashboard({ environmentId, runId, clients, openTask }: {
  environmentId: string; runId?: string; clients: ClientObservation[]; openTask: (identity: string) => void;
}) {
  const [data, setData] = useState<Observation>();
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [nodeId, setNodeId] = useState('');
  const [hostId, setHostId] = useState('');
  const [clientId, setClientId] = useState('');
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const controller = new AbortController(); let timer: number;
    async function refresh() {
      try {
        const path = runId ? `/workload-runs/${encodeURIComponent(runId)}/monitoring`
          : `/environments/${encodeURIComponent(environmentId)}/workload-monitoring`;
        const value = await api<Observation>(path, { signal: controller.signal });
        if (!controller.signal.aborted) { setData(value); setError(''); }
        if (!controller.signal.aborted && (!runId || value.running)) timer = window.setTimeout(() => void refresh(), 5000);
      } catch (cause) {
        if (!controller.signal.aborted) { setError(`稳定性监控读取失败：${(cause as Error).message}。客户端指标独立读取；环境采集状态暂时无法确认。`); timer = window.setTimeout(() => void refresh(), 5000); }
      }
    }
    setData(undefined); void refresh();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [environmentId, runId, revision]);
  const latest = data?.latest;
  const stale = Boolean(data?.running && latest && Date.now()-Date.parse(latest.observed_at) > 60000);
  const nodes = latest?.nodes || [];
  const node = nodes.find((item) => item.node.id === nodeId) || nodes[0];
  const hosts = latest?.hosts || [];
  const host = hosts.find((item) => item.identity === hostId) || hosts[0];
  const client = clients.find((item) => item.id === clientId) || clients.find((item) => item.status === 'RUNNING') || clients.filter((item) => item.samples.length).at(-1) || clients[0];
  const db = latest?.database_metrics?.find((item) => item.node_id === node?.node.id)?.metrics || {};
  const hostValue = (key: 'cpu_percent' | 'memory_percent' | 'iowait_percent') => !stale && host?.valid ? finite(host.metrics?.[key]) : undefined;
  const dbValue = (key: string) => !stale && !node?.error ? finite(db[key]) : undefined;
  const observedNodes = stale ? undefined : latest ? nodes.filter((item) => !item.error && item.sections.runtime?.valid).length : undefined;
  async function enable() {
    try { await api(`/environments/${encodeURIComponent(environmentId)}/monitoring`, { method: 'POST', body: JSON.stringify({ enabled: true }) }); setRevision((value) => value+1); }
    catch (cause) { setError((cause as Error).message); }
  }
  function metricSeries(key: string, category: string): Series[] {
    return data?.series.filter((series) => series.category === category && series.key.endsWith(':'+key)
      && (category === 'host' ? series.key.startsWith('host:'+host?.identity+':') : series.label.startsWith(node?.node.id+' /'))) || [];
  }
  function graph(key: string, category: string, unit: string) {
    const found = metricSeries(key, category);
    return <Card size="small" className="wd-chart" title={labels[key] || key}>{found.length ? found.map((series) => <MonitoringTrend key={series.key} label={labels[key] || series.label} unit={series.unit} points={series.points} range={data?.range} gapSeconds={Math.max(90, (data?.interval_seconds || 15)*2)} />) : <div className="wd-unavailable"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`未采集 ${unit} 数据／等待有效基线`} /></div>}</Card>;
  }
  function clientGraph(field: 'tps' | 'latency_ms', title: string, unit: string) {
    const start = client?.result_started_at ? client.result_started_at*1000 : Date.parse(client?.started_at || '');
    const points = (client?.samples || []).flatMap((sample) => {
      const stamp = sample.observed_at ? Date.parse(sample.observed_at) : start+sample.elapsed*1000;
      if (!Number.isFinite(stamp) || !data || stamp < Date.parse(data.range.start) || stamp > Date.parse(data.range.end)) return [];
      return [{ time: new Date(stamp).toISOString(), value: finite(sample[field]) ?? null }];
    });
    return <Card size="small" className="wd-chart" title={title}><MonitoringTrend label={title} unit={unit} points={points} range={data?.range} gapSeconds={5} /></Card>;
  }
  const stat = (title: string, value: number | undefined, suffix?: string) => <div className="wd-stat"><Statistic title={title} value={value ?? '未采集'} precision={value === undefined ? undefined : suffix === '%' || suffix === 'ms' ? 1 : Number.isInteger(value) ? 0 : 2} suffix={value === undefined ? undefined : suffix} /></div>;
  const replication = data?.series.filter((series) => series.category === 'replication' && series.label.startsWith(node?.node.id+' /')) || [];
  return <div className="wd-dashboard" ref={panel}>
    <header className="wd-header"><div><Typography.Title level={4}>稳定性综合监控</Typography.Title><Typography.Text type="secondary">{runId ? '本次运行独立时间窗口 · '+runId.slice(-12) : '环境预览 · 最近 15 分钟，不属于某次测试'}</Typography.Text></div>
      <Space wrap><Tag color={data?.enabled ? 'processing' : 'default'}>{data ? data.enabled ? '后台采集已启用' : '采集未启用' : error ? '采集状态未知' : '读取采集状态中'}</Tag>{data && !data.enabled && <Button onClick={() => void enable()}>启用环境采集</Button>}<Button icon={<ReloadOutlined />} onClick={() => setRevision((value) => value+1)}>刷新</Button><Button icon={<FullscreenOutlined />} onClick={() => void panel.current?.requestFullscreen().catch((cause: Error) => setError(cause.message))}>全屏</Button></Space>
    </header>
    {error && <Alert type="error" showIcon message={error} />}
    {data?.notes.map((note) => <Alert key={note} type="info" showIcon message={note} />)}
    {stale && <Alert type="warning" showIcon message="采样已过期，当前状态不可判定；历史曲线仅作为历史证据。" />}
    <div className="wd-scope"><Space wrap><Select aria-label="监控实例" value={node?.node.id} placeholder="等待实例采样" style={{ minWidth: 160 }} options={nodes.map((item) => ({ value: item.node.id, label: item.node.id }))} onChange={setNodeId} />
      <Select aria-label="监控主机" value={host?.identity} placeholder="等待主机采样" style={{ minWidth: 180 }} options={hosts.map((item) => ({ value: item.identity, label: item.host }))} onChange={setHostId} />
      <Select aria-label="监控工作负载" value={client?.id} placeholder="尚未启动负载" style={{ minWidth: 180 }} options={clients.map((item) => ({ value: item.id, label: item.title }))} onChange={setClientId} />{client && <Tag color={statusColor(client.status)}>{client.status}</Tag>}</Space>
      <span>{data ? `${new Date(data.range.start).toLocaleString()} — ${new Date(data.range.end).toLocaleString()}` : '读取运行时间范围'} · 环境快照 {data?.sample_count ?? '—'} 次</span>
    </div>
    <div className="wd-stats">{stat('客户端 TPS', finite(client?.summary?.tps) ?? finite(client?.samples.at(-1)?.tps))}{stat('平均延迟', finite(client?.summary?.latency_ms) ?? finite(client?.samples.at(-1)?.latency_ms), 'ms')}{stat('主机 CPU', hostValue('cpu_percent'), '%')}{stat('主机内存', hostValue('memory_percent'), '%')}{stat('活跃连接', dbValue('active'))}{stat('阻塞会话', dbValue('blocked'))}{stat('可观测节点', observedNodes, latest ? '/ '+nodes.length : undefined)}</div>
    {client?.proxy && <div><Typography.Paragraph>本轮 fbasecman：PID {client.proxy.pid} · {client.proxy.endpoint} · {client.proxy.stopped ? '已停止，以下为末次历史采样' : '已启动'}</Typography.Paragraph><div className="wd-stats">{stat('fbasecman CPU（100% = 一个核）', finite(client.proxy.cpu_percent), '%')}{stat('fbasecman RSS', finite(client.proxy.rss_mib), 'MiB')}</div></div>}
    <div className="wd-charts">{clientGraph('tps', '客户端吞吐 · TPS', 'tx/s')}{clientGraph('latency_ms', '客户端平均延迟', 'ms')}{graph('cpu_percent', 'host', '%')}{graph('memory_percent', 'host', '%')}{graph('iowait_percent', 'host', '%')}{graph('instance_clients', 'db', '连接')}{graph('active', 'db', '连接')}{graph('blocked', 'db', '会话')}{graph('commit_per_second', 'db', 'tx/s')}{graph('wal_mib_per_second', 'db', 'MiB/s')}{graph('long_transactions', 'db', '事务')}
      <Card size="small" className="wd-chart" title="复制进度 · 已发送到回放位置差">{replication.length ? replication.map((series) => <MonitoringTrend key={series.key} label={series.label} unit="MiB" points={series.points} range={data?.range} gapSeconds={Math.max(90,(data?.interval_seconds || 15)*2)} />) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无有效发送／回放位置对，不等于零延迟" />}</Card>
    </div>
    <div className="wd-bottom"><Card size="small" title="实例与角色 · 末次有效观测"><Table size="small" pagination={false} rowKey={(item) => item.node.id} dataSource={nodes} scroll={{ x: 460 }} columns={[{ title: '实例', render: (_, item) => item.node.id }, { title: '地址', render: (_, item) => item.node.host+':'+item.node.port }, { title: '实际角色', render: (_, item) => <Tag>{stale || item.error || !item.sections.runtime?.valid ? '不可观测' : item.sections.runtime.rows?.[0]?.recovery === true ? '备库' : item.sections.runtime.rows?.[0]?.recovery === false ? '主库' : '未知'}</Tag> }]} /></Card>
      <Card size="small" title="统一事件时间轴">{data?.events.length ? <Timeline items={data.events.slice(-30).reverse().map((event) => ({ children: <div><small>{new Date(event.observed_at).toLocaleString()} · {event.source || '环境观测'}</small><strong>{event.subject}</strong><p>{event.message}</p>{event.task_id && <Button size="small" onClick={() => openTask(event.task_id!)}>任务证据</Button>}</div> }))} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次区间没有记录事件，不据此断言无异常" />}</Card>
    </div>
    <div className="wd-capabilities">{Object.entries(data?.capabilities || {}).map(([key, capability]) => <Alert key={key} type={capability.available ? 'success' : 'info'} showIcon message={({ pool: '连接池观测', prepared_statements: '预备语句缓存', percentiles: '分位延迟' } as Record<string, string>)[key] || key} description={capability.reason} />)}</div>
    <div className="wd-provenance">数据库／角色按约 15 秒采集，主机按约 60 秒缓存。曲线时间为观测时间，不是故障精确发生时间；重启、基线变化、缺失样本不会补零。{data && data.interval_seconds > 15 && ` 环境曲线为每 ${data.interval_seconds} 秒窗口的末次样本，未冒充均值或峰值。`} 客户端新产物使用接收时间，旧产物按启动时刻估算；不混加多主机 CPU、不同负载 TPS，也不把 WAL 距离当作业务延迟。</div>
  </div>;
}
