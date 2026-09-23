import { useEffect, useState } from 'react';
import { Alert, App, Button, Empty, Form, Input, Select, Space, Typography } from 'antd';
import { DownloadOutlined, MinusCircleOutlined, PlusOutlined } from '@ant-design/icons';

import { api } from '../api';

type Options = {
  vendor: string;
  products: { name: string; version: string }[];
  key_versions: string[];
};

type FormData = {
  license_version: string;
  start_at: string;
  purpose: string;
  macs: string;
  password: string;
  products: { name: string; version: string; expiration_at: string }[];
};

export default function LicensePage() {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormData>();
  const [options, setOptions] = useState<Options | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    void api<Options>('/licenses/options').then((data) => {
      setOptions(data);
      form.setFieldValue('license_version', data.key_versions.at(-1));
    }).catch((cause) => message.error(cause.message));
  }, [form, message]);

  async function generateLicense(values: FormData) {
    setLoading(true);
    try {
      const response = await fetch('/api/v1/licenses/generate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...values,
          mac_addrs: values.macs.split(/[\s,;]+/).filter(Boolean),
          macs: undefined,
        }),
      });
      if (!response.ok) {
        const detail = await response.json();
        throw new Error(typeof detail.detail === 'string' ? detail.detail : '生成失败，请检查表单与密钥口令');
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'license.dat';
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      form.setFieldValue('password', '');
      message.success('License 已生成并下载');
    } catch (cause) {
      message.error((cause as Error).message);
    } finally { setLoading(false); }
  }

  return (
    <>
      <div className="page-heading"><div><Typography.Title level={3}>License 生成</Typography.Title><Typography.Text type="secondary">{options?.vendor || '产品授权文件'}</Typography.Text></div></div>
      {options && options.key_versions.length === 0 ? <Alert type="warning" showIcon message="尚未发现可用密钥" description="请在平台配置的密钥目录中放入有效的密钥版本。" /> : null}
      {!options ? <Empty description="正在读取产品与密钥配置" /> : <div className="form-surface">
        <Form<FormData> form={form} layout="vertical" onFinish={(values) => void generateLicense(values)} initialValues={{ products: [{}], start_at: new Date().toISOString().slice(0, 10) }}>
          <div className="form-grid">
            <Form.Item label="密钥版本" name="license_version" rules={[{ required: true }]}><Select options={options.key_versions.map((value) => ({ label: value, value }))} placeholder="选择密钥版本" /></Form.Item>
            <Form.Item label="生效日期" name="start_at" rules={[{ required: true }]}><Input type="date" /></Form.Item>
          </div>
          <Typography.Title level={5}>授权产品</Typography.Title>
          <Form.List name="products">{(fields, { add, remove }) => <>
            {fields.map(({ key, name }) => <div className="license-product-row" key={key}>
              <Form.Item name={[name, 'name']} label="产品" rules={[{ required: true }]}>
                <Select options={options.products.map((item) => ({ label: item.name, value: item.name }))} onChange={(value) => {
                  const product = options.products.find((item) => item.name === value);
                  form.setFieldValue(['products', name, 'version'], product?.version || '');
                }} />
              </Form.Item>
              <Form.Item name={[name, 'version']} label="版本" rules={[{ required: true }]}><Input /></Form.Item>
              <Form.Item name={[name, 'expiration_at']} label="到期日期" rules={[{ required: true }]}><Input type="date" /></Form.Item>
              <Button type="text" danger icon={<MinusCircleOutlined />} onClick={() => remove(name)} disabled={fields.length <= 1} aria-label="移除授权产品" />
            </div>)}
            <Button type="dashed" icon={<PlusOutlined />} onClick={() => add()}>添加产品</Button>
          </>}</Form.List>
          <div className="form-spacer" />
          <Form.Item name="macs" label="绑定 MAC 地址" extra="多个地址按行分隔" rules={[{ required: true }]}><Input.TextArea rows={3} placeholder="02:42:8e:0f:0b:1b" /></Form.Item>
          <Form.Item name="purpose" label="用途"><Input placeholder="测试环境 / 正式环境" /></Form.Item>
          <Form.Item name="password" label="密钥口令" rules={[{ required: true }]}><Input.Password autoComplete="new-password" /></Form.Item>
          <Space><Button type="primary" htmlType="submit" icon={<DownloadOutlined />} loading={loading} disabled={!options.key_versions.length}>生成并下载</Button><Typography.Text type="secondary">直接生成文件</Typography.Text></Space>
        </Form>
      </div>}
    </>
  );
}
