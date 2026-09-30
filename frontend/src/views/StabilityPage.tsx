import { useState } from 'react';
import { Alert, App, Button, Empty, Input, Space, Typography } from 'antd';
import { PlayCircleOutlined } from '@ant-design/icons';

import { operationRequest, type Environment, type Product, type RegressionBinding } from '../platform/api';
import TestBindingBar from '../components/TestBindingBar';

type Props = {
  product: Product | undefined;
  environment: Environment | undefined;
  openTask: (taskId: string) => void;
  reload: () => Promise<void>;
  environments?: Environment[];
  bindings?: RegressionBinding[];
};

export default function StabilityPage({ product, environment, openTask, reload,
                                        environments = [], bindings = [] }: Props) {
  const { message, modal } = App.useApp();
  const [target, setTarget] = useState('all');
  const [loading, setLoading] = useState(false);

  async function run() {
    if (!environment) return;
    const proceed = await new Promise<boolean>((resolve) => modal.confirm({
      title: '启动常稳测试',
      content: `${environment.title} · ${target || 'all'}。将按原常稳配置执行正式工作负载。`,
      okText: '启动', cancelText: '取消', onOk: () => resolve(true), onCancel: () => resolve(false),
    }));
    if (!proceed) return;
    setLoading(true);
    try {
      const task = await operationRequest(environment.id, `stability.${environment.product_id}`, target || 'all', {}, true);
      openTask(task.id);
    } catch (cause) {
      message.error((cause as Error).message);
    } finally { setLoading(false); }
  }

  return (
    <>
      <div className="page-heading"><div><Typography.Title level={3}>压测与常稳</Typography.Title><Typography.Text type="secondary">长时间工作负载与实时输出</Typography.Text></div></div>
      <TestBindingBar
        product={product}
        profileId={product?.test_profiles?.[0]?.id}
        environments={environments}
        bindings={bindings}
        onChanged={reload}
      />
      {!environment ? <Empty description="先选择环境" /> : <section className="work-section">
        <Typography.Title level={5}>{product?.title || environment.product_id} 常稳任务</Typography.Title>
        <Alert type="info" showIcon message="正式时长由现有 stable 配置决定" description="目标 all 执行完整方案，也可以指定一条工作负载名称。" />
        <Space wrap className="action-row"><Input value={target} onChange={(event) => setTarget(event.target.value)} placeholder="all 或工作负载名" style={{ width: 300 }} /><Button type="primary" icon={<PlayCircleOutlined />} loading={loading} onClick={() => void run()}>启动任务</Button></Space>
      </section>}
    </>
  );
}
