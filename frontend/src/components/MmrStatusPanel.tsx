import { Collapse, Drawer, Empty, Space, Table, Tag, Typography, Alert, Button } from 'antd';
import { memberText, mmrHint, type MmrStatus } from '../platform/mmr';

export default function MmrStatusPanel({open,onClose,data,error,loading,stale,onRefresh}: {
  open:boolean;onClose:()=>void;data:MmrStatus | null;error:string;loading:boolean;stale:boolean;onRefresh:()=>void;
}) {
  return <Drawer title={`MMR 状态${data?.group_name ? ` · ${data.group_name}` : ''}`} open={open} onClose={onClose} width={800}
    extra={<Button loading={loading} onClick={onRefresh}>重新检查</Button>}>
    {(error || stale) && <Alert type="warning" showIcon message={error || '上次检查结果已过期'}
      description={data ? '下列为上次采集结果，不代表当前状态。' : undefined} style={{marginBottom:12}} />}
    {data && <Typography.Paragraph type="secondary" style={{fontSize:12}}>
      数据库：{data.database} · {stale ? '上次检查' : '检查时间'}：{new Date(data.checked_at).toLocaleString()}
    </Typography.Paragraph>}
    {!data ? <Empty description={loading ? '正在检查 MMR 状态' : '尚未取得 MMR 状态'} /> : <>
      <Table rowKey="name" size="small" pagination={false} dataSource={data.members}
        locale={{emptyText:data.applicable ? '插件已安装，未发现 MMR 成员' : '当前数据库未安装 MMR'}}
        columns={[
          {title:'成员',dataIndex:'name',width:110},
          {title:'状态',render:(_,m)=><Tag color={stale || m.unknown ? 'default' : m.state==='ACTIVE' && !m.state_conflict ? 'green' : 'gold'}>{memberText(m)}{m.unknown ? ' · 未确认' : ''}</Tag>},
          {title:'问题',render:(_,m)=>m.issues.length ? <span style={{color:'#ff7875'}}>{mmrHint(m.issues[0].code)}{m.issues.length>1 ? `（${m.issues.length}条观测）` : ''}</span> : m.state_conflict ? '不同主库返回的状态不一致' : m.unknown ? '未取得完整诊断' : '—'},
        ]}
        expandable={{expandedRowRender:m=><div>
          {m.source && <Typography.Paragraph type="secondary">采集主库：{m.source.instance} · {m.source.host}:{m.source.port}</Typography.Paragraph>}
          <Typography.Paragraph type="secondary">登记状态：{m.registered_states.join(' / ') || '未知'}；实际状态：{m.states.join(' / ') || '未知'}</Typography.Paragraph>
          {m.issues.map((issue,index)=><Typography.Paragraph key={index} style={{marginBottom:8}}>
            <Tag color="error">{issue.code}</Tag>{issue.detail}<Typography.Text type="secondary">（来源：{issue.observer || '未知'}）</Typography.Text>
          </Typography.Paragraph>)}
        </div>}} />
      <Space direction="vertical" style={{width:'100%',marginTop:16}}>
        {data.observations.filter(o=>o.error).map((o,index)=><Alert key={index} type="warning" showIcon
          message={`${o.physical_group}：${o.error}`} />)}
      </Space>
      <Collapse style={{marginTop:16}} items={[{key:'evidence',label:'诊断 SQL 与原始结果',children:
        <Space direction="vertical" style={{width:'100%'}}>{data.observations.map((o,index)=><div key={index}>
          <Typography.Text strong>{o.physical_group} · {o.source?.instance || '未确认主库'}</Typography.Text>
          {o.evidence.map((entry,i)=><div key={i} style={{marginTop:10}}>
            <Typography.Text type="secondary">{entry.instance} · {entry.duration_seconds}s</Typography.Text>
            <pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{entry.sql}</pre>
            <pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{entry.error || JSON.stringify(entry.result,null,2)}</pre>
          </div>)}
        </div>)}</Space>}]} />
    </>}
  </Drawer>;
}
