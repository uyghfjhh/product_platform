import { Alert, Card, Empty, Space, Typography } from 'antd';
import MonitoringTrend from './MonitoringTrend';
export type HostObservation={host:string;valid:boolean;error?:string;identity?:string;observed_at?:string;raw?:{hostname:string;boot_id:string;cpu_count:number;uptime_seconds:number;load_average:number[];filesystems:{device?:string;paths:string[];total_bytes?:number;available_bytes?:number;error?:string}[]};metrics?:{cpu_percent:number|null;iowait_percent?:number|null;memory_percent:number|null;devices:Record<string,{read_mib_per_second:number|null;write_mib_per_second:number|null}>}};
type Series={key:string;label:string;unit:string;points:{time:string;value:number;peak:number}[]};
const number=(v:number|null|undefined)=>v===null||v===undefined?'未提供':v.toFixed(2);
export default function HostMonitoring({hosts,series,gapSeconds}:{hosts:HostObservation[];series:Series[];gapSeconds:number}) {
  if(!hosts.length)return <Empty description="主机采集尚未接入" />;
  return <Space orientation="vertical" style={{width:'100%'}}>{hosts.map(h=><Card key={h.host} title={h.host}>
    {!h.valid ? <Alert type="warning" title={h.error || '主机不可观测'} /> : <>
      <Typography.Paragraph type="secondary">{h.raw?.hostname} · 主机采样 {h.observed_at?new Date(h.observed_at).toLocaleString():'时间未知'} · SSH 缓存间隔 60 秒；同机多个实例不重复累计</Typography.Paragraph>
      <Space wrap><Card size="small" title="CPU 使用率"><p>{number(h.metrics?.cpu_percent)} %</p><Typography.Text type="secondary">{h.raw?.cpu_count} 核、相邻样本平均；IO 等待 {number(h.metrics?.iowait_percent)} %；首个样本需建立基线</Typography.Text></Card><Card size="small" title="内存使用率"><p>{number(h.metrics?.memory_percent)} %</p><Typography.Text type="secondary">依据 MemAvailable，非简单 free</Typography.Text></Card><Card size="small" title="负载与运行时间"><p>{h.raw?.load_average?.join(' / ')}</p><p>{number(h.raw?.uptime_seconds===undefined?undefined:h.raw.uptime_seconds/3600)} 小时</p></Card></Space>
      <Card size="small" title="PGDATA / WAL 文件系统容量（同卷合并）">{h.raw?.filesystems.map((f,i)=><div key={i}><p>{f.paths.join(' · ')}</p>{f.error?<Alert type="warning" title={f.error} />:<p>可用 {number(f.available_bytes===undefined?undefined:f.available_bytes/1073741824)} GiB / 总量 {number(f.total_bytes===undefined?undefined:f.total_bytes/1073741824)} GiB</p>}</div>)}</Card>
      <Card size="small" title="设备 IO · MiB/s"><Typography.Paragraph type="secondary">物理盘与 dm 设备分列，不跨层相加。</Typography.Paragraph>{!Object.keys(h.metrics?.devices || {}).length && <Empty description="等待 IO 基线或设备统计" />}{Object.entries(h.metrics?.devices || {}).map(([device,r])=><p key={device}>{device} · 读取 {number(r.read_mib_per_second)} · 写入 {number(r.write_mib_per_second)}</p>)}</Card>
      {series.filter(s=>s.key.startsWith(`host:${h.identity}:`)).map(s=><Card key={s.key} title={s.label}><MonitoringTrend label={s.label} unit={s.unit} points={s.points} gapSeconds={gapSeconds} /></Card>)}
    </>}
  </Card>)}</Space>;
}
