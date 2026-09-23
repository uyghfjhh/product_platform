import { useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Drawer, Empty, Form, Input, InputNumber, Modal, Space, Typography } from 'antd';
import { CheckCircleOutlined, CloudServerOutlined, ReloadOutlined } from '@ant-design/icons';
import { ReactFlow, Background, Controls, type Edge, type Node } from '@xyflow/react';

import { api, operationRequest, type Action, type Environment, type Product } from '../api';
import CodeEditor from '../components/LazyCodeEditor';

type Props = {
  product: Product | undefined;
  environment: Environment | undefined;
  openTask: (taskId: string) => void;
  reload: () => Promise<void>;
};

type Topology = {
  target: string;
  kind: string;
  nodes: { id: string; label: string; host: string; port: number; role: string; group?: string; data_dir: string }[];
  edges: { id: string; source: string; target: string; kind: string }[];
};

type Profile = { generated: boolean; deployment_config: string; test_override: string; context_ready: boolean };

export default function DeploymentPage({ product, environment, openTask, reload }: Props) {
  const { message, modal } = App.useApp();
  const [actions, setActions] = useState<Action[]>([]);
  const [configuration, setConfiguration] = useState<{ content: string; path: string } | null>(null);
  const [topology, setTopology] = useState<Topology | null>(null);
  const [topologyError, setTopologyError] = useState('');
  const [observed, setObserved] = useState<Record<string, { running: boolean | null; message: string }> | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [selectedNode, setSelectedNode] = useState<Topology['nodes'][number] | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileForm] = Form.useForm();
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!environment) { setActions([]); setConfiguration(null); setTopology(null); setObserved(null); return; }
    setObserved(null);
    void Promise.all([
      api<Action[]>(`/environments/${encodeURIComponent(environment.id)}/actions`),
      api<{ content: string; path: string }>(`/environments/${encodeURIComponent(environment.id)}/configuration`).catch(() => null),
      api<Topology>(`/environments/${encodeURIComponent(environment.id)}/topology`).then((value) => { setTopologyError(''); return value; }).catch((error) => { setTopologyError(error.message); return null; }),
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

  const graph = useMemo(() => {
    if (!topology) return { nodes: [] as Node[], edges: [] as Edge[] };
    const groups = Array.from(new Set(topology.nodes.map((item) => item.group || '其他')));
    const nodes: Node[] = topology.nodes.map((item) => {
      const sameGroup = topology.nodes.filter((candidate) => (candidate.group || '其他') === (item.group || '其他'));
      const groupIndex = groups.indexOf(item.group || '其他');
      const row = sameGroup.indexOf(item);
      return {
        id: item.id,
        position: { x: groupIndex * 310, y: row * 98 },
        data: { label: <div className="topo-node-content"><strong>{item.label}</strong><small>{item.host}:{item.port}</small><span>{item.role === 'primary' ? '主节点' : item.role === 'standby' ? '备节点' : '实例'}{observed ? ` · ${observed[item.id]?.running === true ? '运行中' : observed[item.id]?.running === false ? '已停止' : '未知'}` : ''}</span></div> },
        style: { width: 235, borderRadius: 5, border: observed?.[item.id]?.running === false ? '1px solid #bb5c53' : item.role === 'primary' ? '1px solid #24816c' : '1px solid #ccd9d4', background: '#fff', color: '#263832' },
      };
    });
    const edges: Edge[] = topology.edges.map((item) => ({ id: item.id, source: item.source, target: item.target, label: item.kind, style: { stroke: item.kind === 'mmr' ? '#268396' : '#88a69c', strokeWidth: 1.5 } }));
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

  return (
    <>
      <div className="page-heading">
        <div><Typography.Title level={3}>部署管理</Typography.Title><Typography.Text type="secondary">配置、检查与实例生命周期</Typography.Text></div>
      </div>
      {!environment ? <Empty description="先在产品与环境页登记环境" /> : <>
        <div className="context-line"><CloudServerOutlined /> {environment.title} · {environment.deployment_target || '尚未设置部署目标'}</div>
        {product?.id === 'fbasecman' && <section className="work-section">
          <div className="section-heading"><Typography.Title level={5}>fbasecman 回归部署方案</Typography.Title><Button onClick={() => {
            profileForm.setFieldsValue({ mmr1_port: 15011, data_root: `/home/postgres/product_platform/fbasecman_regress/${environment.id}`, license_file: '/home/postgres/license/license.dat' });
            setProfileOpen(true);
          }}>{profile?.generated ? '重新生成方案' : '生成方案'}</Button></div>
          <Typography.Text type="secondary">由 pgcluster 部署 MMR 与流复制节点，测试夹具另行准备。</Typography.Text>
          {profile?.generated && <div className="profile-state"><TagText label="部署配置" value={profile.deployment_config} /><TagText label="测试配置" value={profile.test_override} /><TagText label="测试夹具" value={profile.context_ready ? '已准备' : '待准备'} /></div>}
          {profile?.generated && <Space className="action-row"><Button
            disabled={!topology}
            onClick={() => void run({ id: 'tests.prepare_fbasecman', title: '准备 fbasecman 测试夹具', capability: 'tests', changes_environment: true })}
          >准备测试夹具</Button><Typography.Text type="secondary">在完成 pgcluster 创建后执行</Typography.Text></Space>}
        </section>}
        {!environment.deployment_config ? <Alert type="info" showIcon message="该环境尚未关联部署配置" description="可在本页生成 pgcluster 方案，或在产品与环境中填写现有配置路径与目标。" /> : <>
          <section className="work-section">
            <div className="section-heading"><Typography.Title level={5}>部署操作</Typography.Title></div>
            <Space wrap>
              {actions.filter((action) => !['deployment.failover', 'deployment.rejoin', 'deployment.clean'].includes(action.id)).map((action) => <Button
                key={action.id}
                danger={action.id === 'deployment.stop'}
                type={action.id === 'deployment.validate' ? 'primary' : 'default'}
                icon={action.id === 'deployment.validate' ? <CheckCircleOutlined /> : <ReloadOutlined />}
                loading={loading}
                onClick={() => void run(action)}
              >{action.title}</Button>)}
              {actions.some((action) => action.id === 'deployment.clean') && <Button danger loading={loading} onClick={() => void run(actions.find((action) => action.id === 'deployment.clean')!)}>清理集群</Button>}
            </Space>
          </section>
          <section className="work-section">
            <div className="section-heading"><Typography.Title level={5}>部署拓扑</Typography.Title><Space wrap><Typography.Text type="secondary">{topology?.target || environment.deployment_target}</Typography.Text><Button loading={statusLoading} icon={<ReloadOutlined />} onClick={() => void refreshStatus()}>刷新实际状态</Button></Space></div>
            {topology ? <div className="deployment-flow"><ReactFlow
              nodes={graph.nodes} edges={graph.edges} fitView fitViewOptions={{ padding: 0.16 }}
              nodesDraggable={false} nodesConnectable={false}
              onNodeClick={(_, node) => setSelectedNode(topology.nodes.find((item) => item.id === node.id) || null)}
              proOptions={{ hideAttribution: true }}
            ><Controls showInteractive={false} /><Background gap={24} color="#e6edeb" /></ReactFlow></div>
              : <Alert type="warning" showIcon message="拓扑暂不可显示" description={topologyError || '检查配置和目标名称'} />}
          </section>
          <section className="work-section">
            <div className="section-heading"><Typography.Title level={5}>原始部署配置</Typography.Title><Typography.Text type="secondary" copyable={{ text: environment.deployment_config }}>{environment.deployment_config}</Typography.Text></div>
            {configuration ? <CodeEditor value={configuration.content} language="yaml" readOnly height={460} /> : <Alert type="warning" showIcon message="配置文件尚不可读取" />}
          </section>
        </>}
      </>}
      <Modal title="生成 pgcluster 回归方案" open={profileOpen} onOk={() => void createProfile()} onCancel={() => setProfileOpen(false)} okText="生成并校验" width={570} destroyOnHidden>
        <Form form={profileForm} layout="vertical">
          <Form.Item label="MMR1 主节点端口" name="mmr1_port" rules={[{ required: true }]}><InputNumber min={1024} max={65500} style={{ width: '100%' }} /></Form.Item>
          <Form.Item label="远端 PGDATA 根目录" name="data_root" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item label="远端 License 文件" name="license_file" rules={[{ required: true }]}><Input /></Form.Item>
        </Form>
      </Modal>
      <Drawer title={selectedNode?.label || '节点'} open={!!selectedNode} onClose={() => setSelectedNode(null)} width={380}>
        {selectedNode && <>
          <dl className="node-details"><dt>角色</dt><dd>{selectedNode.role}</dd><dt>地址</dt><dd>{selectedNode.host}:{selectedNode.port}</dd><dt>实际状态</dt><dd>{observed?.[selectedNode.id]?.running === true ? '运行中' : observed?.[selectedNode.id]?.running === false ? '已停止' : '未探测'}</dd><dt>数据目录</dt><dd>{selectedNode.data_dir}</dd><dt>所属组</dt><dd>{selectedNode.group || '-'}</dd></dl>
          <Space wrap className="action-row">
            {(['start', 'stop', 'restart'] as const).map((name) => {
              const action = actions.find((item) => item.id === `deployment.${name}`);
              return action && <Button key={name} danger={name === 'stop'} onClick={() => void run(action, selectedNode.id)}>{name === 'start' ? '启动节点' : name === 'stop' ? '停止节点' : '重启节点'}</Button>;
            })}
            {selectedNode.group && actions.some((item) => item.id === 'deployment.failover') && <Button onClick={() => void run(actions.find((item) => item.id === 'deployment.failover')!, `streaming.${selectedNode.group}`)}>主备切换</Button>}
            {selectedNode.group && actions.some((item) => item.id === 'deployment.rejoin') && <Button onClick={() => void run(actions.find((item) => item.id === 'deployment.rejoin')!, `streaming.${selectedNode.group}`)}>旧主重建</Button>}
          </Space>
        </>}
      </Drawer>
    </>
  );
}

function TagText({ label, value }: { label: string; value: string }) {
  return <div><Typography.Text type="secondary">{label}</Typography.Text><Typography.Text copyable={{ text: value }}>{value}</Typography.Text></div>;
}
