import { useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Drawer, Dropdown, Empty, Segmented, Space, Tag, Typography } from 'antd';
import {
  CloudServerOutlined, ReloadOutlined, CodeOutlined, CopyOutlined, MoreOutlined, DatabaseOutlined,
} from '@ant-design/icons';

import { api, operationRequest, type Action, type Environment, type Product } from '../platform/api';
import type { TopologyData, TopologyNode } from '../platform/topology';
import SharedDeploymentCanvas from '../components/DeploymentCanvas';
import EnvironmentModal from '../components/EnvironmentModal';
import DeploymentWizard from '../components/DeploymentWizard';
import SqlWorkbenchDrawer from '../components/SqlWorkbenchDrawer';
import { deploymentAdapter, deploymentFrontend } from '../products/deploymentRegistry';

type Props = {
  product: Product | undefined;
  environment: Environment | undefined;
  environments?: Environment[];
  products?: Product[];
  onSelectEnvironment?: (id: string) => void;
  openTask: (taskId: string) => void;
  reload: () => Promise<void>;
  onOpenDatabase?: (node?: TopologyNode) => void;
};

type Profile = { generated: boolean; deployment_config: string; test_override: string; context_ready: boolean; defaults?: { data_root?: string; license_file?: string } };

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
  const [observed, setObserved] = useState<Record<string, { running: boolean | null; message: string }> | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null);
  const [sqlNode, setSqlNode] = useState<TopologyNode | null>(null);
  const [envModalOpen, setEnvModalOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardEnvironment, setWizardEnvironment] = useState<Environment | undefined>();
  const [envEditing, setEnvEditing] = useState<Environment | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(false);
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
      productAdapter.profilePath ? api<Profile>(productAdapter.profilePath(environment.id)).catch(() => null) : Promise.resolve(null),
    ]).then(([list, graph, currentProfile]) => {
      setActions(list.filter((item) => item.capability === 'deployment'));
      setTopology(graph);
      setProfile(currentProfile);
    }).catch((error) => message.error(error.message));
  }, [environment, productAdapter.profilePath, message]);

  async function refreshStatus(silent = false) {
    if (!environment) return;
    if (!silent) setStatusLoading(true);
    try {
      const next = await api<Record<string, { running: boolean | null; message: string }>>(
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
      openTask(task.id);
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function handleOpenSqlWorkbench(node: TopologyNode) {
    setSqlNode(node);
  }

  function copyText(text: string) {
    navigator.clipboard.writeText(text);
    message.success('已复制到剪贴板');
  }

  return (
    <div className={productAdapter.workspaceClass}>
      {/* Multi-Environment Switcher Bar */}
      <div
          className="deployment-env-switcher-card"
          style={{
            background: 'var(--bg-surface)',
            padding: '10px 16px',
            borderRadius: 8,
            border: '1px solid var(--border-subtle)',
            marginBottom: 14,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 12,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <CloudServerOutlined style={{ fontSize: 18, color: '#38bdf8' }} />
            <div>
              <Typography.Text strong style={{ fontSize: 14, display: 'block' }}>部署环境</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                已接入 {environments.length} 套独立集群，选择环境查看节点与复制关系
              </Typography.Text>
            </div>
          </div>
          <Space size={8} wrap>
            <Button type="primary" size="small" onClick={() => { setWizardEnvironment(undefined); setWizardOpen(true); }}>新建部署方案</Button>
            {environment && <Button size="small" onClick={() => { setWizardEnvironment(environment); setWizardOpen(true); }}>配置部署方案</Button>}
            <Button size="small" onClick={() => { setEnvEditing(null); setEnvModalOpen(true); }}>
              新增环境
            </Button>
            {environment && (
              <Button size="small" onClick={() => { setEnvEditing(environment); setEnvModalOpen(true); }}>
                编辑当前环境
              </Button>
            )}
          </Space>
          {environments.length > 0 && <Segmented
            size="middle"
            // 窄视口下环境枚举个数多时允许横向滚动，不撑破 body 宽度
            style={{ maxWidth: '100%', overflowX: 'auto' }}
            value={environment?.id}
            onChange={(val) => onSelectEnvironment?.(String(val))}
            options={environments.map((env) => {
              return {
                value: env.id,
                label: (
                  <span style={{ padding: '3px 6px', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <strong style={{ fontSize: 13 }}>{env.title}</strong>
                    <Tag
                      color="cyan"
                      style={{ margin: 0, fontSize: 11, padding: '0 5px' }}
                    >
                      :{env.port}
                    </Tag>
                  </span>
                ),
              };
            })}
          />}
        </div>
      {!environment ? <Empty description="先在产品与环境页登记环境" /> : <>
        <section className="cman-deploy-toolbar" aria-label="集群部署操作" style={{ marginBottom: 16 }}>
          <div className="cman-deploy-actions">
            {([
              ['create', '部署方案'], ['start', '▶ 启动'], ['stop', '⏹ 停止'],
              ['restart', '🔄 重启'],
            ] as const).map(([name, label]) => {
              const action = actions.find((item) => item.id === `deployment.${name}`);
              return <Button key={name} type={name === 'create' ? 'primary' : 'default'}
                danger={name === 'stop'} disabled={!action || loading}
                onClick={() => { if (name === 'create') { setWizardEnvironment(environment); setWizardOpen(true); } else if (action) void run(action); }}>{label}</Button>;
            })}
          </div>
          <div className="cman-deploy-actions">
            {productAdapter.hasProfileWizard && <>
              <Button onClick={() => { setWizardEnvironment(environment); setWizardOpen(true); }}>部署向导</Button>
              <Button disabled={!profile?.generated || loading} title={profile?.context_ready ? '测试夹具已生成，可重新准备' : '部署并启动集群后准备测试夹具'}
                onClick={() => productAdapter.fixtureAction && void run(productAdapter.fixtureAction)}>
                🧪 准备测试夹具
              </Button>
            </>}
            <Dropdown menu={{ items: [
              ['doctor', '环境体检'], ['heal', '自愈'], ['reset', '重置'], ['restore', '角色回切'], ['clean', '清理'],
            ].map(([name, label]) => ({ key: name, label, danger: name === 'clean' || name === 'reset',
              disabled: loading || !actions.some((action) => action.id === `deployment.${name}`),
            })), onClick: ({ key }) => {
              const action = actions.find((item) => item.id === `deployment.${key}`);
              if (action) void run(action);
            } }}>
              <Button icon={<MoreOutlined />}>更多操作</Button>
            </Dropdown>
          </div>
        </section>

        {!environment.deployment_config ? (
          <Alert type="info" showIcon message="该环境尚未关联部署配置" description="点击“配置部署方案”生成或导入配置，系统会自动关联当前环境。" />
        ) : <>
          <section className="work-section cman-topology-section deployment-topology">
            <div className="deployment-topology-heading">
              <Space wrap>
                <Typography.Text strong>集群拓扑</Typography.Text>
                {topology && <Typography.Text type="secondary">{topology.nodes.length} 个节点</Typography.Text>}
                {topology && <Tag color={observed ? 'default' : 'processing'}>
                  {observed ? `在线 ${topology.nodes.filter((node) => observed[node.id]?.running === true).length} · 已停止 ${topology.nodes.filter((node) => observed[node.id]?.running === false).length} · 未知 ${topology.nodes.filter((node) => observed[node.id]?.running == null).length}` : '正在探测'}
                </Tag>}
              </Space>
              <Space wrap>
                {onOpenDatabase && <Button icon={<DatabaseOutlined />} onClick={() => onOpenDatabase()}>数据库管理</Button>}
                <Button loading={statusLoading} icon={<ReloadOutlined />} onClick={() => void refreshStatus()}>刷新状态</Button>
              </Space>
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
              <Button
                type="primary"
                style={{ width: '100%', marginTop: 10 }}
                icon={<CodeOutlined />}
                onClick={() => handleOpenSqlWorkbench(selectedNode)}
              >
                打开 SQL 探测抽屉
              </Button>
              {onOpenDatabase && (
                <Button
                  style={{ width: '100%', marginTop: 8 }}
                  icon={<DatabaseOutlined />}
                  onClick={() => onOpenDatabase(selectedNode)}
                >
                  进入数据库管理
                </Button>
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

      <SqlWorkbenchDrawer
        environment={environment}
        node={sqlNode}
        onClose={() => setSqlNode(null)}
      />
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
