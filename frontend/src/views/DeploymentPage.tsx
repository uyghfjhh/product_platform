import { useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Drawer, Dropdown, Empty, Input, Segmented, Space, Tag, Tooltip, Typography } from 'antd';
import {
  CloudServerOutlined, ReloadOutlined, CopyOutlined, MoreOutlined, DatabaseOutlined,
  PlusOutlined, SettingOutlined, EditOutlined, DeploymentUnitOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons';

import { api, post, operationRequest, type Action, type Environment, type Product } from '../platform/api';
import type { TopologyData, TopologyNode } from '../platform/topology';
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

  async function refreshStatus(silent = false) {
    if (!environment) return;
    if (!silent) setStatusLoading(true);
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
      setTerminalTaskId(task.id);
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
                        disabled={!action || loading}
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
                        ['restore', '角色回切'],
                        ['reset', '重置集群'],
                        ['clean', '清理集群'],
                      ].map(([name, label]) => ({
                        key: name,
                        label,
                        danger: name === 'clean' || name === 'reset',
                        disabled: loading || !actions.some((action) => action.id === `deployment.${name}`),
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
              return <Canvas topology={topology} observed={observed}
                onSelectNode={setSelectedNode} onOpenSql={handleOpenSqlWorkbench}
                onDeploy={() => { setWizardEnvironment(environment); setWizardOpen(true); }} />;
            })() : <Alert type="warning" showIcon message="拓扑暂不可显示"
              description={topologyError || '检查部署配置和目标名称'} />}
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
                      disabled={loading}
                      onClick={() => void run(action, selectedNode.id)}
                    >
                      {name === 'start' ? '启动节点' : name === 'stop' ? '停止节点' : '重启节点'}
                    </Button>
                  );
                })}
                {selectedNode.group && actions.some((item) => item.id === 'deployment.failover') && (
                  <Button disabled={loading} onClick={() => void run(actions.find((item) => item.id === 'deployment.failover')!, `streaming.${selectedNode.group}`)}>
                    主备切换
                  </Button>
                )}
                {selectedNode.group && actions.some((item) => item.id === 'deployment.rejoin') && (
                  <Button disabled={loading} onClick={() => void run(actions.find((item) => item.id === 'deployment.rejoin')!, `streaming.${selectedNode.group}`)}>
                    旧主重建
                  </Button>
                )}
                {selectedNode.group && actions.some((item) => item.id === 'deployment.restore') && (
                  <Button disabled={loading} onClick={() => void run(actions.find((item) => item.id === 'deployment.restore')!, `streaming.${selectedNode.group}`)}>
                    角色回切
                  </Button>
                )}
              </Space>
            </div>
          </div>
        )}
      </Drawer>

      <ExecutionTerminal taskId={terminalTaskId} onInspect={openTask}
        onFinished={() => void reload()} onClose={() => setTerminalTaskId(null)} />
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
