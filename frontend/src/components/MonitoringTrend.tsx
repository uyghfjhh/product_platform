import { useEffect, useRef, useState } from 'react';
import { Empty } from 'antd';
export type TrendPoint = { time: string; value: number | null; peak?: number | null };
export default function MonitoringTrend({ points, unit, label, gapSeconds=45 }: { points: TrendPoint[]; unit: string; label: string; gapSeconds?: number }) {
  const ref=useRef<HTMLDivElement>(null);const [width,setWidth]=useState(600);
  useEffect(()=>{const element=ref.current;if(!element)return;const observer=new ResizeObserver(entries=>setWidth(Math.max(120,entries[0].contentRect.width)));observer.observe(element);return()=>observer.disconnect();},[]);
  const data=points.map(p=>({...p,t:Date.parse(p.time)}));
  const valid=data.filter(p=>p.value!==null && Number.isFinite(p.value) && Number.isFinite(p.t));
  if(valid.length<2)return <div ref={ref}><Empty description="等待至少两个有效样本" /></div>;
  const first=data[0].t,last=data[data.length-1].t,maximum=Math.max(1,...valid.flatMap(p=>[p.value!,p.peak??0]));
  const left=55,right=width-16,bottom=132;
  const x=(t:number)=>left+(t-first)/Math.max(1,last-first)*(right-left);
  const y=(v:number)=>bottom-v/maximum*95;
  const paths=(field:'value'|'peak')=>{let segment='',previous=0;const segments:string[]=[];for(const p of data){const v=p[field];if(v===null || v===undefined || !Number.isFinite(v) || p.t-previous>gapSeconds*1000){if(segment)segments.push(segment);segment='';}if(v!==null && v!==undefined && Number.isFinite(v))segment+=`${segment?'L':'M'}${x(p.t)},${y(v)} `;previous=p.t;}if(segment)segments.push(segment);return segments;};
  const peaks=data.some(p=>p.peak!==undefined);
  return <div ref={ref}><div style={{fontSize:12,color:'var(--text-secondary)',overflowWrap:'anywhere'}}>{new Date(first).toLocaleString()} — {new Date(last).toLocaleString()}</div>{peaks && <div style={{fontSize:12,color:'var(--text-secondary)'}}>实线：有效样本均值　虚线：有效样本峰值</div>}<svg viewBox={`0 0 ${width} 180`} style={{width:'100%'}} role="img" aria-label={`${label}，${unit}`}><text x="4" y="18" fill="currentColor" fontSize="12">{unit}</text><text x="4" y="42" fill="currentColor" fontSize="12">{maximum>=10000?maximum.toExponential(1):maximum.toFixed(1)}</text><text x="34" y="136" fill="currentColor" fontSize="12">0</text><path d={`M${left} 35V${bottom}H${right}`} fill="none" stroke="currentColor" opacity=".25" />{paths('value').map((p,i)=><path key={i} d={p} fill="none" stroke="var(--border-active)" strokeWidth="2" />)}{peaks && paths('peak').map((p,i)=><path key={'peak'+i} d={p} fill="none" stroke="var(--color-fail)" strokeDasharray="4 3" strokeWidth="1.5" />)}{valid.map((p,i)=><circle key={i} cx={x(p.t)} cy={y(p.value!)} r="3" fill="var(--border-active)"><title>{new Date(p.t).toLocaleString()} · 均值 {p.value!.toFixed(2)} {unit}{p.peak!==undefined?` · 峰值 ${p.peak}`:''}</title></circle>)}{width>=220 && <text x={left} y="167" fill="currentColor" fontSize="12">{new Date(first).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</text>}<text x={right} y="167" textAnchor="end" fill="currentColor" fontSize="12">{new Date(last).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</text></svg></div>;
}
