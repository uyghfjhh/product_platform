import { useState } from 'react';
import { App, Button, Empty, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography } from 'antd';
import { EditOutlined, PlusOutlined } from '@ant-design/icons';

import { api, put, type Environment, type Product } from '../api';

type Props = {
  products: Product[];
  environments: Environment[];
  selectedId: string;
  onSelect: (id: string) => void;
  reload: () => Promise<void>;
};

export default function EnvironmentsPage({ products, environments, selectedId, onSelect, reload }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [editing, setEditing] = useState<Environment | null | undefined>(undefined);
  const [saving, setSaving] = useState(false);

  function open(item: Environment | null) {
    setEditing(item);
    form.setFieldsValue(item || {
      id: '', title: '', product_id: products[0]?.id,
      host: '127.0.0.1', port: 5432, database_name: 'postgres', database_user: 'postgres',
    });
  }

  async function save() {
    try {
      const values = await form.validateFields();
      setSaving(true);
      if (editing) {
        await put(`/environments/${encodeURIComponent(editing.id)}`, values);
      } else {
        await api('/environments', { method: 'POST', body: JSON.stringify(values) });
      }
      await reload();
      onSelect(values.id);
      setEditing(undefined);
      message.success(editing ? '环境已更新' : '环境已登记');
    } catch (error) {
      if (error instanceof Error) message.error(error.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="page-heading">
        <div><Typography.Title level={3}>产品与环境</Typography.Title><Typography.Text type="secondary">登记连接与部署配置</Typography.Text></div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => open(null)}>新增环境</Button>
      </div>
      {environments.length === 0 ? <Empty description="尚未登记环境"><Button onClick={() => open(null)}>新增环境</Button></Empty> : <Table
        rowKey="id" dataSource={environments} pagination={{ pageSize: 10 }} size="middle"
        columns={[
          { title: '环境', dataIndex: 'title', render: (text: string, row: Environment) => <Space><Button type="link" onClick={() => onSelect(row.id)}>{text}</Button>{row.id === selectedId && <Tag color="success">当前</Tag>}</Space> },
          { title: '产品', dataIndex: 'product_id', render: (id: string) => products.find((item) => item.id === id)?.title || id },
          { title: '地址', render: (_: unknown, row: Environment) => <Typography.Text code>{row.host}:{row.port}</Typography.Text> },
          { title: '部署目标', dataIndex: 'deployment_target', responsive: ['md'], render: (value: string | null) => value || '-' },
          { title: '', render: (_: unknown, row: Environment) => <Button type="text" icon={<EditOutlined />} onClick={() => open(row)} aria-label={`编辑 ${row.title}`} /> },
        ]}
      />}
      <Modal title={editing ? '编辑环境' : '新增环境'} open={editing !== undefined} onOk={() => void save()} onCancel={() => setEditing(undefined)} confirmLoading={saving} okText="保存" width={650} destroyOnHidden>
        <Form form={form} layout="vertical">
          <div className="form-grid">
            <Form.Item label="环境 ID" name="id" rules={[{ required: true }, { pattern: /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/, message: '使用字母、数字、点、下划线或横线' }]}><Input disabled={!!editing} /></Form.Item>
            <Form.Item label="名称" name="title" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item label="产品" name="product_id" rules={[{ required: true }]}><Select options={products.map((item) => ({ label: item.title, value: item.id }))} /></Form.Item>
            <Form.Item label="主机" name="host" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item label="端口" name="port" rules={[{ required: true }]}><InputNumber min={1} max={65535} style={{ width: '100%' }} /></Form.Item>
            <Form.Item label="数据库名" name="database_name"><Input /></Form.Item>
            <Form.Item label="数据库用户" name="database_user"><Input /></Form.Item>
            <Form.Item label="部署目标" name="deployment_target"><Input placeholder="streaming.demo" /></Form.Item>
          </div>
          <Form.Item label="部署配置文件" name="deployment_config"><Input placeholder="/path/to/pgcluster.yaml" /></Form.Item>
        </Form>
      </Modal>
    </>
  );
}
