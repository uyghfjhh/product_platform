import { useEffect, useState } from 'react';
import { Alert, App, Button, Empty, Form, Input, Modal, Select, Space, Typography } from 'antd';
import { DownloadOutlined, KeyOutlined, MinusCircleOutlined, PlusOutlined, SafetyCertificateOutlined } from '@ant-design/icons';

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
type KeyMetadata = { version: string; public_key: string; fingerprint: string; revoked?: boolean; revoked_at?: string | null };

export default function LicensePage() {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormData>();
  const [options, setOptions] = useState<Options | null>(null);
  const [loading, setLoading] = useState(false);
  const [keyLoading, setKeyLoading] = useState(false);
  const [keyVersion, setKeyVersion] = useState('');
  const [keyInfo, setKeyInfo] = useState<KeyMetadata | null>(null);
  const [keyForm] = Form.useForm<{ version: string; password: string }>();
  const [rotateForm] = Form.useForm<{ old_password: string; new_password: string }>();
  const [deleteForm] = Form.useForm<{ password: string }>();
  const [revokeForm] = Form.useForm<{ password: string }>();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [revokeOpen, setRevokeOpen] = useState(false);

  useEffect(() => {
    void api<Options>('/licenses/options').then((data) => {
      setOptions(data);
      form.setFieldValue('license_version', data.key_versions.at(-1));
      const latest = data.key_versions.at(-1) || '';
      setKeyVersion(latest);
      if (latest) {
        void api<KeyMetadata>(`/licenses/keys/${encodeURIComponent(latest)}`).then(setKeyInfo)
          .catch((cause) => message.error(cause.message));
      }
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

  async function refreshKey(version: string) {
    if (!version) return;
    setKeyVersion(version);
    try { setKeyInfo(await api<KeyMetadata>(`/licenses/keys/${encodeURIComponent(version)}`)); }
    catch (cause) { setKeyInfo(null); message.error((cause as Error).message); }
  }

  async function createKey(values: { version: string; password: string }) {
    setKeyLoading(true);
    try {
      const info = await api<KeyMetadata>('/licenses/keys', { method: 'POST', body: JSON.stringify(values) });
      setKeyInfo(info); setKeyVersion(info.version); keyForm.resetFields();
      const refreshed = await api<Options>('/licenses/options'); setOptions(refreshed);
      message.success(`密钥版本 ${info.version} 已生成`);
    } catch (cause) { message.error((cause as Error).message); }
    finally { setKeyLoading(false); }
  }

  async function rotateKey(values: { old_password: string; new_password: string }) {
    if (!keyVersion) return;
    setKeyLoading(true);
    try {
      const info = await api<KeyMetadata>(`/licenses/keys/${encodeURIComponent(keyVersion)}/password`, { method: 'POST', body: JSON.stringify(values) });
      setKeyInfo(info); rotateForm.resetFields(); message.success('密钥口令已修改');
    } catch (cause) { message.error((cause as Error).message); }
    finally { setKeyLoading(false); }
  }

  async function removeKey(password: string) {
    if (!keyVersion) return;
    setKeyLoading(true);
    try {
      await api(`/licenses/keys/${encodeURIComponent(keyVersion)}`, { method: 'DELETE', body: JSON.stringify({ password }) });
      const refreshed = await api<Options>('/licenses/options');
      setOptions(refreshed); setKeyInfo(null); setKeyVersion(refreshed.key_versions.at(-1) || ''); setDeleteOpen(false); deleteForm.resetFields();
      message.success('密钥版本已删除');
    } catch (cause) { message.error((cause as Error).message); }
    finally { setKeyLoading(false); }
  }

  async function revokeKey(password: string) {
    if (!keyVersion) return;
    setKeyLoading(true);
    try {
      const info = await api<KeyMetadata>(`/licenses/keys/${encodeURIComponent(keyVersion)}/revoke`, { method: 'POST', body: JSON.stringify({ password }) });
      setKeyInfo(info); setRevokeOpen(false); revokeForm.resetFields(); message.success('密钥版本已撤销，不能继续签发');
    } catch (cause) { message.error((cause as Error).message); }
    finally { setKeyLoading(false); }
  }

  return (
    <>
      <div className="page-heading"><div><Typography.Title level={3}>License 管理</Typography.Title><Typography.Text type="secondary">{options?.vendor ? `${options.vendor} 产品授权管理与签发` : '产品授权文件签发与管理'}</Typography.Text></div></div>
      {options && options.key_versions.length === 0 ? <Alert type="warning" showIcon message="尚未发现可用密钥" description="请在平台配置的密钥目录中放入有效的密钥版本。" /> : null}
      {!options ? <Empty description="正在读取产品与密钥配置" /> : <>
        <div className="form-surface license-key-manager">
          <Typography.Title level={5}><KeyOutlined /> 密钥版本管理</Typography.Title>
          <div className="form-grid">
            <Form.Item label="查看版本"><Select value={keyVersion || undefined} placeholder="选择密钥版本" options={options.key_versions.map((value) => ({ label: value, value }))} onChange={(value) => void refreshKey(value)} /></Form.Item>
            <Form<{ version: string; password: string }> form={keyForm} layout="vertical" onFinish={(values) => void createKey(values)}>
              <Form.Item label="生成新版本" name="version" rules={[{ required: true, pattern: /^1\.[1-9][0-9]*$/, message: '格式应为 1.<数字>' }]}><Input placeholder="1.2" /></Form.Item>
              <Form.Item label="新口令" name="password" rules={[{ required: true }]}><Input.Password autoComplete="new-password" /></Form.Item>
              <Button htmlType="submit" icon={<SafetyCertificateOutlined />} loading={keyLoading}>生成密钥</Button>
            </Form>
          </div>
          {keyInfo && <Typography.Paragraph copyable={{ text: keyInfo.public_key }}><strong>公钥：</strong>{keyInfo.public_key}<br /><strong>指纹：</strong>{keyInfo.fingerprint}<br /><strong>状态：</strong>{keyInfo.revoked ? `已撤销 ${keyInfo.revoked_at || ''}` : '可签发'}</Typography.Paragraph>}
          {keyVersion && <Form form={rotateForm} layout="inline" onFinish={(values) => void rotateKey(values)}>
            <Form.Item name="old_password" rules={[{ required: true }]}><Input.Password placeholder="旧口令" /></Form.Item>
            <Form.Item name="new_password" rules={[{ required: true }]}><Input.Password placeholder="新口令" /></Form.Item>
            <Button htmlType="submit" loading={keyLoading}>修改口令</Button>
            <Button danger loading={keyLoading} onClick={() => setDeleteOpen(true)}>删除版本</Button>
            {!keyInfo?.revoked && <Button loading={keyLoading} onClick={() => setRevokeOpen(true)}>撤销签发</Button>}
          </Form>}
        </div>
        <div className="form-surface">
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
        </div>
      </>}
      <Modal title={`删除密钥版本 ${keyVersion || ''}`} open={deleteOpen} onCancel={() => { setDeleteOpen(false); deleteForm.resetFields(); }} footer={null} destroyOnHidden>
        <Typography.Paragraph type="secondary">需要当前口令；平台至少保留一个密钥版本。</Typography.Paragraph>
        <Form form={deleteForm} layout="vertical" onFinish={(values) => void removeKey(values.password)}>
          <Form.Item name="password" label="当前口令" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
          <Space><Button onClick={() => setDeleteOpen(false)}>取消</Button><Button danger type="primary" htmlType="submit" loading={keyLoading}>确认删除</Button></Space>
        </Form>
      </Modal>
      <Modal title={`撤销密钥版本 ${keyVersion || ''}`} open={revokeOpen} onCancel={() => { setRevokeOpen(false); revokeForm.resetFields(); }} footer={null} destroyOnHidden>
        <Typography.Paragraph type="secondary">撤销后保留公钥和历史文件，但禁止继续生成新的 License。</Typography.Paragraph>
        <Form form={revokeForm} layout="vertical" onFinish={(values) => void revokeKey(values.password)}>
          <Form.Item name="password" label="当前口令" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
          <Space><Button onClick={() => setRevokeOpen(false)}>取消</Button><Button danger type="primary" htmlType="submit" loading={keyLoading}>确认撤销</Button></Space>
        </Form>
      </Modal>
    </>
  );
}
