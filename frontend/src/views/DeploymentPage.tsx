import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, App, Button, Drawer, Dropdown, Empty, Input, Segmented, Space, Tag, Tooltip, Typography, Table } from 'antd';
import {
  CloudServerOutlined, ReloadOutlined, CopyOutlined, MoreOutlined, DatabaseOutlined,
  PlusOutlined, SettingOutlined, EditOutlined, DeploymentUnitOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons';

import { api, post, operationRequest, type Action, type Environment, type Product, type Task } from '../platform/api';
import type { TopologyData, TopologyNode } from '../platform/topology';
import MmrStatusPanel from '../components/MmrStatusPanel';
import { memberText, mmrHint, type MmrStatus } from '../platform/mmr';
import SharedDeploymentCanvas from '../components/DeploymentCanvas';
import EnvironmentModal from '../components/EnvironmentModal';
import DeploymentWizard from '../components/DeploymentWizard';
import ExecutionTerminal from '../components/ExecutionTerminal';
import { deploymentAdapter, deploymentFrontend } from '../products/deploymentRegistry';

type Props = {
  product: Product | undefined;
  environment: Environment | undefined;
  environments?: Environment[];
  products?: Product[];
  onSelectEnvironment?: (id: string) => void;
  openTask: (taskId: string) => void;
  reload: () => Promise<void>;
  onOpenDatabase?: (node?: TopologyNode, view?: 'sql') => void;
};


export default function DeploymentPage({
  product,
  environment,
  environments = [],
  products = [],
  onSelectEnvironment,
  openTask,
  reload,
  onOpenDatabase,
}: Props) {
  const { message, modal } = App.useApp();
  const [actions, setActions] = useState<Action[]>([]);
  const [topology, setTopology] = useState<TopologyData | null>(null);
  const [topologyError, setTopologyError] = useState('');
  const [observed, setObserved] = useState<Record<string, { running: boolean | null; message: string; pid?: number | null }> | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null);
  const [envModalOpen, setEnvModalOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardEnvironment, setWizardEnvironment] = useState<Environment | undefined>();
  const [envEditing, setEnvEditing] = useState<Environment | null>(null);
  const [terminalTaskId, setTerminalTaskId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // 单节点右侧抽屉：SQL 快速探针控制台状态与快捷诊断预设
  const [probeSql, setProbeSql] = useState('SELECT pg_is_in_recovery();');
  const [probeExecuting, setProbeExecuting] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [probeResult, setProbeResult] = useState<Array<Record<string, any>> | null>(null);
  const [probeDuration, setProbeDuration] = useState<number | null>(null);

  useEffect(() => {
    // 选中新节点时重置探针输出与耗时，保留输入内容
    setProbeError(null);
    setProbeResult(null);
    setProbeDuration(null);
  }, [selectedNode?.id]);

  const PROBE_PRESETS = [
    { label: '🔍 主备判定', sql: 'SELECT pg_is_in_recovery();', desc: '查询当前节点是否处于只读流复制恢复状态 (false 为主库，true 为备库)' },
    { label: '📊 复制状态', sql: 'SELECT client_addr, state, sync_state, replay_lsn FROM pg_stat_replication;', desc: '查询主库向从库发送的流复制连接与回放进度' },
    { label: '⏱️ 实例版本', sql: 'SELECT version();', desc: '查询当前实例 PostgreSQL/内核编译版本' },
    { label: '👥 活动连接', sql: 'SELECT count(*), state FROM pg_stat_activity GROUP BY state;', desc: '统计各连接状态的客户端连接数' },
  ];

  const runProbeSql = async (sqlToRun?: string) => {
    const sql = (sqlToRun ?? probeSql).trim();
    if (!sql || !environment || !selectedNode) return;
    setProbeExecuting(true);
    setProbeError(null);
    setProbeResult(null);
    const start = performance.now();
    try {
      const [err, res] = await post<[ { message?: string } | null, Record<string, any>[] | null ]>(
        `/environments/${encodeURIComponent(environment.id)}/studio/nodes/${encodeURIComponent(selectedNode.id)}`,
        { procedure: 'query', query: { sql } },
      );
      setProbeDuration(Math.round(performance.now() - start));
      if (err) {
        setProbeError(err.message || '查询失败');
      } else {
        setProbeResult(res || []);
      }
    } catch (cause) {
      setProbeDuration(Math.round(performance.now() - start));
      setProbeError((cause as Error).message);
    } finally {
      setProbeExecuting(false);
    }
  };
  // adapter 工厂每次调用返回新对象——必须 memo，否则下方 effect 依赖每轮渲染都变，
  // 造成 topology/status 无限 refetch 且 setObserved(null) 把已取回的状态清空。
  const productAdapter = useMemo(() => deploymentAdapter(product), [product?.id]);
  // 2D 画布是平台中立能力：产品未提供自有画布时使用共享实现
  const ProductCanvas = useMemo(() => deploymentFrontend(product)?.DeploymentCanvas, [product?.id]);
  useEffect(() => {
    if (!environment) { setActions([]); setTopology(null); setObserved(null); return; }
    setObserved(null);
    void Promise.all([
      api<Action[]>(`/environments/${encodeURIComponent(environment.id)}/actions`),
      api<TopologyData>(`/environments/${encodeURIComponent(environment.id)}/topology`).then((value) => { setTopologyError(''); return value; }).catch((error) => { setTopologyError(error.message); return null; }),
    ]).then(([list, graph]) => {
      setActions(list.filter((item) => item.capability === 'deployment'));
      setTopology(graph);
    }).catch((error) => message.error(error.message));
  }, [environment, productAdapter.profilePath, message]);

  const [mmrData,setMmrData] = useState<MmrStatus | null>(null);
  const [mmrError,setMmrError] = useState('');
  const [mmrLoading,setMmrLoading] = useState(false);
  const [mmrOpen,setMmrOpen] = useState(false);
  const [mmrStale,setMmrStale] = useState(false);
  const mmrRequest = useRef(0);
  const mmrInFlight = useRef(false);
  const hasMmr = topology?.kind === 'mmr' || topology?.nodes.some(n => n.installed_extensions?.includes('fdd_mmr') || n.extensions?.includes('fdd_mmr'));
  async function refreshMmr() {
    if (!environment || !hasMmr || mmrInFlight.current) return;
    const request = ++mmrRequest.current;
    mmrInFlight.current = true; setMmrLoading(true);
    try {
      const value = await api<MmrStatus>(`/environments/${encodeURIComponent(environment.id)}/mmr-status`);
      if (request !== mmrRequest.current) return;
      setMmrData(value); setMmrError(''); setMmrStale(false);
      // Role changes outside the UI must not leave the canvas on the former primary.
      setTopology(previous => {
        if (!previous) return previous;
        const primaries=new Map(value.observations.filter(o=>o.source).map(o=>[o.physical_group,o.source!.instance]));
        if (!previous.nodes.some(n=>primaries.has(n.group || '') && (n.role==='primary') !== (primaries.get(n.group || '')===n.id))) return previous;
        const nodes=previous.nodes.map(n=>primaries.has(n.group || '') ? {...n,role:primaries.get(n.group || '')===n.id?'primary':'standby'} : n);
        const edges=previous.edges.filter(e=>e.kind!=='streaming' || !primaries.has(previous.nodes.find(n=>n.id===e.source)?.group || ''));
        for (const [group,primary] of primaries) for (const node of nodes.filter(n=>n.group===group && n.id!==primary)) {
          edges.push({id:`stream:${primary}:${node.id}`,source:primary,target:node.id,kind:'streaming'});
        }
        const oldNodes=new Map(previous.nodes.map(n=>[n.id,n]));
        return {...previous,nodes,edges:edges.map(e=>e.kind==='mmr'?{...e,
          source:primaries.get(oldNodes.get(e.source)?.group || '') || e.source,
          target:primaries.get(oldNodes.get(e.target)?.group || '') || e.target}:e)};
      });
    } catch (cause) {
      if (request !== mmrRequest.current) return;
      setMmrError((cause as Error).message);setMmrStale(true);
    } finally {
      if (request === mmrRequest.current) {setMmrLoading(false);mmrInFlight.current=false;}
    }
  }
  useEffect(() => {
    ++mmrRequest.current; mmrInFlight.current=false;
    setMmrData(null);setMmrError('');setMmrStale(false);setMmrOpen(false);setMmrLoading(false);
    if (!hasMmr) return;
    void refreshMmr();
    const timer=window.setInterval(() => {if (!document.hidden) void refreshMmr();},30000);
    return () => {window.clearInterval(timer);++mmrRequest.current;mmrInFlight.current=false;};
  }, [environment?.id,environment?.deployment_config,hasMmr]);
  useEffect(() => {
    if (!mmrData) return;
    const timer=window.setTimeout(()=>setMmrStale(true),Math.max(0,60000-(Date.now()-Date.parse(mmrData.checked_at))));
    return ()=>window.clearTimeout(timer);
  },[mmrData]);

  async function refreshStatus(silent = false) {
    if (!environment) return;
    if (!silent) {setStatusLoading(true);void refreshMmr();}
    try {
      const next = await api<Record<string, { running: boolean | null; message: string; pid?: number | null }>>(
        `/environments/${encodeURIComponent(environment.id)}/topology/status`);
      // Reuse the previous object when nothing changed — downstream canvases
      // rebuild DOM/WebGL scenes on identity, so an identical poll must be free.
      setObserved((prev) => {
        if (prev && JSON.stringify(prev) === JSON.stringify(next)) return prev;
        return next;
      });
    } catch (error) {
      if (!silent) message.error((error as Error).message);
    } finally { if (!silent) setStatusLoading(false); }
  }

  useEffect(() => {
    if (!environment?.deployment_config) return;
    void refreshStatus(true);
    const timer = window.setInterval(() => void refreshStatus(true), 15000);
    return () => window.clearInterval(timer);
  }, [environment?.id, environment?.deployment_config]);

  const [roleBusy, setRoleBusy] = useState(false);
  const [roleTerminal, setRoleTerminal] = useState(false);
  useEffect(() => {
    setRoleBusy(false); setRoleTerminal(false); setTerminalTaskId(null);
    if (!environment) return;
    let active = true;
    const saved = localStorage.getItem(`role-task:${environment.id}`);
    if (saved) void api<Task>(`/operations/${encodeURIComponent(saved)}`).then(task => {
      if (!active || task.environment_id !== environment.id) return;
      setRoleTerminal(true); setTerminalTaskId(task.id);
      setRoleBusy(['QUEUED','RUNNING','CANCELLING'].includes(task.status));
    }).catch(() => {});
    return () => { active = false; };
  }, [environment?.id]);
  async function runRole(actionId: string) {
    if (!environment || !selectedNode?.group) return;
    const node = selectedNode;
    setLoading(true);
    try {
      type RoleNode = { id: string; host: string; port: number; data_dir: string; role: string; running: boolean };
      const info = await api<{current_primary: string; original_primary: string; old_primary: string | null; nodes: RoleNode[]}>(
        `/environments/${encodeURIComponent(environment.id)}/role-change?group=${encodeURIComponent(node.group!)}`);
      const primary = info.nodes.find(n => n.id === info.current_primary)!;
      const selected = info.nodes.find(n => n.id === node.id)!;
      const rebuilding = actionId === 'deployment.rejoin';
      if (rebuilding ? info.old_primary !== node.id : selected?.role !== 'standby') {
        throw new Error(rebuilding ? '所选节点不是待重建的旧主库，请刷新拓扑' : '所选节点已不再是备库，请刷新拓扑');
      }
      const title = actionId === 'deployment.failover' ? '备库升主'
        : rebuilding ? '重建旧主为备库' : actionId === 'deployment.restore' ? '切回原主库' : '主备切换';
      const confirmed = await new Promise<boolean>(resolve => modal.confirm({
        title,
        content: <div>
          <p>复制组：{node.group}</p>
          <p>当前主库：{primary.id}（{primary.host}:{primary.port}，{primary.running ? '运行中' : '已停止'}）</p>
          <p>选中节点：{selected.id}（{selected.host}:{selected.port}）</p>
          <p>{rebuilding ? `从 ${primary.id} 重新备份数据，将 ${selected.id} 重建为备库。`
            : `平台将停止 ${primary.id}，并将选中的 ${selected.id} 提升为主库。`}</p>
          {actionId === 'deployment.failover'
            ? <p>旧主 {primary.id} 保持停止，需要另行执行“重建旧主为备库”。</p>
            : <p>{rebuilding ? `从 ${primary.id} 重新备份数据。` : `提升成功后，从 ${selected.id} 重新备份数据，重建 ${primary.id} 为备库。`}将替换 {rebuilding ? selected.id : primary.id} 的数据目录：<code>{rebuilding ? selected.data_dir : primary.data_dir}</code>，完成后作为备库运行。</p>}
          <p>切换会中断连接；会检查复制位置，但不承诺零数据丢失。不会修改 fbasecman 配置。</p>
        </div>,
        okText: rebuilding ? `重建 ${selected.id} 为备库` : `停止 ${primary.id}，提升 ${selected.id}`,
        cancelText: '取消', okButtonProps: { danger: true },
        onOk: () => resolve(true), onCancel: () => resolve(false),
      }));
      if (!confirmed) return;
      const task = await operationRequest(environment.id, actionId, `streaming.${node.group}`,
        { selected_node: node.id, expected_primary: info.current_primary }, true);
      localStorage.setItem(`role-task:${environment.id}`, task.id);
      setRoleTerminal(true); setRoleBusy(true); setTerminalTaskId(task.id);
    } catch (cause) { message.error((cause as Error).message); }
    finally { setLoading(false); }
  }

  async function run(action: Action, target?: string) {
    if (!environment) return;
    if (action.changes_environment) {
      const confirmed = await new Promise<boolean>((resolve) => {
        modal.confirm({
          title: action.title,
          content: `目标：${target || environment.deployment_target || environment.id}。此操作将修改 ${environment.title}。`,
          okText: '执行', cancelText: '取消',
          onOk: () => resolve(true), onCancel: () => resolve(false),
        });
      });
      if (!confirmed) return;
    }
    setLoading(true);
    try {
      const task = await operationRequest(environment.id, action.id, target, {}, action.changes_environment);
      setRoleTerminal(false); setTerminalTaskId(task.id);
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function handleOpenSqlWorkbench(node: TopologyNode) {
    onOpenDatabase?.(node, 'sql');
  }

  function copyText(text: string) {
    navigator.clipboard.writeText(text);
    message.success('已复制到剪贴板');
  }

  return (
    <div className={productAdapter.workspaceClass}>
      {environment?.desired_deployment_plan_id && environment.deployment_status !== 'APPLIED' && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
          message="配置已关联，尚未完成部署验收"
          description={environment.deployment_status === 'PENDING'
            ? '部署申请等待执行或验收；当前配置不代表实例已完成部署。'
            : '上次部署未通过验收，请查看任务结果并重新检查方案。'} />
      )}
      {/* Multi-Environment Switcher & Asset Bar (Row 1) */}
      <div
        className="deployment-env-bar"
        style={{
          background: 'var(--bg-surface)',
          padding: '6px 12px',
          borderRadius: 8,
          border: '1px solid var(--border-subtle)',
          marginBottom: 10,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 0, overflowX: 'auto' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--text-secondary)', fontSize: 13, flexShrink: 0 }}>
            <CloudServerOutlined style={{ color: '#38bdf8' }} />
            <span>环境:</span>
          </span>
          {environments.length > 0 && (
            <Segmented
              size="middle"
              style={{ maxWidth: '100%' }}
              value={environment?.id}
              onChange={(val) => onSelectEnvironment?.(String(val))}
              options={environments.map((env) => ({
                value: env.id,
                label: (
                  <span style={{ padding: '2px 8px', fontSize: 13, fontWeight: 500 }}>
                    {env.title}
                  </span>
                ),
              }))}
            />
          )}
        </div>

        <Space size={8} style={{ flexShrink: 0 }}>
          <Button
            size="small"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => { setEnvEditing(null); setEnvModalOpen(true); }}
          >
            新增环境
          </Button>
          {environment && (
            <Dropdown
              menu={{
                items: [
                  {
                    key: 'edit',
                    icon: <EditOutlined />,
                    label: '编辑当前环境',
                    onClick: () => { setEnvEditing(environment); setEnvModalOpen(true); },
                  },
                  {
                    key: 'wizard',
                    icon: <DeploymentUnitOutlined />,
                    label: '配置部署方案',
                    onClick: () => { setWizardEnvironment(environment); setWizardOpen(true); },
                  },
                ],
              }}
            >
              <Button size="small" icon={<SettingOutlined />}>
                集群设置
              </Button>
            </Dropdown>
          )}
        </Space>
      </div>

      {!environment ? <Empty description="先在产品与环境页登记环境" /> : <>
        {!environment.deployment_config ? (
          <Alert
            type="info"
            showIcon
            message="该环境尚未关联部署配置"
            description="点击右侧“配置部署方案”生成或导入配置，系统会自动关联当前环境并呈现拓扑。"
            action={
              <Button size="small" type="primary" onClick={() => { setWizardEnvironment(environment); setWizardOpen(true); }}>
                配置部署方案
              </Button>
            }
          />
        ) : <>
          <section className="work-section cman-topology-section deployment-topology">
            <div
              className="deployment-topology-heading"
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 12,
                padding: '8px 14px',
                background: 'var(--bg-surface)',
                borderRadius: 8,
                border: '1px solid var(--border-subtle)',
                marginBottom: 10,
              }}
            >
              {/* Left: Topology Metrics & Real-time Health */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <Typography.Text strong style={{ fontSize: 14 }}>集群拓扑</Typography.Text>
                {topology && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {topology.nodes.length} 节点
                  </Typography.Text>
                )}
                {topology && (
                  <Tag
                    color={observed ? 'default' : 'processing'}
                    style={{
                      margin: 0,
                      fontSize: 12,
                      padding: '1px 8px',
                      background: 'var(--bg-surface-elevated)',
                      border: '1px solid var(--border-medium)',
                    }}
                  >
                    {observed ? (
                      <>
                        <span style={{ color: '#52c41a' }}>●</span> 在线 {topology.nodes.filter((node) => observed[node.id]?.running === true).length}
                        <span style={{ margin: '0 6px', opacity: 0.3 }}>|</span>
                        <span style={{ color: '#ff4d4f' }}>●</span> 停止 {topology.nodes.filter((node) => observed[node.id]?.running === false).length}
                        {topology.nodes.some((node) => observed[node.id]?.running == null) && (
                          <>
                            <span style={{ margin: '0 6px', opacity: 0.3 }}>|</span>
                            <span style={{ color: '#8c8c8c' }}>●</span> 未知 {topology.nodes.filter((node) => observed[node.id]?.running == null).length}
                          </>
                        )}
                      </>
                    ) : '正在探测…'}
                  </Tag>
                )}
              </div>

              {/* Right: Operational Actions, Workspace Entry, Refresh */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <Space size={6}>
                  {(['start', 'stop', 'restart'] as const).map((name) => {
                    const action = actions.find((item) => item.id === `deployment.${name}`);
                    const label = name === 'start' ? '▶ 启动' : name === 'stop' ? '⏹ 停止' : '🔄 重启';
                    return (
                      <Button
                        key={name}
                        size="small"
                        danger={name === 'stop'}
                        disabled={!action || loading || roleBusy}
                        onClick={() => { if (action) void run(action); }}
                      >
                        {label}
                      </Button>
                    );
                  })}
                  <Dropdown
                    menu={{
                      items: [
                        ['doctor', '环境体检'],
                        ['heal', '自愈修复'],
                        ['reset', '重置集群'],
                        ['clean', '清理集群'],
                      ].map(([name, label]) => ({
                        key: name,
                        label,
                        danger: name === 'clean' || name === 'reset',
                        disabled: loading || roleBusy || !actions.some((action) => action.id === `deployment.${name}`),
                      })),
                      onClick: ({ key }) => {
                        const action = actions.find((item) => item.id === `deployment.${key}`);
                        if (action) void run(action);
                      },
                    }}
                  >
                    <Button size="small" icon={<MoreOutlined />}>更多操作</Button>
                  </Dropdown>
                </Space>

                <div style={{ width: 1, height: 16, background: 'var(--border-subtle)', margin: '0 4px' }} />

                <Space size={6}>
                  {hasMmr && <Button size="small" onClick={()=>setMmrOpen(true)}>
                    MMR 状态{mmrStale || mmrError || mmrData?.unknown ? ' · 未确认' : mmrData?.issue_count ? ` · ${mmrData.issue_count}项异常` : ''}
                  </Button>}
                  {onOpenDatabase && (
                    <Button
                      size="small"
                      type="primary"
                      ghost
                      icon={<DatabaseOutlined />}
                      onClick={() => onOpenDatabase()}
                    >
                      数据库管理
                    </Button>
                  )}
                  <Button
                    size="small"
                    loading={statusLoading}
                    icon={<ReloadOutlined />}
                    onClick={() => void refreshStatus()}
                    title="刷新拓扑真实状态"
                  >
                    刷新状态
                  </Button>
                </Space>
              </div>
            </div>
            {topology ? (() => {
              const Canvas = ProductCanvas ?? SharedDeploymentCanvas;
              return <Canvas topology={topology} observed={observed} mmrStatus={mmrData} mmrStale={mmrStale} onOpenMmr={()=>setMmrOpen(true)}
                onSelectNode={setSelectedNode} onOpenSql={handleOpenSqlWorkbench}
                onDeploy={() => { setWizardEnvironment(environment); setWizardOpen(true); }} />;
            })() : <Alert type="warning" showIcon message="拓扑暂不可显示"
              description={topologyError || '检查部署配置和目标名称'} />}
            {hasMmr && <div aria-label="MMR 成员状态" className="deployment-mmr-summary">
              <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:6,gap:12}}>
                <Typography.Text style={{color:'#94a3b8',fontSize:12,fontWeight:500}}>MMR 双活{mmrData?.group_name ? ` · ${mmrData.group_name}` : ''}</Typography.Text>
                {mmrStale || mmrError ? <Typography.Text type="warning">上次结果已过期 / 状态未知</Typography.Text> :
                  <Typography.Text type="secondary" style={{fontSize:11}}>{mmrLoading?'检查中…':mmrData ? '点击成员查看详情' : '尚未取得状态'}</Typography.Text>}
              </div>
              <Table size="small" rowKey="name" pagination={false} loading={mmrLoading && !mmrData}
                dataSource={mmrData?.members || []} style={{maxWidth:680}}
                locale={{emptyText:mmrError || (mmrLoading?'正在检查 MMR':mmrData?.applicable?'未初始化 / 未确认':'尚未取得状态')}}
                onRow={()=>({onClick:()=>setMmrOpen(true),style:{cursor:'pointer'}})}
                columns={[
                  {title:'MMR 节点',dataIndex:'name',width:120,render:name=><Typography.Text>{name}</Typography.Text>},
                  {title:'当前实例',width:110,render:(_,m)=>m.source?.instance || '未确认'},
                  {title:'状态',width:150,render:(_,m)=><Tag color={mmrStale || m.unknown?'default':m.state==='ACTIVE' && !m.state_conflict?'green':'gold'}>{memberText(m)}</Tag>},
                  {title:'问题',render:(_,m)=>m.issues.length ? <Typography.Text type="warning">{mmrHint(m.issues[0].code)}{m.issues.length>1?'（另有详情）':''}</Typography.Text> :
                    mmrStale?'结果已过期':m.state_conflict?'各主库观测不一致':m.unknown?'未取得完整诊断':'—'},
                ]} />
            </div>}
          </section>
        </>}
      </>}

      {/* Node Inspector Drawer */}
      <Drawer
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span>{selectedNode?.label || '节点详情'}</span>
            {selectedNode && (
              <Tag color={selectedNode.role === 'primary' ? 'gold' : 'blue'}>
                {selectedNode.role.toUpperCase()}
              </Tag>
            )}
            {selectedNode && (
              <Tag color={observed?.[selectedNode.id]?.running === false ? 'error'
                : observed?.[selectedNode.id]?.running === true ? 'success' : 'default'}>
                {observed?.[selectedNode.id]?.running === false ? '已停止'
                  : observed?.[selectedNode.id]?.running === true ? '运行中' : '探测中'}
              </Tag>
            )}
          </div>
        }
        open={!!selectedNode}
        onClose={() => setSelectedNode(null)}
        width={420}
      >
        {selectedNode && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            {/* Quick PSQL Connection Snippet */}
            <div className="psql-snippet-card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <span className="psql-snippet-label">PSQL 直连命令行</span>
                <Button
                  size="small"
                  type="text"
                  className="psql-snippet-copy"
                  icon={<CopyOutlined />}
                  onClick={() => copyText(`psql -h ${selectedNode.host} -p ${selectedNode.port} -U ${environment?.database_user || 'postgres'}`)}
                >
                  复制
                </Button>
              </div>
              <code className="psql-snippet">
                psql -h {selectedNode.host} -p {selectedNode.port} -U {environment?.database_user || 'postgres'}
              </code>
              {onOpenDatabase && (
                <Button
                  type="primary"
                  style={{ width: '100%', marginTop: 10 }}
                  icon={<DatabaseOutlined />}
                  onClick={() => onOpenDatabase(selectedNode)}
                >
                  进入完整数据库 Studio
                </Button>
              )}
            </div>

            {/* In-Place Quick SQL Probe Console */}
            <div className="node-sql-probe-card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <span style={{ fontWeight: 600, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span>⚡</span> SQL 探针控制台
                </span>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  支持 <kbd style={{ padding: '1px 4px', background: 'rgba(255,255,255,0.08)', borderRadius: 3 }}>Ctrl+Enter</kbd>
                </Typography.Text>
              </div>

              {/* Preset Diagnostic Pills */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                {PROBE_PRESETS.map((preset) => (
                  <button
                    key={preset.label}
                    type="button"
                    className="probe-preset-pill"
                    title={`${preset.desc}\n${preset.sql}`}
                    onClick={() => {
                      setProbeSql(preset.sql);
                      void runProbeSql(preset.sql);
                    }}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>

              {/* SQL Text Area */}
              <Input.TextArea
                value={probeSql}
                onChange={(e) => setProbeSql(e.target.value)}
                onKeyDown={(e) => {
                  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    e.preventDefault();
                    void runProbeSql();
                  }
                }}
                autoSize={{ minRows: 2, maxRows: 5 }}
                placeholder="输入 SQL 语句，例如 SELECT version();"
                style={{
                  fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace',
                  fontSize: 12,
                  background: '#090f1c',
                  color: '#e2e8f0',
                  borderColor: '#2d4567',
                }}
              />

              {/* Action Bar */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }}>
                <Space size={6}>
                  <Button
                    type="primary"
                    size="small"
                    icon={<PlayCircleOutlined />}
                    loading={probeExecuting}
                    onClick={() => void runProbeSql()}
                  >
                    执行探针
                  </Button>
                  <Button
                    size="small"
                    onClick={() => {
                      setProbeSql('');
                      setProbeResult(null);
                      setProbeError(null);
                    }}
                  >
                    清空
                  </Button>
                </Space>
                {probeDuration !== null && (
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    耗时 {probeDuration}ms {probeResult ? `· ${probeResult.length} 行` : ''}
                  </Typography.Text>
                )}
              </div>

              {/* Result Area */}
              {probeError && (
                <Alert
                  type="error"
                  showIcon
                  message="执行失败"
                  description={<pre style={{ margin: 0, fontSize: 11, whiteSpace: 'pre-wrap', maxHeight: 120, overflow: 'auto' }}>{probeError}</pre>}
                  style={{ marginTop: 8 }}
                />
              )}

              {probeResult !== null && (
                <div style={{ marginTop: 8 }}>
                  {probeResult.length === 0 ? (
                    <div style={{ padding: '6px 8px', background: 'rgba(255,255,255,0.03)', borderRadius: 4, fontSize: 12, color: '#94a3b8' }}>
                      ✓ 执行成功，返回 0 行记录
                    </div>
                  ) : (
                    <div className="probe-result-table-wrapper">
                      <table className="probe-result-table">
                        <thead>
                          <tr>
                            {Object.keys(probeResult[0] || {}).map((col) => (
                              <th key={col}>{col}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {probeResult.slice(0, 50).map((row, idx) => (
                            <tr key={idx}>
                              {Object.keys(probeResult[0] || {}).map((col) => {
                                const val = row[col];
                                const str = val === null ? 'NULL' : typeof val === 'object' ? JSON.stringify(val) : String(val);
                                return (
                                  <td key={col} className={val === null ? 'cell-null' : ''} title={str}>
                                    {str}
                                  </td>
                                );
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {probeResult.length > 50 && (
                        <div style={{ fontSize: 11, color: '#94a3b8', padding: '4px 6px', textAlign: 'center' }}>
                          仅展示前 50 行，共 {probeResult.length} 行
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Key Metadata Table */}
            <div>
              <Typography.Title level={5} style={{ marginBottom: 10 }}>节点元数据</Typography.Title>
              <dl className="node-details">
                <dt>节点标识</dt><dd><code>{selectedNode.id}</code></dd>
                <dt>监听地址</dt><dd><code>{selectedNode.host}:{selectedNode.port}</code></dd>
                <dt>所属集群</dt><dd>{selectedNode.group || '未分组'}</dd>
                <dt>节点角色</dt><dd>{selectedNode.role === 'primary' ? '主写入库 (Primary)' : '流复制从库 (Standby)'}</dd>
                <dt>主守护进程 PID</dt>
                <dd>
                  <Tooltip title="PostgreSQL 根守护进程 (postmaster/pgmaster) OS PID。当节点重启后，PID 会变更。">
                    <code>
                      {observed?.[selectedNode.id]?.pid != null
                        ? `pgmaster: ${observed[selectedNode.id].pid}`
                        : (observed?.[selectedNode.id]?.running === false ? '已停止 (未运行)' : '未探测 / --')}
                    </code>
                  </Tooltip>
                </dd>
                <dt>运行状态</dt>
                <dd>
                  {observed?.[selectedNode.id]?.running === true ? (
                    <Tag color="success">运行中</Tag>
                  ) : observed?.[selectedNode.id]?.running === false ? (
                    <Tag color="error">已停止</Tag>
                  ) : (
                    <Tag>未探测</Tag>
                  )}
                </dd>
                <dt>数据库版本</dt><dd title={selectedNode.database_version}>{selectedNode.server_version || '未读取（实例不可达）'}</dd>
                <dt>MMR 插件版本</dt><dd>{selectedNode.extension_versions?.fdd_mmr || '未读取 / 未安装'}</dd>
                <dt>数据目录</dt><dd><code style={{ fontSize: 11 }}>{selectedNode.data_dir}</code></dd>
                {(() => {
                  const configured = selectedNode.extensions || [];
                  const installed = selectedNode.installed_extensions?.length
                    ? selectedNode.installed_extensions : configured;
                  return installed.length > 0 && (
                    <>
                      <dt>已安装扩展</dt>
                      <dd>
                        <Space size={4} wrap>
                          {installed.map((ext) => (
                            <Tag key={ext} color="purple">{ext}</Tag>
                          ))}
                        </Space>
                        {!selectedNode.installed_extensions?.length ? (
                          <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 4 }}>
                            实例未连通，显示部署配置声明的扩展
                          </Typography.Text>
                        ) : null}
                      </dd>
                    </>
                  );
                })()}
              </dl>
            </div>

            {/* Single Node Operations */}
            <div>
              <Typography.Title level={5} style={{ marginBottom: 10 }}>单节点运维动作</Typography.Title>
              <Space wrap>
                {(['start', 'stop', 'restart'] as const).map((name) => {
                  const action = actions.find((item) => item.id === `deployment.${name}`);
                  return action && (
                    <Button
                      key={name}
                      danger={name === 'stop'}
                      type={name === 'start' ? 'primary' : 'default'}
                      disabled={loading || roleBusy}
                      onClick={() => void run(action, selectedNode.id)}
                    >
                      {name === 'start' ? '启动节点' : name === 'stop' ? '停止节点' : '重启节点'}
                    </Button>
                  );
                })}
                {selectedNode.group && selectedNode.role === 'standby' && actions.some(item => item.id === 'deployment.failover') && <>
                  <Tooltip title={observed?.[selectedNode.id]?.running !== true ? '该节点未确认运行，请先启动并确认备库角色' : '停止当前主库，只提升选中的备库；旧主保持停止'}>
                    <Button disabled={loading || roleBusy || observed?.[selectedNode.id]?.running !== true}
                      onClick={() => void runRole('deployment.failover')}>备库升主</Button>
                  </Tooltip>
                  <Tooltip title={observed?.[selectedNode.id]?.running !== true ? '该节点未确认运行，请先启动并确认备库角色' : '停止当前主库，提升选中的备库，再重建旧主为备库'}>
                    <Button disabled={loading || roleBusy || observed?.[selectedNode.id]?.running !== true}
                      onClick={() => void runRole('deployment.switchover')}>主备切换</Button>
                  </Tooltip>
                </>}
                {selectedNode.group && selectedNode.previous_primary === selectedNode.id && selectedNode.rejoin_required && actions.some(item => item.id === 'deployment.rejoin') && <Tooltip title="从当前主库重新备份，替换旧主的数据目录并以备库启动">
                  <Button disabled={loading || roleBusy} onClick={() => void runRole('deployment.rejoin')}>重建旧主为备库</Button>
                </Tooltip>}
                {selectedNode.group && selectedNode.original_primary === selectedNode.id && selectedNode.role === 'standby' && actions.some(item => item.id === 'deployment.restore') && <Tooltip title={observed?.[selectedNode.id]?.running !== true ? '请先将旧主重建为备库并启动，再执行回切' : '将原主库重新提升为主库，并重建当前主库为备库'}>
                  <Button disabled={loading || roleBusy || observed?.[selectedNode.id]?.running !== true}
                    onClick={() => void runRole('deployment.restore')}>切回原主库</Button>
                </Tooltip>}
              </Space>
            </div>
          </div>
        )}
      </Drawer>

      <MmrStatusPanel open={mmrOpen} onClose={()=>setMmrOpen(false)} data={mmrData} error={mmrError}
        loading={mmrLoading} stale={mmrStale} onRefresh={()=>void refreshMmr()} />
      <ExecutionTerminal taskId={terminalTaskId} onInspect={openTask} initiallyExpanded={roleTerminal}
        onFinished={() => { setRoleBusy(false); void refreshStatus(true); void refreshMmr();
          if (environment) void api<TopologyData>(`/environments/${encodeURIComponent(environment.id)}/topology`).then(graph => {
            setTopology(graph); setSelectedNode(prev => graph.nodes.find(n => n.id === prev?.id) || null);
          });
          void reload(); }} onClose={() => {
          if (roleBusy) return; // Keep polling a dismissed running role task until its final state is known.
          if (roleTerminal && environment) localStorage.removeItem(`role-task:${environment.id}`);
          setTerminalTaskId(null);
        }} />
      <DeploymentWizard open={wizardOpen} environment={wizardEnvironment} onClose={() => setWizardOpen(false)}
        openTask={openTask} onSaved={async (id) => { await reload(); onSelectEnvironment?.(id); }} />
      <EnvironmentModal
        open={envModalOpen}
        editing={envEditing}
        products={products}
        onClose={() => setEnvModalOpen(false)}
        onSaved={(id) => { void reload(); onSelectEnvironment?.(id); }}
      />
    </div>
  );
}
