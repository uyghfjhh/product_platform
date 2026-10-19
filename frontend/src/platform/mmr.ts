export type MmrIssue = {code:string; detail:string; observer:string | null};
export type MmrMember = {name:string; state:string | null; registered_states:string[]; local_registered_state?:string; states:string[];
  issues:MmrIssue[]; source:{instance:string; host:string; port:number} | null; physical_group:string | null;
  unknown:boolean; state_conflict:boolean};
export type MmrObservation = {physical_group:string; database:string; source:{instance:string;host:string;port:number} | null;
  installed:boolean; local_member:string | null; metadata:unknown[]; records:unknown[]; error:string | null;
  evidence:{instance:string;sql:string;result:unknown;error:string | null;duration_seconds:number}[]};
export type MmrStatus = {applicable:boolean; group_name:string | null; database:string; checked_at:string;
  members:MmrMember[]; observations:MmrObservation[]; issue_count:number; unknown:boolean};
export function mmrHint(code:string):string {
  return ({ERR_NODE_SET_MODE:'发布变更未完成',ERR_SUB_RUN:'订阅运行异常',CONNECT_FAIL:'成员连接失败',
    ERR_PUB_TABLES:'发布表不一致',ERR_TABLE_NOEXIST:'业务表缺失',ERR_TABLE_COLNUM:'表结构不一致',
    ERR_TABLE_NOSAMPLE:'表结构不一致',ERR_PUB_TABLE_REPINDEX:'缺少复制标识',ERR_SLOT_STATE:'复制槽状态异常',
    ERR_SLOT_EXIST:'复制槽异常',ERR_SUB_EXIST:'订阅缺失',ERR_SUB_TABLE_STATE:'订阅表同步异常',
    ERR_NODE_STATE:'成员状态不一致',DIFF_ClUSTER:'集群身份不一致',ERR_NODE_VERSION:'成员版本不一致',
    ERR_CONF_LACK:'必要配置缺失',TIME_DIFF:'节点时间偏差'} as Record<string,string>)[code] || code;
}
export function memberText(member:MmrMember):string {
  if (member.state_conflict) return '状态不一致';
  return member.state || (member.local_registered_state ? `${member.local_registered_state}（登记）` : member.registered_states.length===1 ? `${member.registered_states[0]}（登记）` : '未知');
}
