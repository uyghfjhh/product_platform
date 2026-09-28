import { useEffect, useState } from 'react';
import { Alert, App, Button, Select, Space, Tag, Typography } from 'antd';
import { AimOutlined, CheckCircleFilled, CloseCircleFilled, LinkOutlined } from '@ant-design/icons';

import { api, post, type Environment, type Product, type RegressionBinding } from '../api';

/** 测试页就地绑定栏：展示/切换当前测试 profile 绑定的执行环境（§6.2）。 */
export default function TestBindingBar({ product, profileId, environments, bindings, onChanged }: {
  product: Product | undefined;
  profileId: string | undefined;
  environments: Environment[];
  bindings: RegressionBinding[];
  onChanged: () => Promise<void> | void;
}) {
  const { message } = App.useApp();
  const [connectivity, setConnectivity] = useState<'unknown' | 'checking' | 'ok' | 'fail'>('unknown');

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

  useEffect(() => { setConnectivity('unknown'); }, [binding?.environment_id]);

  async function probe() {
    if (!bound) return;
    setConnectivity('checking');
    try {
      await post(`/environments/${encodeURIComponent(bound.id)}/query`, {
        sql: 'SELECT 1', max_rows: 1,
      });
      setConnectivity('ok');
    } catch {
      setConnectivity('fail');
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
        {connectivity === 'ok' && (
          <Tag icon={<CheckCircleFilled />} color="success">已连通</Tag>
        )}
        {connectivity === 'fail' && (
          <Tag icon={<CloseCircleFilled />} color="error">连接失败</Tag>
        )}
        {connectivity === 'checking' && <Tag color="processing">探测中</Tag>}
        <Button size="small" type="text" icon={<LinkOutlined />} onClick={() => void probe()}>
          探测连通性
        </Button>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {bound.host}:{bound.port}
        </Typography.Text>
      </Space>
    </div>
  );
}
