import { useEffect, useState } from 'react';
import { Alert, App, Button, Form, Input, Modal, Radio, Space, Typography } from 'antd';
import { api, put, type Environment } from '../../../frontend/src/platform/api';

type Choice = { id: string; name: string; fbasecman_bin: string; license_dir: string };
type Build = Choice & { source: string; version?: string; sha256?: string; resolved_path?: string;
  error?: string; build_name?: string; builds: Choice[]; active_build_id: string };
export default function TestSettings({ environment, onChanged }: {
  environment: Environment; onChanged: () => Promise<void> | void;
}) {
  const [value, setValue] = useState<Build>();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [form] = Form.useForm();
  const selected = Form.useWatch('active_build_id', form);
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
      if (!fields.builds?.some((item: Choice) => item.id === fields.active_build_id)) {
        setError('请选择一个当前生效版本'); return;
      }
      setBusy(true);
      const data = await put<Build>(path, fields);
      setValue(data); setError(''); await onChanged(); setOpen(false);
      message.success('被测版本已保存，回归与常稳共用');
    } catch (cause) { if (cause instanceof Error) setError(cause.message); }
    finally { setBusy(false); }
  }
  return <Space wrap>
    <Typography.Text type="secondary">被测 fbasecman：{value?.build_name || '默认版本'} · {value?.version?.split('\n')[0] || (value?.error ? '配置无效' : '读取中')}</Typography.Text>
    <Button size="small" disabled={!value} onClick={() => {
      form.resetFields(); form.setFieldsValue({ builds: value?.builds, active_build_id: value?.active_build_id });
      setError(''); setOpen(true);
    }}>配置被测 fbasecman</Button>
    <Modal title="被测 fbasecman · 回归与常稳共用" open={open} onCancel={() => setOpen(false)}
      onOk={() => void save()} confirmLoading={busy} okText="检查当前版本并保存" width={860}>
      {error && <Alert type="error" showIcon message={error} />}
      <Typography.Paragraph>保存多个控制机上的构建版本，选择一个当前生效版本。保存后新任务使用该版本；已启动的任务保留原版本。数据库拓扑独立配置，修改后需要重新审阅负载计划。</Typography.Paragraph>
      <Form form={form} layout="vertical">
        <Form.Item name="active_build_id" hidden rules={[{ required: true }]}><Input /></Form.Item>
        <Form.List name="builds">
          {(fields, { add, remove }) => <>
            {fields.map((field) => {
              const id = form.getFieldValue(['builds', field.name, 'id']);
              return <div key={field.key} style={{ border: '1px solid #475569', padding: 16, borderRadius: 8, marginBottom: 16 }}>
                <Space style={{ marginBottom: 12 }}>
                  <Radio checked={selected === id} onChange={() => form.setFieldValue('active_build_id', id)}>当前生效</Radio>
                  <Button danger size="small" disabled={fields.length === 1} onClick={() => {
                    if (selected === id) {
                      const replacement = fields.find((other) => other.key !== field.key);
                      form.setFieldValue('active_build_id', form.getFieldValue(['builds', replacement!.name, 'id']));
                    }
                    remove(field.name);
                  }}>删除版本</Button>
                </Space>
                <Form.Item name={[field.name, 'id']} hidden><Input /></Form.Item>
                <Form.Item label="版本名称" name={[field.name, 'name']} rules={[{ required: true, whitespace: true }]}><Input placeholder="例如 master 基线版、hint 修改版" maxLength={100} /></Form.Item>
                <Form.Item label="fbasecman 可执行文件绝对路径" name={[field.name, 'fbasecman_bin']} rules={[{ required: true, whitespace: true }]}><Input /></Form.Item>
                <Form.Item label="fbasecman License 目录绝对路径" name={[field.name, 'license_dir']} rules={[{ required: true, whitespace: true }]}><Input /></Form.Item>
              </div>;
            })}
            <Button disabled={fields.length >= 50} onClick={() => add({ id: `build-${Date.now()}-${Math.random().toString(36).slice(2)}`, name: '', fbasecman_bin: '', license_dir: value?.license_dir || '' })}>新增版本</Button>
          </>}
        </Form.List>
      </Form>
      <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>已保存的当前版本：{value?.build_name} · {value?.source}</Typography.Paragraph>
      {value?.error && <Alert type="warning" showIcon message={value.error} />}
      {value?.version && <pre style={{ whiteSpace: 'pre-wrap' }}>{value.version}</pre>}
      {value?.resolved_path && <Typography.Paragraph>实际文件：{value.resolved_path}</Typography.Paragraph>}
      {value?.sha256 && <Typography.Paragraph copyable>SHA256：{value.sha256}</Typography.Paragraph>}
    </Modal>
  </Space>;
}
