import { useMemo } from 'react';
import { ReactFlow, Background, Controls, MarkerType } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './monitoringTopology.css';
export type ObservedLink = { id: string; kind: string; source: string | null; target: string; status: string; reason: string; sub_id?: number; sub_name?: string; slot_name?: string; sender_pid?: number; origin_name?: string };
export type ObservedMember = { member_key: string; node_id: string; member_name: string; recovery: boolean | null };
type Instance = { id: string; label: string; host: string; port: number; role: string };
const statusNames: Record<string,string> = { connected:'连接证据存在',interrupted:'接收／槽中断',unknown:'待核查',ambiguous:'身份歧义',disabled:'已禁用' };
export default function MonitoringTopology({ instances, members, links, onSelect }: { instances: Instance[]; members: ObservedMember[]; links: ObservedLink[]; onSelect: (link: ObservedLink) => void }) {
  const {nodes,edges} = useMemo(()=>{
    const groups: string[]=[];const counts: Record<string,number>={};
    const nodes=instances.map(n=>{const member=members.find(m=>m.node_id===n.id);const group=member?.member_key || n.id;let column=groups.indexOf(group);if(column<0){groups.push(group);column=groups.length-1;}const row=counts[group]||0;counts[group]=row+1;
      return {id:n.id,position:{x:column*330,y:row*150},data:{label:<div><strong>{member?.member_name || n.label}</strong><div>{n.label} · {n.host}:{n.port}</div><div>{n.role}</div></div>},style:{width:260,background:'var(--bg-surface)',color:'var(--text-primary, #e5edf7)',border:'1px solid var(--border-medium)',borderRadius:8,fontSize:13}};
    });
    const ids=new Set(instances.map(n=>n.id));
    const edges=links.filter(l=>l.source && ids.has(l.source) && ids.has(l.target)).map(l=>({id:l.id,source:l.source!,target:l.target,label:`${l.kind==='mmr'?'多活':'物理'} · ${statusNames[l.status]||l.status}`,type:'smoothstep',markerEnd:{type:MarkerType.ArrowClosed},style:{stroke:l.status==='connected'?'var(--border-active)':'#dcad64',strokeDasharray:l.status==='connected'?undefined:'5 4'},labelStyle:{fill:'var(--text-primary, #e5edf7)',fontSize:12},labelBgStyle:{fill:'var(--bg-surface)'},data:{link:l}}));
    return {nodes,edges};
  },[instances,members,links]);
  return <div className="monitoring-topology" style={{height:430,width:'100%',minWidth:0}}><ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={false} nodesConnectable={false} edgesReconnectable={false} onEdgeClick={(_,edge)=>onSelect(edge.data!.link)} proOptions={{hideAttribution:true}}><Background /><Controls showInteractive={false} /></ReactFlow></div>;
}
