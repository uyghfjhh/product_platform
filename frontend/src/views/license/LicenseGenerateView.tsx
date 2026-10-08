import { useEffect, useState } from 'react';
import { Alert, App, Button, Checkbox, Empty, Form, Input, Select, Space, Typography } from 'antd';
import { DownloadOutlined, MinusCircleOutlined, PlusOutlined } from '@ant-design/icons';

import { api } from '../../platform/api';

import { expirationDate, localDate, type LicenseOptions as Options } from './defaults';

type FormData = {
  license_version: string;
  start_at: string;
  purpose: string;
  macs: string;
  password: string;
  save_to_directory: boolean;
  output_directory: string;
  products: { name: string; version: string; expiration_at: string }[];
};

const MAC_LINE = /^[0-9a-fA-F]{2}([:-][0-9a-fA-F]{2}){5}$/;

/** License 生成：密钥版本 + 生效日期 + 授权矩阵 + MAC 绑定 + 签名下载。 */
export default function LicenseGenerateView() {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormData>();
  const [options, setOptions] = useState<Options | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    void api<Options>('/licenses/options').then(setOptions)
      .catch((cause) => message.error(cause.message));
  }, [message]);

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
      const savedPath = response.headers.get('X-License-Saved-Path');
      message.success(savedPath ? `License 已下载并保存到 ${decodeURIComponent(savedPath)}` : 'License 已生成并下载');
    } catch (cause) {
      message.error((cause as Error).message);
    } finally { setLoading(false); }
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>License 生成</Typography.Title>
          <Typography.Text type="secondary">选择密钥版本签发授权文件并下载 license.dat</Typography.Text>
        </div>
      </div>
      {options && options.usable_key_versions.length === 0 && (
        <Alert type="warning" showIcon message="尚未发现可用密钥"
          description="请先在「密钥管理」页面生成密钥版本。" />
      )}
      {!options ? <Empty description="正在读取产品与密钥配置" /> : (
        <div className="form-surface">
          <Form<FormData> form={form} layout="vertical"
            onFinish={(values) => void generateLicense(values)}
            initialValues={{
              license_version: options.usable_key_versions.includes(options.defaults.license_version || '') ? options.defaults.license_version : options.usable_key_versions.at(-1),
              products: options.products.filter((item) => options.defaults.products[item.name].selected).map((item) => ({
                name: item.name, version: item.version,
                expiration_at: expirationDate(options.defaults.products[item.name].validity ?? options.defaults.validity),
              })),
              start_at: localDate(new Date()),
              purpose: options.defaults.purpose,
              password: options.defaults.default_password,
              macs: options.defaults.mac_addrs.join('\n'),
              save_to_directory: options.defaults.save_to_directory,
              output_directory: options.defaults.output_directory,
            }}>
            <div className="form-grid">
              <Form.Item label="密钥版本" name="license_version" rules={[{ required: true }]}>
                <Select options={options.usable_key_versions.map((value) => ({ label: value, value }))}
                  placeholder="选择密钥版本" />
              </Form.Item>
              <Form.Item label="生效日期" name="start_at" rules={[{ required: true }]}>
                <Input type="date" />
              </Form.Item>
            </div>
            <Typography.Title level={5}>授权产品</Typography.Title>
            <Form.List name="products" rules={[{ validator: (_, products) => products?.length
              ? Promise.resolve() : Promise.reject(new Error('请至少添加一个授权产品')) }]}>{(fields, { add, remove }, { errors }) => <>
              {fields.map(({ key, name }) => (
                <div className="license-product-row" key={key}>
                  <Form.Item name={[name, 'name']} label="产品" rules={[{ required: true }]}>
                    <Select options={options.products.map((item) => ({ label: item.name, value: item.name }))}
                      onChange={(value) => {
                        const product = options.products.find((item) => item.name === value);
                        form.setFieldValue(['products', name, 'version'], product?.version || '');
                        const start = form.getFieldValue('start_at');
                        form.setFieldValue(['products', name, 'expiration_at'], expirationDate(
                          options.defaults.products[value]?.validity ?? options.defaults.validity,
                          start ? new Date(`${start}T00:00:00`) : new Date()));
                      }} />
                  </Form.Item>
                  <Form.Item name={[name, 'version']} label="版本" rules={[{ required: true }]}>
                    <Input />
                  </Form.Item>
                  <Form.Item name={[name, 'expiration_at']} label="到期日期" rules={[{ required: true }]}>
                    <Input type="date" />
                  </Form.Item>
                  <Button type="text" danger icon={<MinusCircleOutlined />}
                    onClick={() => remove(name)} disabled={fields.length <= 1}
                    aria-label="移除授权产品" />
                </div>
              ))}
              <Button type="dashed" icon={<PlusOutlined />}
                onClick={() => add({ expiration_at: expirationDate(options.defaults.validity, new Date(`${form.getFieldValue('start_at')}T00:00:00`)) })}>添加产品</Button>
              <Form.ErrorList errors={errors} />
            </>}</Form.List>
            <div className="form-spacer" />
            <Form.Item name="macs" label="绑定 MAC 地址" extra="多网卡按行分隔录入"
              rules={[{
                required: true,
                validator: (_, value: string) => {
                  const bad = (value || '').split(/[\s,;]+/).filter(Boolean)
                    .filter((line: string) => !MAC_LINE.test(line));
                  return bad.length
                    ? Promise.reject(new Error(`MAC 格式无效: ${bad[0]}`))
                    : Promise.resolve();
                },
              }]}>
              <Input.TextArea rows={3} placeholder="02:42:8e:0f:0b:1b" />
            </Form.Item>
            <Form.Item name="purpose" label="用途"><Input placeholder="测试环境 / 正式环境" /></Form.Item>
            <Form.Item name="password" label="密钥口令" rules={[{ required: true }]}>
              <Input.Password autoComplete="current-password" />
            </Form.Item>
            <Form.Item name="save_to_directory" valuePropName="checked"><Checkbox>同时保存到服务器目录</Checkbox></Form.Item>
            <Form.Item noStyle shouldUpdate={(before, after) => before.save_to_directory !== after.save_to_directory}>
              {({ getFieldValue }) => getFieldValue('save_to_directory') && <Form.Item name="output_directory" label="服务器保存目录"
                extra="保存为 license.dat，同名文件会被替换；不影响浏览器下载。" rules={[{ required: true }]}><Input /></Form.Item>}
            </Form.Item>
            <Space>
              <Button type="primary" htmlType="submit" icon={<DownloadOutlined />}
                loading={loading} disabled={!options.usable_key_versions.length}>
                签名并下载 license.dat
              </Button>
            </Space>
          </Form>
        </div>
      )}
    </>
  );
}
