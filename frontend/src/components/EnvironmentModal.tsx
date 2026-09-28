import { useEffect, useState } from 'react';
import { App, Form, Input, InputNumber, Modal, Select } from 'antd';

import { api, put, type Environment, type Product } from '../api';

/** 环境登记/编辑弹窗：保存后落盘为 data/environments/<id>.yaml。 */
export default function EnvironmentModal({ open, editing, products, onClose, onSaved }: {
  open: boolean;
  /** null=新建；否则为被编辑环境 */
  editing: Environment | null;
  products: Product[];
  onClose: () => void;
  onSaved: (environmentId: string) => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue(editing || {
      id: '', title: '', product_id: products[0]?.id,
      host: '127.0.0.1', port: 5432, database_name: 'postgres', database_user: 'postgres',
      deployment_target: '', deployment_config: '',
    });
  }, [open, editing, products, form]);

  async function save() {
    try {
      const values = await form.validateFields();
      setSaving(true);
      if (editing) {
        await put(`/environments/${encodeURIComponent(editing.id)}`, values);
      } else {
        await api('/environments', { method: 'POST', body: JSON.stringify(values) });
      }
      onSaved(values.id);
      onClose();
      message.success(editing ? '环境已更新' : '环境已登记');
    } catch (error) {
      if (error instanceof Error && error.message) message.error(error.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={editing ? '编辑环境' : '新增环境'}
      open={open}
      onOk={() => void save()}
      onCancel={onClose}
      confirmLoading={saving}
      okText="保存"
      width={650}
      destroyOnHidden
    >
      <Form form={form} layout="vertical">
        <div className="form-grid">
          <Form.Item label="环境 ID" name="id" rules={[{ required: true }, { pattern: /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/, message: '使用字母、数字、点、下划线或横线' }]}>
            <Input disabled={!!editing} />
          </Form.Item>
          <Form.Item label="名称" name="title" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item label="产品" name="product_id" rules={[{ required: true }]}>
            <Select options={products.map((item) => ({ label: item.title, value: item.id }))} />
          </Form.Item>
          <Form.Item label="主机" name="host" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item label="端口" name="port" rules={[{ required: true }]}>
            <InputNumber min={1} max={65535} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="数据库名" name="database_name"><Input /></Form.Item>
          <Form.Item label="数据库用户" name="database_user"><Input /></Form.Item>
          <Form.Item label="部署目标" name="deployment_target"><Input placeholder="streaming.demo" /></Form.Item>
        </div>
        <Form.Item label="部署配置文件" name="deployment_config">
          <Input placeholder="/path/to/pgcluster.yaml" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
