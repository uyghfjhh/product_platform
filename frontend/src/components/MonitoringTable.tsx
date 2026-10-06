import { Alert, Empty } from 'antd';
export type MonitoringRow=Record<string,unknown>;
export type MonitoringSection={valid:boolean;rows?:MonitoringRow[];error?:string};
export const monitorValue=(value:unknown)=>value===undefined?'未提供':value===null?'NULL':typeof value==='object'?JSON.stringify(value,null,2):String(value);
export default function MonitoringTable({section}:{section?:MonitoringSection}) {
  if(!section?.valid)return <Alert type="warning" title={section?.error || '不可观测'} />;
  if(!section.rows?.length)return <Empty description="查询有效，当前无记录" />;
  return <div style={{overflowX:'auto'}}><table style={{width:'100%',borderCollapse:'collapse'}}><thead><tr>{Object.keys(section.rows[0]).map(k=><th key={k} style={{textAlign:'left',padding:8}}>{k}</th>)}</tr></thead><tbody>{section.rows.map((row,i)=><tr key={i}>{Object.values(row).map((value,j)=><td key={j} style={{padding:8,verticalAlign:'top',whiteSpace:'pre-wrap',maxWidth:400,overflowWrap:'anywhere'}}>{monitorValue(value)}</td>)}</tr>)}</tbody></table></div>;
}
