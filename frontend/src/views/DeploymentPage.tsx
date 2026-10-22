import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, App, Button, Dropdown, Empty, Select, Space, Tag, Typography, Table } from 'antd';
import {
  CloudServerOutlined, ReloadOutlined, MoreOutlined, DatabaseOutlined,
  PlusOutlined, SettingOutlined, EditOutlined, DeploymentUnitOutlined,
} from '@ant-design/icons';

import { api, operationRequest, type Action, type Environment, type Product, type Task } from '../platform/api';
import type { TopologyData, TopologyNode } from '../platform/topology';
import MmrStatusPanel from '../components/MmrStatusPanel';
import NodeInspectorDrawer from '../components/NodeInspectorDrawer';
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

  // adapter 工厂每次调用返回新对象——必须 memo，否则下方 effect 依赖每轮渲染都变，
  // 造成 topology/status 无限 refetch 且 setObserved(null) 把已取回的状态清空。
  const productAdapter = useMemo(() => deploymentAdapter(product), [product?.id]);
  // 2D 画布是平台中立能力：产品未提供自有画布时使用共享实现
  const ProductCanvas = useMemo(() => deploymentFrontend(product)?.DeploymentCanvas, [product?.id]);

  // 环境过多时按产品分组，可输入环境名、部署目标或主机过滤
  const environmentGroups = useMemo(() => {
    const groups = new Map<string, { label: string; options: Array<{ value: string; label: string; env: Environment; searchText: string }> }>();
    for (const env of environments) {
      const key = env.product_id || '';
      if (!groups.has(key)) {
        const title = products.find((p) => p.id === key)?.title || key || '未关联产品';
        groups.set(key, { label: title, options: [] });
      }
      groups.get(key)!.options.push({
        value: env.id,
        label: env.title,
        env,
        searchText: `${env.title} ${env.deployment_target || ''} ${env.host}`.toLowerCase(),
      });
    }
    return Array.from(groups.values());
  }, [environments, products]);

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
          <p>切换会中断连接；会检查复制位置，但不承诺零数据丢失。{environment.product_id === 'fbasecman' ? '不会修改 fbasecman 配置。' : ''}</p>
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
            <Select
              size="middle"
              showSearch
              filterOption={(input, option) =>
                String((option as { searchText?: string })?.searchText || '').includes(input.toLowerCase())}
              popupMatchSelectWidth={false}
              style={{ minWidth: 200, maxWidth: 380, flex: '0 1 auto' }}
              styles={{ popup: { root: { minWidth: 300 } } }}
              value={environment?.id}
              onChange={(val) => onSelectEnvironment?.(String(val))}
              options={environmentGroups}
              optionRender={(option) => {
                const env = (option.data as { env?: Environment }).env;
                if (!env) return option.label;
                return (
                  <div>
                    <div style={{ fontWeight: 500 }}>{env.title}</div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{env.host}</div>
                  </div>
                );
              }}
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

      <NodeInspectorDrawer
        environment={environment}
        selectedNode={selectedNode}
        observed={observed}
        actions={actions}
        loading={loading}
        roleBusy={roleBusy}
        onClose={() => setSelectedNode(null)}
        onOpenDatabase={onOpenDatabase}
        onRunAction={run}
        onRunRole={runRole}
        onCopyText={copyText}
      />

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
