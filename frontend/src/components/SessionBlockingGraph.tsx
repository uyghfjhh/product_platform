import { ReactFlow, Background, Controls, MarkerType } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './monitoringTopology.css';
type Row=Record<string,unknown>;
export default function SessionBlockingGraph({ relations, onSelect }: {relations:Row[];onSelect:(pid:number)=>void}) {
  const ids=[...new Set(relations.flatMap(r=>[Number(r.blocking_pid),Number(r.waiting_pid)]).filter(Number.isFinite))];
  const waiting=new Set(relations.map(r=>Number(r.waiting_pid)));
  const roots=ids.filter(pid=>!waiting.has(pid));const others=ids.filter(pid=>waiting.has(pid));
  const nodes=ids.map(pid=>{const root=roots.includes(pid);return {id:String(pid),position:{x:root?0:330,y:(root?roots:others).indexOf(pid)*120},data:{label:<div><strong>{pid===0?'预备事务／PID 0':`PID ${pid}`}</strong><div>{root?'阻塞来源':'等待会话'}</div></div>},style:{width:220,background:'var(--bg-surface)',color:'var(--text-primary)',border:'1px solid var(--border-medium)',borderRadius:8}};});
  const edges=relations.map((r,i)=>({id:String(i),source:String(r.blocking_pid),target:String(r.waiting_pid),label:'阻塞',type:'smoothstep',markerEnd:{type:MarkerType.ArrowClosed},style:{stroke:'var(--color-fail)'},labelBgStyle:{fill:'var(--bg-surface)'},labelStyle:{fill:'var(--text-primary)'}}));
  return <div className="monitoring-topology" style={{height:350,width:'100%'}}><ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={false} nodesConnectable={false} edgesReconnectable={false} onNodeClick={(_,n)=>onSelect(Number(n.id))} proOptions={{hideAttribution:true}}><Background /><Controls showInteractive={false} /></ReactFlow></div>;
}
