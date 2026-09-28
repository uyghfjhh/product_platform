import { useEffect, useState } from 'react';
import { Alert, App, Button, Empty, Form, Input, Modal, Space, Table, Tag, Typography } from 'antd';
import { KeyOutlined, SafetyCertificateOutlined } from '@ant-design/icons';

import { api } from '../../api';

type Options = {
  vendor: string;
  products: { name: string; version: string }[];
  key_versions: string[];
};
type KeyMetadata = {
  version: string;
  public_key: string;
  fingerprint: string;
  revoked?: boolean;
  revoked_at?: string | null;
};

/** 密钥管理：版本列表、生成、口令轮换、撤销、删除。 */
export default function LicenseKeysView() {
  const { message } = App.useApp();
  const [options, setOptions] = useState<Options | null>(null);
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
      const latest = data.key_versions.at(-1) || '';
      setKeyVersion(latest);
      if (latest) void refreshKey(latest);
    }).catch((cause) => message.error(cause.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refreshKey(version: string) {
    if (!version) return;
    setKeyVersion(version);
    try {
      setKeyInfo(await api<KeyMetadata>(`/licenses/keys/${encodeURIComponent(version)}`));
    } catch (cause) {
      setKeyInfo(null);
      message.error((cause as Error).message);
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
      setKeyVersion(refreshed.key_versions.at(-1) || '');
      setDeleteOpen(false);
      deleteForm.resetFields();
      message.success('密钥版本已删除');
    } catch (cause) {
      message.error((cause as Error).message);
    } finally { setKeyLoading(false); }
  }

  async function revokeKey(password: string) {
    if (!keyVersion) return;
    setKeyLoading(true);
    try {
      const info = await api<KeyMetadata>(
        `/licenses/keys/${encodeURIComponent(keyVersion)}/revoke`,
        { method: 'POST', body: JSON.stringify({ password }) });
      setKeyInfo(info);
      setRevokeOpen(false);
      revokeForm.resetFields();
      message.success('密钥版本已撤销，不能继续签发');
    } catch (cause) {
      message.error((cause as Error).message);
    } finally { setKeyLoading(false); }
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>密钥管理</Typography.Title>
          <Typography.Text type="secondary">
            {options?.vendor ? `${options.vendor} 签名密钥版本与生命周期` : '签名密钥版本与生命周期'}
          </Typography.Text>
        </div>
      </div>
      {options && options.key_versions.length === 0 && (
        <Alert type="warning" showIcon message="尚未发现可用密钥"
          description="可在下方生成第一个密钥版本。" />
      )}
      {!options ? <Empty description="正在读取密钥配置" /> : (
        <div className="form-surface license-key-manager">
          <Typography.Title level={5}><KeyOutlined /> 密钥版本</Typography.Title>
          <Table
            size="small"
            rowKey="version"
            pagination={false}
            dataSource={options.key_versions.map((version) => ({ version }))}
            columns={[
              { title: '版本', dataIndex: 'version' },
              {
                title: '状态', key: 'status',
                render: (_: unknown, row: { version: string }) => (
                  row.version === keyVersion && keyInfo?.revoked
                    ? <Tag color="error">已撤销</Tag>
                    : <Tag color="success">可用</Tag>
                ),
              },
              {
                title: '', key: 'select',
                render: (_: unknown, row: { version: string }) => (
                  <Button type="link" onClick={() => void refreshKey(row.version)}>
                    查看
                  </Button>
                ),
              },
            ]}
          />
          {keyInfo && (
            <Typography.Paragraph copyable={{ text: keyInfo.public_key }} style={{ marginTop: 12 }}>
              <strong>版本：</strong>{keyInfo.version}<br />
              <strong>公钥：</strong>{keyInfo.public_key}<br />
              <strong>SHA256 指纹：</strong>{keyInfo.fingerprint}<br />
              <strong>状态：</strong>{keyInfo.revoked ? `已撤销 ${keyInfo.revoked_at || ''}` : '可签发'}
            </Typography.Paragraph>
          )}
          <Form<{ version: string; password: string }> form={keyForm} layout="inline"
            onFinish={(values) => void createKey(values)} style={{ marginTop: 16 }}>
            <Form.Item label="新版本" name="version"
              rules={[{ required: true, pattern: /^1\.[1-9][0-9]*$/, message: '格式应为 1.<数字>' }]}>
              <Input placeholder="1.2" />
            </Form.Item>
            <Form.Item label="新口令" name="password" rules={[{ required: true }]}>
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Button htmlType="submit" icon={<SafetyCertificateOutlined />} loading={keyLoading}>
              生成密钥
            </Button>
          </Form>
          {keyVersion && (
            <Form form={rotateForm} layout="inline" style={{ marginTop: 12 }}
              onFinish={(values) => void rotateKey(values)}>
              <Form.Item name="old_password" rules={[{ required: true }]}>
                <Input.Password placeholder="旧口令" />
              </Form.Item>
              <Form.Item name="new_password" rules={[{ required: true }]}>
                <Input.Password placeholder="新口令" />
              </Form.Item>
              <Button htmlType="submit" loading={keyLoading}>修改口令</Button>
              <Button danger loading={keyLoading} onClick={() => setDeleteOpen(true)}>删除版本</Button>
              {!keyInfo?.revoked && (
                <Button loading={keyLoading} onClick={() => setRevokeOpen(true)}>撤销签发</Button>
              )}
            </Form>
          )}
        </div>
      )}
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
      <Modal title={`撤销密钥版本 ${keyVersion || ''}`} open={revokeOpen}
        onCancel={() => { setRevokeOpen(false); revokeForm.resetFields(); }}
        footer={null} destroyOnHidden>
        <Typography.Paragraph type="secondary">
          撤销后保留公钥和历史文件，但禁止继续生成新的 License。
        </Typography.Paragraph>
        <Form form={revokeForm} layout="vertical"
          onFinish={(values) => void revokeKey(values.password)}>
          <Form.Item name="password" label="当前口令" rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Space>
            <Button onClick={() => setRevokeOpen(false)}>取消</Button>
            <Button danger type="primary" htmlType="submit" loading={keyLoading}>确认撤销</Button>
          </Space>
        </Form>
      </Modal>
    </>
  );
}
