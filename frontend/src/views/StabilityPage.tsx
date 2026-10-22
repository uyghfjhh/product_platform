import { Empty, Tag, Typography } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';

import { type Environment, type Product, type RegressionBinding } from '../platform/api';
import TestBindingBar from '../components/TestBindingBar';
import WorkloadWorkbench from '../components/WorkloadWorkbench';

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
  const profileId = product?.test_profiles?.[0]?.id;
  const binding = bindings.find((item) => item.product_id === product?.id && item.profile_id === profileId);
  const executionEnvironment = profileId
    ? environments.find((item) => item.id === binding?.environment_id && item.product_id === product?.id)
    : environment;

  return (
    <div className="workload-page">
      <header className="workload-page-heading">
        <div className="workload-page-heading-left">
          <span className="workload-page-icon"><ThunderboltOutlined /></span>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Typography.Title level={3} style={{ margin: 0 }}>压测与常稳</Typography.Title>
              <Tag color="blue">代理路由 · HA 混沌 · 基准对比</Tag>
            </div>
            <Typography.Text type="secondary">
              编排 fbasecman 多路并发常稳（读写分离 / 负载均衡 / HA 动态切换 / 预编译防泄漏）与直连代理 A/B 性能基准。
            </Typography.Text>
          </div>
        </div>
      </header>
      <TestBindingBar product={product} profileId={profileId}
        environments={environments} bindings={bindings} onChanged={reload} />
      {executionEnvironment
        ? <WorkloadWorkbench key={executionEnvironment.id} environment={executionEnvironment} openTask={openTask} />
        : <div className="workload-empty"><Empty description="先选择并绑定执行环境" /></div>}
    </div>
  );
}
