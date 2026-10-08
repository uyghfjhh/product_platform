import { useEffect, useRef, useState } from 'react';
import { Alert, App, Button, Card, Dropdown, Empty, Form, Input, Modal, Space, Table, Tag, Typography } from 'antd';
import { KeyOutlined, PlusOutlined } from '@ant-design/icons';

import { api } from '../../platform/api';

import type { LicenseOptions as Options } from './defaults';
import './LicenseKeysView.css';

type KeyMetadata = {
  version: string;
  public_key: string;
  fingerprint: string;
  revoked?: boolean;
  revoked_at?: string | null;
};

/** 密钥管理：版本列表、生成、口令轮换、默认版本、删除。 */
export default function LicenseKeysView() {
  const { message } = App.useApp();
  const [options, setOptions] = useState<Options | null>(null);
  const [keyLoading, setKeyLoading] = useState(false);
  const [keyVersion, setKeyVersion] = useState('');
  const [keyInfo, setKeyInfo] = useState<KeyMetadata | null>(null);
  const [keyForm] = Form.useForm<{ version: string; password: string }>();
  const [rotateForm] = Form.useForm<{ old_password: string; new_password: string }>();
  const [deleteForm] = Form.useForm<{ password: string }>();
  const [createOpen, setCreateOpen] = useState(false);
  const [rotateOpen, setRotateOpen] = useState(false);
  const selectionSequence = useRef(0);
  const [deleteOpen, setDeleteOpen] = useState(false);

  useEffect(() => {
    void api<Options>('/licenses/options').then((data) => {
      setOptions(data);
      const latest = data.key_versions.includes(data.defaults.license_version || '')
        ? data.defaults.license_version! : data.key_versions.at(-1) || '';
      setKeyVersion(latest);
      if (latest) void refreshKey(latest);
    }).catch((cause) => message.error(cause.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refreshKey(version: string) {
    if (!version) return;
    const sequence = ++selectionSequence.current;
    setKeyVersion(version);
    setKeyInfo(null);
    try {
      const info = await api<KeyMetadata>(`/licenses/keys/${encodeURIComponent(version)}`);
      if (sequence !== selectionSequence.current) return false;
      setKeyInfo(info);
      return true;
    } catch (cause) {
      if (sequence !== selectionSequence.current) return;
      setKeyInfo(null);
      message.error((cause as Error).message);
      return false;
    }
  }

  async function createKey(values: { version: string; password: string }) {
    setKeyLoading(true);
    try {
      const info = await api<KeyMetadata>('/licenses/keys', {
        method: 'POST', body: JSON.stringify(values),
      });
      setKeyInfo(info);
      setKeyVersion(info.version);
      setCreateOpen(false);
      keyForm.resetFields();
      setOptions(await api<Options>('/licenses/options'));
      message.success(`密钥版本 ${info.version} 已生成`);
    } catch (cause) {
      message.error((cause as Error).message);
    } finally { setKeyLoading(false); }
  }

  async function rotateKey(values: { old_password: string; new_password: string }) {
    if (!keyVersion) return;
    setKeyLoading(true);
    try {
      const info = await api<KeyMetadata>(
        `/licenses/keys/${encodeURIComponent(keyVersion)}/password`,
        { method: 'POST', body: JSON.stringify(values) });
      setKeyInfo(info);
      setRotateOpen(false);
      rotateForm.resetFields();
      message.success('密钥口令已修改');
    } catch (cause) {
      message.error((cause as Error).message);
    } finally { setKeyLoading(false); }
  }

  async function removeKey(password: string) {
    if (!keyVersion) return;
    setKeyLoading(true);
    try {
      await api(`/licenses/keys/${encodeURIComponent(keyVersion)}`, {
        method: 'DELETE', body: JSON.stringify({ password }),
      });
      const refreshed = await api<Options>('/licenses/options');
      setOptions(refreshed);
      setKeyInfo(null);
      const next = refreshed.key_versions.at(-1) || '';
      setKeyVersion(next);
      if (next) await refreshKey(next);
      setDeleteOpen(false);
      deleteForm.resetFields();
      message.success('密钥版本已删除');
    } catch (cause) {
      message.error((cause as Error).message);
    } finally { setKeyLoading(false); }
  }

  async function setDefaultKey(version: string) {
    setKeyLoading(true);
    try {
      await api('/licenses/default-key', { method: 'PUT', body: JSON.stringify({ version }) });
      setOptions(await api<Options>('/licenses/options'));
      message.success(`默认密钥已设为 ${version}`);
    } catch (cause) { message.error((cause as Error).message); }
    finally { setKeyLoading(false); }
  }

  async function rowAction(version: string, action: string) {
    if (!await refreshKey(version)) return;
    if (action === 'rotate') openRotate();
    if (action === 'delete') {
      deleteForm.setFieldValue('password', options?.defaults.default_password || '');
      setDeleteOpen(true);
    }
  }

  function openCreate() {
    const next = Math.max(0, ...(options?.key_versions || []).map((v) => Number(v.split('.')[1]))) + 1;
    keyForm.setFieldsValue({ version: `1.${next}`, password: options?.defaults.default_password || '' });
    setCreateOpen(true);
  }

  function openRotate() {
    rotateForm.setFieldsValue({ old_password: options?.defaults.default_password || '', new_password: options?.defaults.default_password || '' });
    setRotateOpen(true);
  }

  return (
    <>
      <div className="page-heading">
        <div><Typography.Title level={3}>密钥管理</Typography.Title>
          <Typography.Text type="secondary">{options?.vendor || ''} · 管理签名密钥、口令与签发状态</Typography.Text></div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate} disabled={!options || keyLoading}>生成新密钥</Button>
      </div>
      {!options ? <Empty description="正在读取密钥配置" /> : <div className="license-keys-layout">
        <Card title={<Space><KeyOutlined />密钥版本<Tag>{options.key_versions.length}</Tag></Space>}>
          <Table size="middle" rowKey="version" pagination={{ pageSize: 6, hideOnSinglePage: true }}
            rowClassName={(row) => row.version === keyVersion ? 'license-key-selected' : ''}
            dataSource={[...options.key_versions].reverse().map((version) => ({ version }))}
            onRow={(row) => ({ onClick: () => { if (!keyLoading) void refreshKey(row.version); } })}
            columns={[
              { title: '版本', dataIndex: 'version', render: (version: string) => <Space wrap>
                <Typography.Text strong>{version}</Typography.Text>
                {version === options.defaults.license_version && <Tag color="blue">默认</Tag>}</Space> },
              { title: '签发状态', render: (_, row) => options.usable_key_versions.includes(row.version)
                ? <Tag color="success">可签发</Tag> : <Tag color="error">已撤销</Tag> },
              { title: '操作', render: (_, row) => <Space wrap size={0}>
                <Button type="link" disabled={keyLoading} onClick={(event) => {
                  event.stopPropagation(); void rowAction(row.version, 'rotate');
                }}>修改口令</Button>
                <Button type="link" disabled={keyLoading || row.version === options.defaults.license_version || !options.usable_key_versions.includes(row.version)}
                  onClick={(event) => { event.stopPropagation(); void setDefaultKey(row.version); }}>
                  {row.version === options.defaults.license_version ? '已是默认' : '设为默认'}</Button>
                <Dropdown trigger={['click']} menu={{ items: [
                  { key: 'delete', label: '删除版本', danger: true, disabled: options.key_versions.length <= 1 },
                ], onClick: ({ key, domEvent }) => { domEvent.stopPropagation(); void rowAction(row.version, key); } }}>
                  <Button type="text" disabled={keyLoading} onClick={(event) => event.stopPropagation()}>更多</Button>
                </Dropdown>
              </Space> },
            ]} />
        </Card>
        <Card title={keyVersion ? `版本 ${keyVersion}` : '密钥详情'}
          extra={keyInfo && <Tag color={keyInfo.revoked ? 'error' : 'success'}>{keyInfo.revoked ? '已撤销' : '可签发'}</Tag>}>
          {!keyInfo ? <Empty description={keyVersion ? '正在读取密钥详情' : '生成新密钥以开始签发'} /> : <>
            <div className="license-key-detail"><Typography.Text type="secondary">公钥</Typography.Text>
              <Typography.Paragraph copyable={{ text: keyInfo.public_key }} code>{keyInfo.public_key}</Typography.Paragraph></div>
            <div className="license-key-detail"><Typography.Text type="secondary">公钥 SHA256 指纹</Typography.Text>
              <Typography.Paragraph copyable={{ text: keyInfo.fingerprint }} code>{keyInfo.fingerprint}</Typography.Paragraph>
              <Typography.Text type="secondary">用于核对两处公钥是否一致。</Typography.Text></div>
            {keyInfo.revoked && <Alert type="warning" showIcon message="已停止使用此版本签发新的 License"
              description={keyInfo.revoked_at ? `撤销时间：${keyInfo.revoked_at}` : undefined} />}
            <div className="license-key-actions"><Space wrap>
              <Button disabled={keyLoading} onClick={openRotate}>修改口令</Button>
              <Button disabled={keyLoading || keyInfo.revoked || keyVersion === options.defaults.license_version}
                onClick={() => void setDefaultKey(keyVersion)}>{keyVersion === options.defaults.license_version ? '已是默认' : '设为默认'}</Button>
              <Button danger disabled={keyLoading || options.key_versions.length <= 1} onClick={() => {
                deleteForm.setFieldValue('password', options.defaults.default_password); setDeleteOpen(true);
              }}>删除版本</Button>
            </Space><Typography.Paragraph type="secondary">平台至少保留一个密钥版本。修改口令不改变全局默认口令。</Typography.Paragraph></div>
          </>}
        </Card>
      </div>}
      <Modal title="生成新密钥" open={createOpen} footer={null} destroyOnHidden
        onCancel={() => { if (!keyLoading) { setCreateOpen(false); keyForm.resetFields(); } }}>
        <Form form={keyForm} layout="vertical" onFinish={(values) => void createKey(values)}>
          <Form.Item label="新版本" name="version" rules={[{ required: true, pattern: /^1\.[1-9][0-9]*$/, message: '格式应为 1.<数字>' }]}><Input /></Form.Item>
          <Form.Item label="密钥口令" name="password" rules={[{ required: true }]}><Input.Password autoComplete="new-password" /></Form.Item>
          <Button type="primary" htmlType="submit" loading={keyLoading}>生成密钥</Button>
        </Form>
      </Modal>
      <Modal title={`修改版本 ${keyVersion} 的口令`} open={rotateOpen} footer={null} destroyOnHidden
        onCancel={() => { if (!keyLoading) { setRotateOpen(false); rotateForm.resetFields(); } }}>
        <Form form={rotateForm} layout="vertical" onFinish={(values) => void rotateKey(values)}>
          <Form.Item label="当前口令" name="old_password" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
          <Form.Item label="新口令" name="new_password" rules={[{ required: true }]}><Input.Password autoComplete="new-password" /></Form.Item>
          <Button type="primary" htmlType="submit" loading={keyLoading}>确认修改</Button>
        </Form>
      </Modal>
      <Modal title={`删除密钥版本 ${keyVersion || ''}`} open={deleteOpen}
        onCancel={() => { setDeleteOpen(false); deleteForm.resetFields(); }}
        footer={null} destroyOnHidden>
        <Typography.Paragraph type="secondary">
          需要当前口令；平台至少保留一个密钥版本。
        </Typography.Paragraph>
        <Form form={deleteForm} layout="vertical"
          onFinish={(values) => void removeKey(values.password)}>
          <Form.Item name="password" label="当前口令" rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Space>
            <Button onClick={() => setDeleteOpen(false)}>取消</Button>
            <Button danger type="primary" htmlType="submit" loading={keyLoading}>确认删除</Button>
          </Space>
        </Form>
      </Modal>
    </>
  );
}
