import { useEffect, useState } from 'react';
import { Alert, App, Button, Drawer, Empty, Form, Input, InputNumber, Modal, Segmented, Select, Space, Tag, Typography } from 'antd';
import {
  CloudServerOutlined, ReloadOutlined, CodeOutlined, CopyOutlined,
} from '@ant-design/icons';

import { api, operationRequest, type Action, type Environment, type Product, type RegressionBinding } from '../api';
import CodeEditor from '../components/LazyCodeEditor';
import ThreeTopologyView, { type TopologyData, type TopologyNode } from '../components/ThreeTopologyView';
import EnvironmentModal from '../components/EnvironmentModal';
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
  productBindings?: RegressionBinding[];
};

type Profile = { generated: boolean; deployment_config: string; test_override: string; context_ready: boolean };

export default function DeploymentPage({
  product,
  environment,
  environments = [],
  products = [],
  onSelectEnvironment,
  openTask,
  reload,
  productBindings = [],
}: Props) {
  const { message, modal } = App.useApp();
  const [actions, setActions] = useState<Action[]>([]);
  const [configuration, setConfiguration] = useState<{ content: string; path: string } | null>(null);
  const [topology, setTopology] = useState<TopologyData | null>(null);
  const [topologyError, setTopologyError] = useState('');
  const [observed, setObserved] = useState<Record<string, { running: boolean | null; message: string }> | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null);
  const [sqlNode, setSqlNode] = useState<TopologyNode | null>(null);
  const [envModalOpen, setEnvModalOpen] = useState(false);
  const [envEditing, setEnvEditing] = useState<Environment | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileForm] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [viewMode, setViewMode] = useState<'3d' | '2d'>('2d');
  const productAdapter = deploymentAdapter(product);
  const DeploymentCanvas = deploymentFrontend(product)?.DeploymentCanvas;
  const compatibleProfiles = (product?.test_profiles || []).filter((profile) =>
    profile.deployment_targets.some((pattern) => pattern.endsWith('*')
      ? Boolean(environment?.deployment_target?.startsWith(pattern.slice(0, -1)))
      : environment?.deployment_target === pattern));
  const boundProfiles = productBindings.filter((binding) => binding.environment_id === environment?.id
    && binding.product_id === product?.id).map((binding) => binding.profile_id);

  async function updateRegressionBindings(selected: string[]) {
    if (!product || !environment) return;
    try {
      for (const profileId of boundProfiles.filter((id) => !selected.includes(id))) {
        await api(`/regression-bindings/${encodeURIComponent(product.id)}/${encodeURIComponent(profileId)}`,
          { method: 'DELETE' });
      }
      for (const profileId of selected.filter((id) => !boundProfiles.includes(id))) {
        await api(`/regression-bindings/${encodeURIComponent(product.id)}/${encodeURIComponent(profileId)}`,
          { method: 'PUT', body: JSON.stringify({ environment_id: environment.id }) });
      }
      await reload();
      message.success('环境用途已更新');
    } catch (cause) {
      message.error((cause as Error).message);
    }
  }

  useEffect(() => {
    if (!environment) { setActions([]); setConfiguration(null); setTopology(null); setObserved(null); return; }
    setObserved(null);
    void Promise.all([
      api<Action[]>(`/environments/${encodeURIComponent(environment.id)}/actions`),
      api<{ content: string; path: string }>(`/environments/${encodeURIComponent(environment.id)}/configuration`).catch(() => null),
      api<TopologyData>(`/environments/${encodeURIComponent(environment.id)}/topology`).then((value) => { setTopologyError(''); return value; }).catch((error) => { setTopologyError(error.message); return null; }),
      productAdapter.profilePath ? api<Profile>(productAdapter.profilePath(environment.id)).catch(() => null) : Promise.resolve(null),
    ]).then(([list, config, graph, currentProfile]) => {
      setActions(list.filter((item) => item.capability === 'deployment'));
      setConfiguration(config);
      setTopology(graph);
      setProfile(currentProfile);
    }).catch((error) => message.error(error.message));
  }, [environment, productAdapter.profilePath, message]);

  async function refreshStatus() {
    if (!environment) return;
    setStatusLoading(true);
    try {
      setObserved(await api(`/environments/${encodeURIComponent(environment.id)}/topology/status`));
    } catch (error) {
      message.error((error as Error).message);
    } finally { setStatusLoading(false); }
  }

  useEffect(() => { if (environment?.deployment_config) void refreshStatus(); }, [environment?.id, environment?.deployment_config]);

  async function createProfile() {
    if (!environment) return;
    try {
      const values = await profileForm.validateFields();
      const saved = await api<Profile>(productAdapter.profilePath!(environment.id), {
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

  function openProfileWizard() {
    if (!environment) return;
    profileForm.setFieldsValue({
      // 默认值从当前环境派生：端口取环境登记端口，License 取部署配置同目录或留空必填。
      mmr1_port: environment.port,
      data_root: productAdapter.defaultDataRoot?.(environment.id),
      license_file: environment.deployment_config
        ? `${environment.deployment_config.replace(/\/[^/]*$/, '')}/license.dat`
        : '',
    });
    setProfileOpen(true);
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
      {environments.length > 0 && (
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
              <Typography.Text strong style={{ fontSize: 14, display: 'block' }}>当前集群部署环境</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                已接入 {environments.length} 套独立集群，点击切换拓扑视图与生命周期管控
              </Typography.Text>
            </div>
          </div>
          <Space size={8}>
            <Button size="small" onClick={() => { setEnvEditing(null); setEnvModalOpen(true); }}>
              新增环境
            </Button>
            {environment && (
              <Button size="small" onClick={() => { setEnvEditing(environment); setEnvModalOpen(true); }}>
                编辑当前环境
              </Button>
            )}
          </Space>
          <Segmented
            size="middle"
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
          />
        </div>
      )}
      {!productAdapter.workspaceClass && <div className="page-heading">
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>数据库部署管理</Typography.Title>
          <Typography.Text type="secondary">pgcluster 驱动的多中心拓扑编排、健康探测与实例生命周期</Typography.Text>
        </div>
      </div>}

      {environment && <div className="regression-binding-control" style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
        <Typography.Text strong>环境用途</Typography.Text>
        <Tag color={boundProfiles.length ? 'green' : 'default'}>{boundProfiles.length ? '回归测试' : '开发环境'}</Tag>
        <Select mode="multiple" aria-label="绑定回归测试" style={{ minWidth: 260, maxWidth: '100%' }}
          value={boundProfiles} placeholder="选择回归测试"
          options={compatibleProfiles.map((profile) => ({ value: profile.id, label: profile.title }))}
          onChange={(values: string[]) => void updateRegressionBindings(values)} />
      </div>}

      {!environment ? <Empty description="先在产品与环境页登记环境" /> : <>
        <section className="cman-deploy-toolbar" aria-label="集群部署操作" style={{ marginBottom: 16 }}>
          <div className="cman-deploy-actions">
            {([
              ['create', '⚡ 一键部署'], ['start', '▶ 启动'], ['stop', '⏹ 停止'],
              ['restart', '🔄 重启'], ['clean', '🧹 清理'], ['heal', '🩺 自愈'],
              ['reset', '♻️ 重置'],
              ['doctor', '🔍 体检'],
            ] as const).map(([name, label]) => {
              const action = actions.find((item) => item.id === `deployment.${name}`);
              return <Button key={name} type={name === 'create' ? 'primary' : 'default'}
                danger={name === 'stop' || name === 'clean'} disabled={!action || loading}
                onClick={() => action && void run(action)}>{label}</Button>;
            })}
          </div>
          <div className="cman-deploy-actions">
            {productAdapter.hasProfileWizard && <>
              <Button onClick={openProfileWizard}>📐 部署向导</Button>
              <Button disabled={!profile?.generated || loading} title={profile?.context_ready ? '测试夹具已生成，可重新准备' : '部署并启动集群后准备测试夹具'}
                onClick={() => productAdapter.fixtureAction && void run(productAdapter.fixtureAction)}>
                🧪 准备测试夹具
              </Button>
            </>}
            <Button loading={statusLoading} icon={<ReloadOutlined />} onClick={() => void refreshStatus()}>🔄 刷新拓扑</Button>
            <Segmented
              value={viewMode}
              onChange={(val) => setViewMode(val as '3d' | '2d')}
              options={[
                { label: '📐 2D 架构 (GSAP/SVG)', value: '2d' },
                { label: '🪐 3D 全息 (Three.js)', value: '3d' },
              ]}
            />
          </div>
        </section>

        {!environment.deployment_config ? (
          <Alert type="info" showIcon message="该环境尚未关联部署配置" description="可在上方点击一键生成 pgcluster 方案，或在产品与环境中填写现有配置路径与目标。" />
        ) : <>
          {/* Unified Cyberpunk Topology Stage (2D GSAP / 3D Three.js) */}
          <section className="work-section cman-topology-section" style={{ background: 'transparent', padding: 0, border: 'none', marginBottom: 16 }}>
            {topology ? (
              viewMode === '3d' ? (
                <ThreeTopologyView
                  topology={topology}
                  observed={observed}
                  onSelectNode={(node) => setSelectedNode(node)}
                  height={640}
                />
              ) : DeploymentCanvas ? (
                <DeploymentCanvas
                  topology={topology}
                  observed={observed}
                  onSelectNode={setSelectedNode}
                  onOpenSql={handleOpenSqlWorkbench}
                  onDeploy={() => {
                    const action = actions.find((item) => item.id === 'deployment.create');
                    if (action) void run(action);
                  }}
                />
              ) : (
                <ThreeTopologyView topology={topology} observed={observed}
                  onSelectNode={setSelectedNode} height={640} />
              )
            ) : (
              <Alert type="warning" showIcon message="拓扑暂不可显示" description={topologyError || '检查部署配置和目标名称'} />
            )}
          </section>

          {/* Configuration File Viewer */}
          <section className="work-section" style={{ background: 'var(--bg-surface)', padding: '16px 20px', borderRadius: 8, border: '1px solid var(--border-subtle)' }}>
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

      <SqlWorkbenchDrawer
        environment={environment}
        node={sqlNode}
        onClose={() => setSqlNode(null)}
      />
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
