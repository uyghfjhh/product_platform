import { useEffect, useState } from 'react';
import { Alert, App, Button, Select, Space, Tag, Tooltip, Typography } from 'antd';
import { AimOutlined, CheckCircleFilled, CloseCircleFilled, LinkOutlined } from '@ant-design/icons';

import { api, post, type Environment, type Product, type RegressionBinding } from '../platform/api';

type NodeStatus = { running?: boolean | null; known?: boolean; message?: string };
type ProbeResult =
  | { kind: 'cluster'; online: number; total: number; nodes: Record<string, NodeStatus> }
  | { kind: 'endpoint'; ok: boolean };

/** 测试页就地绑定栏：展示/切换当前测试 profile 绑定的执行环境（§6.2）。 */
export default function TestBindingBar({ product, profileId, environments, bindings, onChanged, onOpenDeployment }: {
  product: Product | undefined;
  profileId: string | undefined;
  environments: Environment[];
  bindings: RegressionBinding[];
  onChanged: () => Promise<void> | void;
  onOpenDeployment?: (environmentId: string) => void;
}) {
  const { message } = App.useApp();
  const [probe, setProbe] = useState<'checking' | ProbeResult | null>(null);

  const profile = product?.test_profiles?.find((item) => item.id === profileId);
  const compatible = environments.filter((item) => {
    if (item.product_id !== product?.id) return false;
    if (!profile) return true;
    return profile.deployment_targets.some((pattern) => pattern.endsWith('*')
      ? Boolean(item.deployment_target?.startsWith(pattern.slice(0, -1)))
      : item.deployment_target === pattern);
  });
  const binding = bindings.find((item) => item.product_id === product?.id
    && item.profile_id === (profileId || 'default'));
  const bound = compatible.find((item) => item.id === binding?.environment_id);

  useEffect(() => { setProbe(null); }, [binding?.environment_id]);

  async function runProbe() {
    if (!bound) return;
    setProbe('checking');
    try {
      const nodes = await api<Record<string, NodeStatus>>(
        `/environments/${encodeURIComponent(bound.id)}/topology/status`);
      const entries = Object.values(nodes);
      setProbe({
        kind: 'cluster',
        online: entries.filter((item) => item.running === true).length,
        total: entries.length,
        nodes,
      });
    } catch {
      // 无部署拓扑的环境退化为单点连通性探测。
      try {
        const [error] = await post<[{ message?: string } | null, unknown]>(
          `/environments/${encodeURIComponent(bound.id)}/studio`, {
            procedure: 'query', query: { sql: 'SELECT 1', parameters: [] },
          });
        if (error) throw new Error(error.message || '连接失败');
        setProbe({ kind: 'endpoint', ok: true });
      } catch {
        setProbe({ kind: 'endpoint', ok: false });
      }
    }
  }

  async function rebind(environmentId: string) {
    if (!product || !profileId) return;
    try {
      await api(
        `/regression-bindings/${encodeURIComponent(product.id)}/${encodeURIComponent(profileId)}`,
        { method: 'PUT', body: JSON.stringify({ environment_id: environmentId }) });
      await onChanged();
      message.success('绑定已更新');
    } catch (cause) {
      message.error((cause as Error).message);
    }
  }

  if (!product || !profileId) return null;

  if (!bound) {
    return (
      <Alert
        type="warning" showIcon className="test-binding-bar"
        message={<span><AimOutlined /> 当前测试尚未绑定环境</span>}
        description={compatible.length === 0
          ? '没有拓扑兼容的已登记环境，请先在数据库部署页登记环境。'
          : '选择一套兼容环境后开始执行测试。'}
        action={compatible.length > 0 && (
          <Select
            size="small" style={{ minWidth: 220 }} placeholder="选择执行环境"
            options={compatible.map((item) => ({
              label: `${item.title} (${item.host}:${item.port})`, value: item.id,
            }))}
            onChange={(id) => void rebind(id)}
          />
        )}
      />
    );
  }

  return (
    <div className="test-binding-bar test-binding-bar-bound">
      <Space wrap align="center" size={10}>
        <AimOutlined className="test-binding-icon" />
        <Typography.Text strong>执行环境</Typography.Text>
        <Select
          size="small" style={{ minWidth: 230 }}
          value={bound.id}
          options={compatible.map((item) => ({
            label: `${item.title} (${item.host}:${item.port})`, value: item.id,
          }))}
          onChange={(id) => void rebind(id)}
          aria-label="切换绑定环境"
        />
        {probe === 'checking' && <Tag color="processing">探测中</Tag>}
        {probe !== null && probe !== 'checking' && probe.kind === 'endpoint' && (
          <Tag icon={probe.ok ? <CheckCircleFilled /> : <CloseCircleFilled />}
            color={probe.ok ? 'success' : 'error'}>
            {probe.ok ? '接入点已连通' : '连接失败'}
          </Tag>
        )}
        {probe !== null && probe !== 'checking' && probe.kind === 'cluster' && (
          <Tooltip
            title={(
              <div className="cluster-probe-nodes">
                {Object.entries(probe.nodes).map(([name, item]) => (
                  <div key={name} className="cluster-probe-node">
                    {item.running === true
                      ? <CheckCircleFilled className="probe-ok" />
                      : <CloseCircleFilled className="probe-fail" />}
                    <Typography.Text code>{name}</Typography.Text>
                    <Typography.Text type="secondary">
                      {item.running === true ? '运行中' : item.running === false ? '已停止' : '未知'}
                    </Typography.Text>
                  </div>
                ))}
                <div className="cluster-probe-hint">点击进入部署页查看集群详情</div>
              </div>
            )}
          >
            <Tag
              icon={probe.online === probe.total ? <CheckCircleFilled /> : <CloseCircleFilled />}
              color={probe.online === probe.total ? 'success' : 'warning'}
              style={{ cursor: 'pointer' }}
              onClick={() => bound && onOpenDeployment?.(bound.id)}
            >
              集群 {probe.online}/{probe.total} 在线
            </Tag>
          </Tooltip>
        )}
        <Button size="small" type="text" icon={<LinkOutlined />} onClick={() => void runProbe()}>
          探测集群状态
        </Button>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {bound.host}:{bound.port}
        </Typography.Text>
      </Space>
    </div>
  );
}
