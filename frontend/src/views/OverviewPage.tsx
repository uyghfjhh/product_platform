import { Button, Empty, Space, Table, Tag, Typography } from 'antd';
import { ArrowRightOutlined, PlusOutlined } from '@ant-design/icons';

import { type Environment, type Product, type Task, statusColor } from '../api';

type Props = {
  products: Product[];
  environments: Environment[];
  tasks: Task[];
  openTask: (taskId: string) => void;
  navigate: (page: 'environments') => void;
};

export default function OverviewPage({ products, environments, tasks, openTask, navigate }: Props) {
  return (
    <>
      <div className="page-heading">
        <div><Typography.Title level={3}>工作台</Typography.Title><Typography.Text type="secondary">产品、环境与当前操作</Typography.Text></div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('environments')}>管理环境</Button>
      </div>
      <section className="overview-numbers" aria-label="工作台概览">
        <div><span>产品</span><strong>{products.length}</strong></div>
        <div><span>已登记环境</span><strong>{environments.length}</strong></div>
        <div><span>执行中</span><strong>{tasks.filter((item) => ['RUNNING', 'QUEUED', 'CANCELLING'].includes(item.status)).length}</strong></div>
      </section>
      <section className="work-section">
        <div className="section-heading"><Typography.Title level={5}>最近操作</Typography.Title></div>
        {tasks.length === 0 ? <Empty description="暂无操作" /> : <Table
          rowKey="id" size="middle" pagination={false} dataSource={tasks.slice(0, 8)}
          columns={[
            { title: '操作', dataIndex: 'action', render: (value: string) => <Typography.Text code>{value}</Typography.Text> },
            { title: '环境', dataIndex: 'environment_id' },
            { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag> },
            { title: '时间', dataIndex: 'created_at', responsive: ['md'], render: (value: string) => new Date(value).toLocaleString('zh-CN') },
            { title: '', render: (_: unknown, row: Task) => <Button type="link" icon={<ArrowRightOutlined />} onClick={() => openTask(row.id)}>查看</Button> },
          ]}
        />}
      </section>
      <section className="work-section">
        <div className="section-heading"><Typography.Title level={5}>产品目录</Typography.Title></div>
        <div className="product-list">
          {products.map((item) => <div key={item.id} className="product-row">
            <Space direction="vertical" size={2}><Typography.Text strong>{item.title}</Typography.Text><Typography.Text type="secondary">{item.description}</Typography.Text></Space>
            <Typography.Text type="secondary">{environments.filter((environment) => environment.product_id === item.id).length} 个环境</Typography.Text>
          </div>)}
        </div>
      </section>
    </>
  );
}
