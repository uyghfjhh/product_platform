import { useEffect, useState } from 'react';
import { Alert, App, Button, Form, Input, Modal, Space, Typography } from 'antd';
import { api, put, type Environment } from '../../../frontend/src/platform/api';

type Build = { fbasecman_bin: string; license_dir: string; source: string; version?: string;
  sha256?: string; resolved_path?: string; error?: string };
export default function TestSettings({ environment, onChanged }: {
  environment: Environment; onChanged: () => Promise<void> | void;
}) {
  const [value, setValue] = useState<Build>();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const path = `/environments/${encodeURIComponent(environment.id)}/fbasecman-test-settings`;
  useEffect(() => {
    let active = true;
    void api<Build>(path).then((data) => { if (active) { setValue(data); setError(''); } })
      .catch((cause: Error) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [path, environment.product_test_settings]);
  async function save() {
    try {
      const fields = await form.validateFields();
      setBusy(true);
      const data = await put<Build>(path, fields);
      setValue(data); setError(''); await onChanged(); setOpen(false);
      message.success('被测 fbasecman 已保存，回归与常稳共用');
    } catch (cause) { if (cause instanceof Error) setError(cause.message); }
    finally { setBusy(false); }
  }
  return <Space wrap>
    <Typography.Text type="secondary">被测 fbasecman：{value?.version?.split('\n')[0] || (value?.error ? '配置无效' : '读取中')}</Typography.Text>
    <Button size="small" onClick={() => { form.setFieldsValue(value); setOpen(true); }}>配置被测 fbasecman</Button>
    <Modal title="被测 fbasecman · 回归与常稳共用" open={open} onCancel={() => setOpen(false)}
      onOk={() => void save()} confirmLoading={busy} okText="检查并保存" width={760}>
      {(error || value?.error) && <Alert type="error" showIcon message={error || value?.error} />}
      <Typography.Paragraph>这里选择平台控制机上的构建文件。数据库部署拓扑独立配置；修改后需要重新审阅负载计划。</Typography.Paragraph>
      <Form form={form} layout="vertical">
        <Form.Item label="fbasecman 可执行文件绝对路径" name="fbasecman_bin" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item label="fbasecman License 目录绝对路径" name="license_dir" rules={[{ required: true }]}><Input /></Form.Item>
      </Form>
      <Typography.Paragraph type="secondary">当前来源：{value?.source}</Typography.Paragraph>
      {value?.version && <pre style={{ whiteSpace: 'pre-wrap' }}>{value.version}</pre>}
      {value?.resolved_path && <Typography.Paragraph>实际文件：{value.resolved_path}</Typography.Paragraph>}
      {value?.sha256 && <Typography.Paragraph copyable>SHA256：{value.sha256}</Typography.Paragraph>}
    </Modal>
  </Space>;
}
