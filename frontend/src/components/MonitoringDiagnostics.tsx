import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Empty, Select, Space, Typography } from 'antd';
import { api } from '../platform/api';
import MonitoringTable,{type MonitoringSection} from './MonitoringTable';
type Data={available:boolean;reason?:string;observed_at:string;sections:Record<string,MonitoringSection>};
export default function MonitoringDiagnostics({environmentId,nodes,initialNodeId,view}:{environmentId:string;nodes:{id:string;label:string}[];initialNodeId:string;view:'queries'|'tables'}) {
  const [node,setNode]=useState(initialNodeId || nodes[0]?.id || '');const [data,setData]=useState<Data>();const [error,setError]=useState('');const [loading,setLoading]=useState(false);const request=useRef<AbortController|null>(null);
  useEffect(()=>{setData(undefined);setError('');setLoading(false);return()=>request.current?.abort();},[environmentId,node,view]);
  const load=async()=>{request.current?.abort();const controller=new AbortController();request.current=controller;setLoading(true);setError('');try {const value=await api<Data>(`/environments/${encodeURIComponent(environmentId)}/monitoring/nodes/${encodeURIComponent(node)}/diagnostics/${view}`,{signal:controller.signal});if(!controller.signal.aborted)setData(value);}catch(e){if(!controller.signal.aborted)setError(String(e));}finally{if(!controller.signal.aborted)setLoading(false);}};
  return <Space orientation="vertical" style={{width:'100%'}}><Space wrap><Select aria-label={view==='queries'?'查询诊断实例':'表维护实例'} value={node} options={nodes.map(n=>({value:n.id,label:n.label}))} onChange={setNode} /><Button loading={loading} disabled={!node} onClick={()=>void load()}>{view==='queries'?'读取查询排行':'读取表维护'}</Button></Space>
    <Typography.Paragraph type="secondary">{view==='queries'?'从现有 pg_stat_statements 读取当前数据库累计执行耗时前 30 条。均值不是 P95；不自动安装扩展或修改预加载。':'按死元组估计数读取前 30 张表，并显示正在执行的 Vacuum；死元组估计不是膨胀率，监控不执行维护操作。'}</Typography.Paragraph>
    {error && <Alert type="error" title={error} />}{data?.available===false && <Alert type="info" title={data.reason} />}{!data && !loading && <Empty description="选择实例并读取诊断" />}
    {data?.available && <><Typography.Text type="secondary">读取时间：{new Date(data.observed_at).toLocaleString()}</Typography.Text>
      {view==='queries' && data.sections.ranking?.rows?.map((row,i)=><Card size="small" key={i} title={`${i+1} · queryid ${row.queryid}`}><div style={{height:8,background:'var(--bg-surface-elevated)',marginBottom:10}}><div style={{height:'100%',background:'var(--border-active)',width:`${100*Number(row.total_exec_time)/Math.max(1,...data.sections.ranking!.rows!.map(r=>Number(r.total_exec_time)))}%`}} /></div><Typography.Text>{Number(row.total_exec_time).toFixed(2)} ms 累计 · {String(row.calls)} 次 · {Number(row.mean_exec_time).toFixed(2)} ms 均值</Typography.Text><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{String(row.query)}</pre></Card>)}
      {Object.entries(data.sections).map(([name,section])=><Card key={name} title={name==='ranking'?'查询原始统计':name==='reset'?'统计重置信息':name==='vacuum'?'Vacuum 执行进度':'表维护原始统计'}><MonitoringTable section={section} /></Card>)}
    </>}
  </Space>;
}
