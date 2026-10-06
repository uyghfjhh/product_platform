import { Empty, Typography } from 'antd';
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
        <span className="workload-page-icon"><ThunderboltOutlined /></span>
        <div>
          <Typography.Title level={3}>压测与常稳</Typography.Title>
          <Typography.Text type="secondary">组合工作负载，审阅真实执行目标，观察持续运行表现。</Typography.Text>
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
