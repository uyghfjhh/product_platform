type Props = { details: Record<string, unknown> };

export default function FbasecmanSceneDetails({ details }: Props) {
  return <>
    {Boolean(details.group_role) && <span>运行角色：{String(details.group_role)}</span>}
    {Boolean(details.primary) && <span>主节点：{String(details.primary)}</span>}
    {Boolean(details.candidate_type) && <span>候选类型：{String(details.candidate_type)}</span>}
    {Boolean(details.effective_role) && <span>有效角色：{String(details.effective_role)}</span>}
    {Boolean(details.effective_status) && <span>有效状态：{String(details.effective_status)}</span>}
    {Boolean(details.write_cluster) && <span>写集群：{String(details.write_cluster)}</span>}
    {Boolean(details.promoted_cluster) && <span>接管集群：{String(details.promoted_cluster)}</span>}
  </>;
}
