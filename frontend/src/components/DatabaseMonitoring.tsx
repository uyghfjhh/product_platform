import { useEffect, useState } from 'react';
import { Alert, Button, Card, Drawer, Empty, Select, Space, Tabs, Typography, type TabsProps } from 'antd';
import MonitoringTopology, { type ObservedLink, type ObservedMember } from './MonitoringTopology';
import MonitoringAlertPanel,{type AlertRules,type AlertRecord as MonitorAlert} from './MonitoringAlertPanel';
import HostMonitoring, {type HostObservation} from './HostMonitoring';
import MonitoringDiagnostics from './MonitoringDiagnostics';
import Table, {type MonitoringRow as Row,type MonitoringSection as Section,monitorValue as text} from './MonitoringTable';
import DatabaseLoadCharts, { type DatabaseMetric } from './DatabaseLoadCharts';
import SessionBlockingGraph from './SessionBlockingGraph';
import MonitoringTrend from './MonitoringTrend';
import { api } from '../platform/api';
type Node = { node: { id: string; label: string; host: string; port: number }; error?: string; sections: Record<string, Section> };
type ReplicationMetric = { node_id: string; pid: number; backend_start: string; application_name: string; slot_type?: string | null; stage_bytes: Record<string, number | null>; bytes_per_second: Record<string, number | null>; observed_at: string };
type Snapshot = { observed_at: string; nodes: Node[]; hosts?:HostObservation[]; database_metrics?:DatabaseMetric[]; member_consensus?: {key:string;member_name:string;status:string;observations:{observer:string;state:string}[]}[]; resolved_topology?: { members: ObservedMember[]; links: ObservedLink[] }; replication_metrics?: ReplicationMetric[]; alerts?: MonitorAlert[]; mmr_metrics?: {node_id:string;sub_id:number;received_to_origin_bytes:number|null;apply_mode?:string;observable_workers?:number;error_records_without_recovery:number;invalid_error_timestamps:number;evidence:string}[]; topology?: { edges: { id: string; source: string; target: string; kind: string }[] } };
type Response = { enabled: boolean; latest: Snapshot | null; history: Snapshot[]; events?: {kind:string;subject:string;message:string;observed_at:string;task_id?:string;source?:string}[]; interval_seconds: number; series?: {key:string;label:string;unit:string;points:{time:string;value:number;peak:number;valid_samples:number}[]}[]; window_minutes?:number; rules?:AlertRules };
const labels: Record<string, string> = { runtime: '角色与位置', senders: '发送端', receivers: '物理接收端', slots: '复制槽', members: '成员', subscriptions: '有向订阅', subscription_runtime: '接收进程', database:'数据库统计',connections:'客户端连接',wal:'WAL 统计',sessions:'会话与 SQL',blocking:'阻塞关系',activity_summary:'事务与锁摘要',workers:'应用进程与等待', apply_configuration:'应用串行／并行配置', origins: 'Origin 位置', errors: '进程错误与恢复', conflicts: '冲突', scope: '复制范围', catchup: '追平记录' };
function MonitoringSections({items}:{items:NonNullable<TabsProps['items']>}) {
  const item=(key:string)=>items.find(i=>i.key===key)!;
  const sub=(keys:string[])=> <Tabs items={keys.map(item)} />;
  return <Tabs items={[
    item('overview'),
    {key:'load-group',label:'负载趋势',children:<Tabs items={[{...item('load'),label:'即时负载'},item('aggregates')]} />},
    {key:'replication-group',label:'复制监控',children:sub(['physical','mmr','members','history','scope'])},
    {key:'diagnostics-group',label:'诊断工作区',children:sub(['sessions','conflicts','errors','queries','tables'])},
    item('hosts'),item('events'),
  ]} />;
}
function Chart({ samples, nodeId, slot }: { samples: Snapshot[]; nodeId: string; slot: string }) {
  let previousBaseline = '';
  const points = samples.map(s => {
    const n = s.nodes.find(x => x.node.id===nodeId);
    const row = n?.sections.slots?.valid ? n.sections.slots.rows?.find(x=>x.slot_name===slot) : undefined;
    const baseline = n?.sections.runtime?.valid ? text(n.sections.runtime.rows?.[0]?.started_at) : '';
    const restarted = previousBaseline && previousBaseline!==baseline;
    previousBaseline = baseline;
    const v = row?.retained_bytes;
    return {time:s.observed_at,value:!baseline || restarted || v===null || v===undefined ? null : Number(v)/1048576};
  });
  return <MonitoringTrend points={points} unit="MiB" label={`${slot} 保留 WAL`} />;
}
function ReplicationProgress({ samples, current, nodeId, kind }: { samples: Snapshot[]; current: Snapshot; nodeId: string; kind: 'physical' | 'logical' }) {
  const metrics = current.replication_metrics?.filter(m => m.node_id === nodeId && (kind==='logical' ? m.slot_type==='logical' : m.slot_type!=='logical')) || [];
  const stages: Record<string,string> = { sent_to_write: '已发送 → 写入差值', write_to_flush: '写入 → 刷盘差值', flush_to_replay: '刷盘 → 回放差值' };
  const rates: Record<string,string> = { sent_lsn: '发送位置推进', write_lsn: '写入位置推进', flush_lsn: '刷盘位置推进', replay_lsn: '回放位置推进' };
  return <>{metrics.map(m => <Card key={`${m.pid}:${m.backend_start}`} size="small" title={`${m.application_name} · WAL 位置进度`}>
    <Space wrap>{Object.entries(stages).map(([key,label]) => <Card size="small" key={key}><Typography.Text type="secondary">{label}</Typography.Text><p>{m.stage_bytes[key] === null ? '不可计算' : `${(m.stage_bytes[key]/1048576).toFixed(3)} MiB`}</p></Card>)}</Space>
    <Tabs items={Object.entries(rates).map(([key,label]) => ({key,label,children:<MonitoringTrend label={label} unit="MiB/s" points={samples.map(sample => { const metric = sample.replication_metrics?.find(x => x.node_id===m.node_id && x.pid===m.pid && x.backend_start===m.backend_start); const value = metric?.bytes_per_second[key]; return {time:sample.observed_at,value:value===null || value===undefined ? null : value/1048576}; })} />}))} />
    <Typography.Text type="secondary">同一发送连接的 WAL 坐标推进，非业务吞吐；重启、时间线变化、断连或超过 45 秒采样间隔重新建立基线。</Typography.Text>
  </Card>)}</>;
}
function Conflict({ row }: { row: Row }) {
  const object = (v: unknown): Row => typeof v === 'object' && v !== null && !Array.isArray(v) ? v as Row : {};
  const a = object(row.local_tuple), b = object(row.remote_tuple), keys = [...new Set([...Object.keys(a), ...Object.keys(b)])];
  return <Card size="small" title={`${text(row.nspname)}.${text(row.relname)} · ${text(row.conflict_type)} · ${text(row.conflict_resolution)}`}><p>{text(row.local_time)} · sub_id {text(row.sub_id)}</p><div style={{ overflowX: 'auto' }}><table style={{ width: '100%' }}><thead><tr><th>字段</th><th>本地</th><th>远端</th></tr></thead><tbody>{keys.map(k => <tr key={k} style={{ background: JSON.stringify(a[k]) !== JSON.stringify(b[k]) || (k in a) !== (k in b) ? 'rgba(220,160,50,.15)' : undefined }}><td>{k}</td><td>{k in a ? text(a[k]) : '字段缺失'}</td><td>{k in b ? text(b[k]) : '字段缺失'}</td></tr>)}</tbody></table></div><p>apply_tuple 原始标记：{text(row.apply_tuple)}</p><details><summary>完整记录（不脱敏）</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{text(row)}</pre></details></Card>;
}
export default function DatabaseMonitoring({ environmentId, onOpenStudio, onOpenTask }: { environmentId: string; onOpenStudio: (nodeId: string) => void; onOpenTask: (taskId: string) => void }) {
  const [now,setNow]=useState(Date.now);
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),5000);return()=>clearInterval(timer);},[]);
  const [detailNodeId,setDetailNodeId]=useState('');
  const [selectedSession,setSelectedSession]=useState<{nodeId:string;pid:number;start:unknown}>();
  const [windowMinutes,setWindowMinutes]=useState(60);
  const [selectedLinkId,setSelectedLinkId] = useState<string>();
  const [editedRules, setEditedRules] = useState<AlertRules>();
  const [data, setData] = useState<Response>(); const [error, setError] = useState(''); const [revision, setRevision] = useState(0);
  useEffect(() => { const c = new AbortController(); let timer: ReturnType<typeof setTimeout>; const load = async () => { try { const value = await api<Response>(`/environments/${encodeURIComponent(environmentId)}/monitoring?window_minutes=${windowMinutes}`, { signal: c.signal }); if (!c.signal.aborted) { setData(value); setError(''); } } catch (e) { if (!c.signal.aborted) setError(String(e)); } finally { if (!c.signal.aborted) timer = setTimeout(load, 5000); } }; void load(); return () => { c.abort(); clearTimeout(timer); }; }, [environmentId, revision, windowMinutes]);
  const toggle = async () => { try { await api(`/environments/${encodeURIComponent(environmentId)}/monitoring`, { method: 'POST', body: JSON.stringify({ enabled: !data?.enabled }) }); setRevision(x => x + 1); } catch (e) { setError(String(e)); } };
  const rules = editedRules || data?.rules || {retained_mib:512,consecutive_samples:3,cpu_percent:85,memory_percent:90,disk_free_percent:10,connection_percent:80,blocked_sessions:1,long_transaction_seconds:180};
  const saveRules = async () => { try { await api(`/environments/${encodeURIComponent(environmentId)}/monitoring/rules`, {method:'PUT',body:JSON.stringify(rules)}); setEditedRules(undefined); setRevision(x=>x+1); } catch(e) { setError(String(e)); } };
  const latest = data?.latest;
  const stale=!!latest && now-Date.parse(latest.observed_at)>60000;
  const displayedLinks=(latest?.resolved_topology?.links || []).map(l=>stale?{...l,status:'unknown',reason:'缓存样本已过期；'+l.reason}:l);
  const sessionNode=latest?.nodes.find(n=>n.node.id===selectedSession?.nodeId);
  const sessionRows=sessionNode?.sections.sessions?.rows?.filter(r=>!!selectedSession?.start && r.pid===selectedSession.pid && r.backend_start===selectedSession.start) || [];
  const selectedLink = displayedLinks.find(l=>l.id===selectedLinkId);
  const sourceNode = latest?.nodes.find(n=>n.node.id===selectedLink?.source);
  const targetNode = latest?.nodes.find(n=>n.node.id===selectedLink?.target);
  const pick = (node:Node|undefined,key:string,predicate:(r:Row)=>boolean):Section => {
    const section=node?.sections[key];
    return section?.valid ? {...section,rows:section.rows?.filter(predicate)} : {valid:false,error:section?.error || '实例映射或统计不可观测'};
  };
  const detailNodes=latest?.nodes.filter(n=>!detailNodeId || n.node.id===detailNodeId) || [];
  const wrap = (render: (n: Node) => React.ReactNode) => <Space orientation="vertical" style={{ width: '100%' }}>{!detailNodes.length && <Empty description="所选实例不在当前采样中" />}{detailNodes.map(n => <Card key={n.node.id} title={`${n.node.label} · ${n.node.host}:${n.node.port}`} extra={<Button onClick={() => onOpenStudio(n.node.id)}>Studio</Button>}>{n.error ? <Alert type="warning" title={n.error} /> : render(n)}</Card>)}</Space>;
  const details = (keys: string[]) => wrap(n => <Tabs items={keys.map(k => ({ key: k, label: labels[k], children: <Table section={n.sections[k]} /> }))} />);
  return <Space orientation="vertical" style={{ width: '100%' }}><Space wrap><Button type="primary" onClick={() => void toggle()}>{data?.enabled ? '停止后台采集' : '启用后台采集'}</Button><Button onClick={() => setRevision(x => x + 1)}>刷新缓存</Button><Button href={`/api/v1/environments/${encodeURIComponent(environmentId)}/monitoring/metrics`} target="_blank" rel="noopener noreferrer">Prometheus 指标</Button><Select aria-label="监控时间范围" value={windowMinutes} onChange={setWindowMinutes} options={[{value:15,label:'15 分钟'},{value:60,label:'1 小时'},{value:360,label:'6 小时'},{value:1440,label:'24 小时'},{value:10080,label:'7 天'}]} /><Select aria-label="实例详情范围" value={detailNodeId} onChange={setDetailNodeId} options={[{value:'',label:'全部实例详情'},...(latest?.nodes || []).map(n=>({value:n.node.id,label:n.node.label}))]} style={{minWidth:160}} /><Typography.Text type="secondary">拓扑、长周期及事件保持环境范围；原始样本 24 小时 · 分钟聚合 7 天 · 即时趋势显示最近原始样本，长周期趋势使用所选时间范围</Typography.Text></Space><MonitoringAlertPanel alerts={(latest?.alerts || []).map(a=>stale && (a.status==='active'||a.status==='pending')?{...a,status:'unknown'}:a)} rules={rules} dirty={!!editedRules} onChange={setEditedRules} onSave={()=>void saveRules()} onDiscard={()=>setEditedRules(undefined)} />{error && <Alert type="error" title={error} />}{latest && <Alert type={stale?'warning':'info'} title={`采样 ${new Date(latest.observed_at).toLocaleString()}${stale?' · 缓存已过期，请核查采集状态':''} · MMR 准确应用延迟未验证`} />}{!latest ? <Empty description={data?.enabled ? '等待后台首个样本' : '启用后展示真实数据'} /> : <MonitoringSections items={[
    { key: 'overview', label: '实例概况', children: <><Card title="实际成员与复制链路"><MonitoringTopology instances={latest.nodes.map(n=>({...n.node,role:!n.error && n.sections.runtime?.valid ? n.sections.runtime.rows?.[0]?.recovery ? '实际备库' : '实际主库' : '角色不可观测'}))} members={latest.resolved_topology?.members || []} links={displayedLinks} onSelect={l=>setSelectedLinkId(l.id)} />
      <Space wrap>{displayedLinks.map(l=><Button style={{whiteSpace:'normal',height:'auto',maxWidth:'100%'}} key={l.id} onClick={()=>setSelectedLinkId(l.id)}>{l.kind==='mmr'?'多活':'物理'} · {l.source || '源实例待确认'} → {l.target} · {l.status}</Button>)}</Space>
      <Typography.Paragraph type="secondary">点击链路打开完整证据；成员身份来自数据库，配置边与实际连接证据分别标注。存在连接不代表业务数据已一致。</Typography.Paragraph>
    </Card>{wrap(n => <><p>实际角色：{n.sections.runtime?.valid ? n.sections.runtime.rows?.[0]?.recovery ? '备库' : '主库' : '不可观测'}</p><Table section={n.sections.runtime} /></>)}</> },
    { key: 'load', label: '负载趋势', children: wrap(n=><>{['database','connections','wal','activity_summary'].filter(k=>!n.sections[k]?.valid).map(k=><Alert key={k} type="warning" title={`${labels[k]}：${n.sections[k]?.error || '不可观测'}`} />)}<DatabaseLoadCharts current={latest.database_metrics?.find(m=>m.node_id===n.node.id)} history={data?.history || []} nodeId={n.node.id} recovery={n.sections.runtime?.rows?.[0]?.recovery as boolean|undefined} /></>) },
    { key: 'sessions', label: '会话与锁', children: wrap(n=><>
      <Alert type="info" title="当前数据库最多 100 条阻塞关系及 100 个客户端会话；图中箭头从阻塞来源指向等待者，PID 0 表示预备事务。" />
      {n.sections.blocking?.valid && n.sections.blocking.rows?.length ? <SessionBlockingGraph relations={n.sections.blocking.rows} onSelect={pid=>{
        const session=n.sections.sessions?.rows?.find(r=>r.pid===pid);const relation=n.sections.blocking.rows?.find(r=>r.waiting_pid===pid || r.blocking_pid===pid);
        setSelectedSession({nodeId:n.node.id,pid,start:session?.backend_start || (relation?.waiting_pid===pid?relation.waiting_start:relation?.blocking_start)});
      }} /> : <Table section={n.sections.blocking} />}
      <Tabs items={['sessions','blocking','activity_summary'].map(k=>({key:k,label:labels[k],children:<Table section={n.sections[k]} />}))} />
    </>) },
    { key: 'physical', label: '物理流复制', children: wrap(n => <>{n.sections.senders?.valid ? n.sections.senders.rows?.filter(r=>r.slot_type!=='logical').map((r, i) => <Card size="small" key={i} title={`${text(r.application_name)} · ${text(r.state)} · ${text(r.sync_state)}`}><Space wrap>{[['当前 WAL', n.sections.runtime?.rows?.[0]?.wal_position], ['发送', r.sent_lsn], ['写入', r.write_lsn], ['刷盘', r.flush_lsn], ['回放', r.replay_lsn]].map(([label, v], j) => <span key={j}><strong>{text(label)}</strong><br />{text(v)}　{j < 4 && '→'}</span>)}</Space><p>write / flush / replay lag：{text(r.write_lag)} / {text(r.flush_lag)} / {text(r.replay_lag)}</p><p>NULL 不代表零延迟，空闲时不据此判断停滞。</p></Card>) : <Table section={n.sections.senders} />}<Table section={n.sections.receivers} /><ReplicationProgress samples={data?.history || []} current={latest} nodeId={n.node.id} kind="physical" /></>) },
    { key: 'members', label: '成员状态对照', children: <Space orientation="vertical" style={{width:'100%'}}>{latest.member_consensus?.length ? latest.member_consensus.map(m=><Card key={m.key} title={`${m.member_name} · ${m.status==='agreed'?'可观测主成员一致':m.status==='disagree'?'状态分歧':'证据不足'}`}><Table section={{valid:true,rows:m.observations}} /><Typography.Text type="secondary">只比较主成员观察者；证据不足不判健康，状态分歧不等同于脑裂。</Typography.Text></Card>) : <Empty description="暂无可比较的成员观察" />}</Space> },
    { key: 'mmr', label: 'MMR 有向链路', children: wrap(n => <>
      <Space wrap align="start">{n.sections.subscriptions?.rows?.map((r, i) => {
        const members = n.sections.members?.rows || [];
        const name = (id: unknown) => text(members.find(m => String(m.node_id) === String(id))?.node_name || id);
        const runtime = n.sections.subscription_runtime;
        const receiver = runtime?.rows?.find(x => String(x.subid) === String(r.sub_id) && x.relid === null);
        const metric = latest.mmr_metrics?.find(m=>m.node_id===n.node.id && String(m.sub_id)===String(r.sub_id));
        const origin = n.sections.origins?.rows?.find(x => x.external_id === r.origin_name);
        return <Card size="small" key={i} title={`${name(r.origin_node_id)} → ${name(r.target_node_id)}`}>
          <p>订阅：{text(r.sub_name)} · {r.sub_enabled ? '启用' : '禁用'}</p>
          <p>源端槽：{text(r.slot_name)} → 接收：{!runtime?.valid ? '不可观测' : receiver?.pid ? `PID ${text(receiver.pid)}` : '未见主接收进程'}</p>
          <p>received_lsn：{text(receiver?.received_lsn)}</p><p>origin remote_lsn：{text(origin?.remote_lsn)}</p>
          <p>应用模式：{metric?.apply_mode==='serial'?'串行配置与进程证据已确认':'并行或待确认'} · 可观测主应用进程：{metric?.observable_workers ?? '未知'}</p>
          <p>接收 → origin 坐标距离：{metric?.received_to_origin_bytes===null || metric?.received_to_origin_bytes===undefined ? '不可计算' : `${(metric.received_to_origin_bytes/1048576).toFixed(3)} MiB`}</p>
          <p>无恢复记录的历史错误：{metric?.error_records_without_recovery ?? '不可观测'} · 异常时间戳：{metric?.invalid_error_timestamps ?? '不可观测'}</p>
          <Typography.Text type="secondary">{metric?.evidence || '按当前实例观测，不宣称已追平；多 writer 的差值尚未验证。'}</Typography.Text>
        </Card>;
      })}</Space>
      <ReplicationProgress samples={data?.history || []} current={latest} nodeId={n.node.id} kind="logical" />
      <Tabs items={['members', 'subscriptions', 'subscription_runtime', 'apply_configuration', 'workers', 'origins', 'catchup'].map(k => ({key:k,label:labels[k],children:<Table section={n.sections[k]} />}))} />
    </>) },
    { key: 'history', label: 'WAL 保留趋势', children: wrap(n => <>{n.sections.slots?.rows?.map(r => <Card size="small" key={text(r.slot_name)} title={`${text(r.slot_name)} · ${text(r.slot_type)} · ${r.active ? 'active' : 'inactive'}`}><Chart samples={data?.history || []} nodeId={n.node.id} slot={String(r.slot_name)} /></Card>)}<Table section={n.sections.slots} /></>) },
    { key: 'conflicts', label: '冲突与行值', children: wrap(n => n.sections.conflicts?.valid && n.sections.conflicts.rows?.length ? n.sections.conflicts.rows.map((r, i) => <Conflict key={i} row={r} />) : <Table section={n.sections.conflicts} />) },
    { key: 'aggregates', label: '长周期趋势', children: <Space orientation="vertical" style={{width:'100%'}}>{data?.series?.length ? data.series.map(series=><Card key={series.key} title={series.label}><MonitoringTrend label={series.label} unit={series.unit} points={series.points} gapSeconds={Math.max(90,Math.ceil(windowMinutes/120)*90)} /><Typography.Text type="secondary">按有效样本聚合，缺失不填零；连接、启动周期及时间线分开。峰值保留，不冒充业务吞吐。</Typography.Text></Card>) : <Empty description="所选时间范围尚无有效聚合数据" />}</Space> },
    { key: 'hosts', label: '主机资源', children: <HostMonitoring hosts={latest.hosts || []} series={data?.series || []} gapSeconds={Math.max(90,Math.ceil(windowMinutes/120)*90)} /> },
    { key: 'queries', label: '查询排行', children: <MonitoringDiagnostics key={`${environmentId}:${detailNodeId}:queries`} environmentId={environmentId} nodes={latest.nodes.map(n=>n.node)} initialNodeId={detailNodeId} view="queries" /> },
    { key: 'tables', label: '表维护', children: <MonitoringDiagnostics key={`${environmentId}:${detailNodeId}:tables`} environmentId={environmentId} nodes={latest.nodes.map(n=>n.node)} initialNodeId={detailNodeId} view="tables" /> },
    { key: 'events', label: '观测事件', children: <Card title="主备角色、链路与告警变化"><Typography.Paragraph type="secondary">时间是后台发现变化的采样时间，不是故障精确发生时间；采集未知不代表节点宕机。</Typography.Paragraph>{data?.events?.length ? data.events.map((e,i)=><Card size="small" key={i} title={`${new Date(e.observed_at).toLocaleString()} · ${e.kind}`}><p>{e.subject}</p><p>{e.message}</p><Typography.Text type="secondary">{e.source || '数据库采样观察'}</Typography.Text>{e.task_id && <Button onClick={()=>onOpenTask(e.task_id!)}>查看部署任务</Button>}</Card>) : <Empty description="暂无观测变化事件" />}</Card> },
    { key: 'errors', label: '进程与恢复', children: details(['workers','errors']) }, { key: 'scope', label: '复制范围', children: details(['scope']) },
  ]} />}
  <Drawer title="复制链路证据" open={!!selectedLinkId} onClose={()=>setSelectedLinkId(undefined)} size="large">
    {!selectedLink ? <Empty description="当前采样中链路已变化，请重新选择" /> : <Space orientation="vertical" style={{width:'100%'}}>
      <Alert type={selectedLink.status==='connected'?'info':'warning'} title={`${selectedLink.source || '源实例待确认'} → ${selectedLink.target} · ${selectedLink.status}`} description={selectedLink.reason} />
      <Space><Button disabled={!sourceNode} onClick={()=>sourceNode && onOpenStudio(sourceNode.node.id)}>源实例 Studio</Button><Button disabled={!targetNode} onClick={()=>targetNode && onOpenStudio(targetNode.node.id)}>目标实例 Studio</Button></Space>
      <Card size="small" title="1 · 源端复制槽"><Table section={pick(sourceNode,'slots',r=>r.slot_name===selectedLink.slot_name)} /></Card>
      <Card size="small" title="2 · 源端发送进程"><Table section={selectedLink.sender_pid ? pick(sourceNode,'senders',r=>r.pid===selectedLink.sender_pid) : {valid:false,error:'无法唯一关联发送进程，未根据同机地址猜测'}} /></Card>
      {selectedLink.kind==='mmr' ? <>
        <Card size="small" title="3 · 目标订阅配置"><Table section={pick(targetNode,'subscriptions',r=>String(r.sub_id)===String(selectedLink.sub_id))} /></Card>
        <Card size="small" title="4 · 接收运行统计"><Table section={pick(targetNode,'subscription_runtime',r=>String(r.subid)===String(selectedLink.sub_id))} /></Card>
        <Card size="small" title="5 · Origin 提交处理进度"><Table section={pick(targetNode,'origins',r=>r.external_id===selectedLink.origin_name)} /><Typography.Text type="secondary">remote_lsn 与 local_lsn 属于不同坐标，不能相减；提交处理可能包含跳过。</Typography.Text></Card>
        <Card size="small" title="6 · 关联冲突与错误"><Tabs items={[{key:'errors',label:'错误及恢复',children:<Table section={pick(targetNode,'errors',r=>r.sub_name===selectedLink.sub_name)} />},{key:'conflicts',label:'冲突原始记录',children:<Table section={pick(targetNode,'conflicts',r=>String(r.sub_id)===String(selectedLink.sub_id))} />}]} /></Card>
      </> : <><Card size="small" title="3 · 备库接收来源"><Table section={pick(targetNode,'receivers',()=>true)} /></Card><Card size="small" title="4 · 备库回放位置"><Table section={targetNode?.sections.runtime} /></Card></>}
    </Space>}
  </Drawer><Drawer title="会话与等待证据" open={!!selectedSession} onClose={()=>setSelectedSession(undefined)} size="large">
    {selectedSession?.pid===0 ? <Alert type="info" title="PID 0 是预备事务阻塞来源，不是可以终止的后台进程；请在 Studio 中核查预备事务。" /> : sessionRows.length ? <Table section={{valid:true,rows:sessionRows}} /> : <Alert type="warning" title="当前样本中会话不存在、身份已变化或不在前 100 个结果内；不将复用的 PID 视为原会话。" />}
    {selectedSession && <Table section={pick(sessionNode,'blocking',r=>selectedSession.pid===0 ? r.blocking_pid===0 : !!selectedSession.start && ((r.waiting_pid===selectedSession.pid && r.waiting_start===selectedSession.start) || (r.blocking_pid===selectedSession.pid && r.blocking_start===selectedSession.start)))} />}
    <Button disabled={!sessionNode} onClick={()=>sessionNode && onOpenStudio(sessionNode.node.id)}>打开对应实例 Studio</Button>
  </Drawer></Space>;
}
