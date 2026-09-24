import { useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Drawer, Empty, Form, Input, InputNumber, Modal, Segmented, Space, Tag, Typography } from 'antd';
import {
  CheckCircleOutlined, CloudServerOutlined, ReloadOutlined, CodeOutlined,
  RocketOutlined, CopyOutlined, PlayCircleOutlined, StopOutlined
} from '@ant-design/icons';
import { ReactFlow, Background, Controls, type Edge, type Node } from '@xyflow/react';

import { api, operationRequest, type Action, type Environment, type Product } from '../api';
import CodeEditor from '../components/LazyCodeEditor';
import ThreeTopologyView, { type TopologyData, type TopologyNode } from '../components/ThreeTopologyView';

type Props = {
  product: Product | undefined;
  environment: Environment | undefined;
  openTask: (taskId: string) => void;
  reload: () => Promise<void>;
  navigate?: (page: string) => void;
};

type Profile = { generated: boolean; deployment_config: string; test_override: string; context_ready: boolean };

export default function DeploymentPage({ product, environment, openTask, reload, navigate }: Props) {
  const { message, modal } = App.useApp();
  const [actions, setActions] = useState<Action[]>([]);
  const [configuration, setConfiguration] = useState<{ content: string; path: string } | null>(null);
  const [topology, setTopology] = useState<TopologyData | null>(null);
  const [topologyError, setTopologyError] = useState('');
  const [observed, setObserved] = useState<Record<string, { running: boolean | null; message: string }> | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileForm] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [viewMode, setViewMode] = useState<'3d' | '2d'>('3d');

  useEffect(() => {
    if (!environment) { setActions([]); setConfiguration(null); setTopology(null); setObserved(null); return; }
    setObserved(null);
    void Promise.all([
      api<Action[]>(`/environments/${encodeURIComponent(environment.id)}/actions`),
      api<{ content: string; path: string }>(`/environments/${encodeURIComponent(environment.id)}/configuration`).catch(() => null),
      api<TopologyData>(`/environments/${encodeURIComponent(environment.id)}/topology`).then((value) => { setTopologyError(''); return value; }).catch((error) => { setTopologyError(error.message); return null; }),
      product?.id === 'fbasecman' ? api<Profile>(`/environments/${encodeURIComponent(environment.id)}/fbasecman-profile`).catch(() => null) : Promise.resolve(null),
    ]).then(([list, config, graph, currentProfile]) => {
      setActions(list.filter((item) => item.capability === 'deployment'));
      setConfiguration(config);
      setTopology(graph);
      setProfile(currentProfile);
    }).catch((error) => message.error(error.message));
  }, [environment, product?.id, message]);

  async function refreshStatus() {
    if (!environment) return;
    setStatusLoading(true);
    try {
      setObserved(await api(`/environments/${encodeURIComponent(environment.id)}/topology/status`));
    } catch (error) {
      message.error((error as Error).message);
    } finally { setStatusLoading(false); }
  }

  async function createProfile() {
    if (!environment) return;
    try {
      const values = await profileForm.validateFields();
      const saved = await api<Profile>(`/environments/${encodeURIComponent(environment.id)}/fbasecman-profile`, {
        method: 'POST', body: JSON.stringify(values),
      });
      setProfile(saved);
      setProfileOpen(false);
      await reload();
      message.success('已生成并校验 pgcluster 部署方案');
    } catch (error) {
      if (error instanceof Error) message.error(error.message);
    }
  }

  // 2D Symmetrical Layout with Custom Styling
  const graph = useMemo(() => {
    if (!topology) return { nodes: [] as Node[], edges: [] as Edge[] };
    const groups = Array.from(new Set(topology.nodes.map((item) => item.group || 'cluster')));
    const nodes: Node[] = [];

    groups.forEach((groupName, gIdx) => {
      const groupNodes = topology.nodes.filter((item) => (item.group || 'cluster') === groupName);
      const baseX = groups.length === 1 ? 260 : gIdx === 0 ? 80 : 560;

      const primary = groupNodes.find((n) => n.role === 'primary') || groupNodes[0];
      const standbys = groupNodes.filter((n) => n !== primary);

      // Primary Node (Prominent Header Position)
      if (primary) {
        const isStopped = observed?.[primary.id]?.running === false;
        nodes.push({
          id: primary.id,
          position: { x: baseX + 80, y: 30 },
          data: {
            label: (
              <div style={{ textAlign: 'center', padding: '4px 0' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                  <Tag color="gold" style={{ margin: 0, fontSize: 10, lineHeight: '16px' }}>👑 PRIMARY</Tag>
                  <strong style={{ fontSize: 14, color: '#0f172a' }}>{primary.label}</strong>
                </div>
                <div style={{ color: '#0284c7', fontSize: 12, marginTop: 4, fontFamily: 'monospace' }}>
                  {primary.host}:{primary.port}
                </div>
                <div style={{ marginTop: 4, fontSize: 11, color: isStopped ? '#ef4444' : '#10b981', fontWeight: 600 }}>
                  {observed ? (isStopped ? '● 节点已离线' : '● 正在运行') : '● 主写库就绪'}
                </div>
              </div>
            ),
          },
          style: {
            width: 220,
            borderRadius: 8,
            border: isStopped ? '2px solid #ef4444' : '2px solid #10b981',
            boxShadow: isStopped ? '0 0 14px rgba(239, 68, 68, 0.35)' : '0 0 16px rgba(16, 185, 129, 0.28)',
            background: '#ffffff',
            cursor: 'pointer',
          },
        });
      }

      // Standby Nodes arranged in 2 columns (Compact Grid)
      standbys.forEach((sb, sIdx) => {
        const col = sIdx % 2;
        const row = Math.floor(sIdx / 2);
        const nx = baseX + col * 190;
        const ny = 145 + row * 88;
        const isStopped = observed?.[sb.id]?.running === false;

        nodes.push({
          id: sb.id,
          position: { x: nx, y: ny },
          data: {
            label: (
              <div style={{ textAlign: 'left', padding: '2px 4px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ fontSize: 12, color: '#334155' }}>{sb.label}</strong>
                  <span style={{ fontSize: 10, color: isStopped ? '#ef4444' : '#0284c7' }}>STANDBY</span>
                </div>
                <div style={{ color: '#64748b', fontSize: 11, fontFamily: 'monospace', marginTop: 2 }}>
                  :{sb.port}
                </div>
              </div>
            ),
          },
          style: {
            width: 175,
            borderRadius: 6,
            border: isStopped ? '1px solid #ef4444' : '1px solid #94a3b8',
            background: '#f8fafc',
            cursor: 'pointer',
          },
        });
      });
    });

    // Edges with glowing animation
    const edges: Edge[] = topology.edges.map((edge) => {
      const isMmr = edge.kind === 'mmr';
      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        animated: true,
        label: isMmr ? 'MMR 双向对等复制' : 'WAL 流复制',
        style: {
          stroke: isMmr ? '#a855f7' : '#0284c7',
          strokeWidth: isMmr ? 2.5 : 1.5,
        },
      };
    });

    return { nodes, edges };
  }, [topology, observed]);

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
      openTask(task.id);
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function handleOpenSqlWorkbench(node: TopologyNode) {
    sessionStorage.setItem('sql_target_port', String(node.port));
    sessionStorage.setItem('sql_target_node', node.id);
    if (navigate) {
      navigate('database');
    } else {
      message.info(`已选中节点 ${node.id} (端口 ${node.port})，可在左侧切换到数据库管理直接查询`);
    }
  }

  function copyText(text: string) {
    navigator.clipboard.writeText(text);
    message.success('已复制到剪贴板');
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>部署管理</Typography.Title>
          <Typography.Text type="secondary">pgcluster 驱动的多中心拓扑编排、健康探测与实例生命周期</Typography.Text>
        </div>
      </div>

      {!environment ? <Empty description="先在产品与环境页登记环境" /> : <>
        <div className="context-line" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 16px', background: '#fff', borderRadius: 8, border: '1px solid #e2e8f0', marginBottom: 16 }}>
          <Space>
            <CloudServerOutlined style={{ fontSize: 18, color: '#166e60' }} />
            <strong>{environment.title}</strong>
            <Tag color="cyan">{environment.deployment_target || '未设置部署目标'}</Tag>
            <Typography.Text type="secondary">主机: {environment.host}</Typography.Text>
          </Space>
          <Button loading={statusLoading} icon={<ReloadOutlined />} onClick={() => void refreshStatus()}>
            探测实际运行状态
          </Button>
        </div>

        {product?.id === 'fbasecman' && (
          <section className="work-section" style={{ background: '#fff', padding: '16px 20px', borderRadius: 8, border: '1px solid #e2e8f0', marginBottom: 16 }}>
            <div className="section-heading" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <Typography.Title level={5} style={{ margin: 0 }}>fbasecman 14节点回归方案 (pgcluster 引擎驱动)</Typography.Title>
              <Button onClick={() => {
                profileForm.setFieldsValue({
                  mmr1_port: 15011,
                  data_root: `/home/postgres/product_platform/fbasecman_regress/${environment.id}`,
                  license_file: '/home/postgres/license/license.dat',
                });
                setProfileOpen(true);
              }}>
                {profile?.generated ? '重新生成部署方案' : '生成 pgcluster 方案'}
              </Button>
            </div>
            <Typography.Text type="secondary">全面替代硬编码旧脚本，由 pgcluster 自动化规划端口、数据目录与 14 节点双 MMR 拓扑。</Typography.Text>
            {profile?.generated && (
              <div className="profile-state" style={{ marginTop: 12 }}>
                <TagText label="部署拓扑配置" value={profile.deployment_config} />
                <TagText label="测试覆盖配置" value={profile.test_override} />
                <TagText label="测试夹具状态" value={profile.context_ready ? '✅ test_context.yaml 已生成' : '⏳ 待准备测试夹具'} />
              </div>
            )}
            {profile?.generated && (
              <Space style={{ marginTop: 12 }}>
                <Button
                  type="primary"
                  icon={<RocketOutlined />}
                  disabled={!topology}
                  onClick={() => void run({ id: 'tests.prepare_fbasecman', title: '准备 fbasecman 测试夹具', capability: 'tests', changes_environment: true })}
                >
                  一键准备测试夹具 (生成 test_context.yaml)
                </Button>
                <Typography.Text type="secondary">创建并启动集群后执行，自动完成 roles 认证与 MMR 初始化</Typography.Text>
              </Space>
            )}
          </section>
        )}

        {!environment.deployment_config ? (
          <Alert type="info" showIcon message="该环境尚未关联部署配置" description="可在上方点击一键生成 pgcluster 方案，或在产品与环境中填写现有配置路径与目标。" />
        ) : <>
          {/* Action Toolbar */}
          <section className="work-section" style={{ background: '#fff', padding: '16px 20px', borderRadius: 8, border: '1px solid #e2e8f0', marginBottom: 16 }}>
            <div className="section-heading" style={{ marginBottom: 12 }}>
              <Typography.Title level={5} style={{ margin: 0 }}>集群生命周期管控</Typography.Title>
            </div>
            <Space wrap size="middle">
              {actions.filter((action) => !['deployment.failover', 'deployment.rejoin', 'deployment.clean'].includes(action.id)).map((action) => (
                <Button
                  key={action.id}
                  danger={action.id === 'deployment.stop'}
                  type={action.id === 'deployment.create' ? 'primary' : action.id === 'deployment.validate' ? 'dashed' : 'default'}
                  icon={action.id === 'deployment.validate' ? <CheckCircleOutlined /> : action.id === 'deployment.start' ? <PlayCircleOutlined /> : action.id === 'deployment.stop' ? <StopOutlined /> : <ReloadOutlined />}
                  loading={loading}
                  onClick={() => void run(action)}
                >
                  {action.title}
                </Button>
              ))}
              {actions.some((action) => action.id === 'deployment.clean') && (
                <Button danger loading={loading} onClick={() => void run(actions.find((action) => action.id === 'deployment.clean')!)}>
                  清理集群
                </Button>
              )}
            </Space>
          </section>

          {/* Topology Stage (2D vs 3D View Switcher) */}
          <section className="work-section" style={{ background: '#fff', padding: '16px 20px', borderRadius: 8, border: '1px solid #e2e8f0', marginBottom: 16 }}>
            <div className="section-heading" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
              <div>
                <Typography.Title level={5} style={{ margin: 0 }}>集群拓扑架构看板</Typography.Title>
                <Typography.Text type="secondary">
                  当前目标: {topology?.target || environment.deployment_target} · 共 {topology?.nodes.length || 0} 个实例节点
                </Typography.Text>
              </div>
              <Space>
                <Segmented
                  value={viewMode}
                  onChange={(val) => setViewMode(val as '3d' | '2d')}
                  options={[
                    { label: '🪐 3D 全息视图', value: '3d' },
                    { label: '📐 2D 架构视图', value: '2d' },
                  ]}
                />
              </Space>
            </div>

            {topology ? (
              viewMode === '3d' ? (
                <ThreeTopologyView
                  topology={topology}
                  observed={observed}
                  onSelectNode={(node) => setSelectedNode(node)}
                  height={580}
                />
              ) : (
                <div style={{ height: 560, background: '#f8fafc', borderRadius: 8, border: '1px solid #e2e8f0' }}>
                  <ReactFlow
                    nodes={graph.nodes}
                    edges={graph.edges}
                    fitView
                    fitViewOptions={{ padding: 0.18 }}
                    nodesDraggable={false}
                    nodesConnectable={false}
                    onNodeClick={(_, node) => setSelectedNode(topology.nodes.find((item) => item.id === node.id) || null)}
                    proOptions={{ hideAttribution: true }}
                  >
                    <Controls showInteractive={false} />
                    <Background gap={20} color="#cbd5e1" />
                  </ReactFlow>
                </div>
              )
            ) : (
              <Alert type="warning" showIcon message="拓扑暂不可显示" description={topologyError || '检查部署配置和目标名称'} />
            )}
          </section>

          {/* Configuration File Viewer */}
          <section className="work-section" style={{ background: '#fff', padding: '16px 20px', borderRadius: 8, border: '1px solid #e2e8f0' }}>
            <div className="section-heading" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <Typography.Title level={5} style={{ margin: 0 }}>底层部署配置 (YAML)</Typography.Title>
              <Typography.Text type="secondary" copyable={{ text: environment.deployment_config }}>
                {environment.deployment_config}
              </Typography.Text>
            </div>
            {configuration ? <CodeEditor value={configuration.content} language="yaml" readOnly height={420} /> : <Alert type="warning" showIcon message="配置文件尚不可读取" />}
          </section>
        </>}
      </>}

      {/* Profile Form Modal */}
      <Modal title="生成 pgcluster 回归部署方案" open={profileOpen} onOk={() => void createProfile()} onCancel={() => setProfileOpen(false)} okText="生成并校验" width={570} destroyOnHidden>
        <Form form={profileForm} layout="vertical">
          <Form.Item label="MMR1 主节点起始端口" name="mmr1_port" rules={[{ required: true }]} extra="系统将基于此端口依次自动规划全部 14 个主从节点端口">
            <InputNumber min={1024} max={65500} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="远端 PGDATA 数据存储根目录" name="data_root" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="远端 License 文件绝对路径" name="license_file" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
        </Form>
      </Modal>

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
              <Tag color={observed?.[selectedNode.id]?.running === false ? 'error' : 'success'}>
                {observed?.[selectedNode.id]?.running === false ? '已停止' : '运行中'}
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
            <div style={{ padding: '12px 14px', background: '#0f172a', borderRadius: 8, color: '#e2e8f0' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <span style={{ fontSize: 12, color: '#94a3b8' }}>PSQL 直连命令行</span>
                <Button
                  size="small"
                  type="text"
                  style={{ color: '#38bdf8' }}
                  icon={<CopyOutlined />}
                  onClick={() => copyText(`psql -h ${selectedNode.host} -p ${selectedNode.port} -U postgres`)}
                >
                  复制
                </Button>
              </div>
              <code style={{ fontSize: 13, color: '#38bdf8', fontFamily: 'monospace', display: 'block', wordBreak: 'break-all' }}>
                psql -h {selectedNode.host} -p {selectedNode.port} -U postgres
              </code>
              <Button
                type="primary"
                style={{ width: '100%', marginTop: 10, background: '#166e60', borderColor: '#166e60' }}
                icon={<CodeOutlined />}
                onClick={() => handleOpenSqlWorkbench(selectedNode)}
              >
                进入 Web-PSQL 交互控制台
              </Button>
            </div>

            {/* Key Metadata Table */}
            <div>
              <Typography.Title level={5} style={{ marginBottom: 10 }}>节点元数据</Typography.Title>
              <dl className="node-details">
                <dt>节点标识</dt><dd><code>{selectedNode.id}</code></dd>
                <dt>监听地址</dt><dd><code>{selectedNode.host}:{selectedNode.port}</code></dd>
                <dt>所属集群</dt><dd>{selectedNode.group || '未分组'}</dd>
                <dt>节点角色</dt><dd>{selectedNode.role === 'primary' ? '主写入库 (Primary)' : '流复制从库 (Standby)'}</dd>
                <dt>数据目录</dt><dd><code style={{ fontSize: 11 }}>{selectedNode.data_dir}</code></dd>
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
                      onClick={() => void run(action, selectedNode.id)}
                    >
                      {name === 'start' ? '启动节点' : name === 'stop' ? '停止节点' : '重启节点'}
                    </Button>
                  );
                })}
                {selectedNode.group && actions.some((item) => item.id === 'deployment.failover') && (
                  <Button onClick={() => void run(actions.find((item) => item.id === 'deployment.failover')!, `streaming.${selectedNode.group}`)}>
                    主备切换
                  </Button>
                )}
                {selectedNode.group && actions.some((item) => item.id === 'deployment.rejoin') && (
                  <Button onClick={() => void run(actions.find((item) => item.id === 'deployment.rejoin')!, `streaming.${selectedNode.group}`)}>
                    旧主重建
                  </Button>
                )}
              </Space>
            </div>
          </div>
        )}
      </Drawer>
    </>
  );
}

function TagText({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
      <Typography.Text type="secondary" style={{ minWidth: 90 }}>{label}:</Typography.Text>
      <Typography.Text copyable={{ text: value }} code>{value}</Typography.Text>
    </div>
  );
}
