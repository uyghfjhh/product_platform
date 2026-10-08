import { useEffect, useState } from 'react';
import { Alert, App, Button, Checkbox, Empty, Input, InputNumber, Select, Space, Typography } from 'antd';
import { SaveOutlined } from '@ant-design/icons';
import { api } from '../../platform/api';
import type { Duration, LicenseDefaults, LicenseOptions, ProductDefaults } from './defaults';

function DurationInput({ value, onChange }: { value: Duration; onChange: (value: Duration) => void }) {
  return <Space wrap>{(['years', 'months', 'days'] as const).map((unit, index) =>
    <Space key={unit}><InputNumber min={0} max={unit === 'years' ? 100 : unit === 'months' ? 1200 : 36600}
      precision={0} value={value[unit]} onChange={(number) => onChange({ ...value, [unit]: number ?? 0 })} />
      <span>{['年', '月', '天'][index]}</span></Space>)}</Space>;
}

export default function LicenseDefaultsView() {
  const { message } = App.useApp();
  const [settings, setSettings] = useState<LicenseDefaults | null>(null);
  const [keys, setKeys] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [macText, setMacText] = useState('');
  useEffect(() => {
    void api<LicenseOptions>('/licenses/options').then((options) => {
      setSettings(options.defaults); setKeys(options.usable_key_versions);
      setMacText(options.defaults.mac_addrs.join('\n'));
    }).catch((cause) => message.error(cause.message));
  }, [message]);
  if (!settings) return <Empty description="正在读取默认设置" />;
  function productChange(name: string, update: Partial<ProductDefaults>) {
    setSettings((current) => current && ({ ...current, products: {
      ...current.products, [name]: { ...current.products[name], ...update },
    } }));
  }
  async function save() {
    if (!settings) return;
    setSaving(true);
    try {
      const data = await api<LicenseDefaults>('/licenses/defaults', { method: 'PUT',
        body: JSON.stringify({ ...settings, mac_addrs: macText.split(/[\s,;]+/).filter(Boolean).map((v) => v.replaceAll('-', ':')) }) });
      setSettings(data); setMacText(data.mac_addrs.join('\n'));
      message.success('默认设置已保存，下次打开 License 生成页时生效');
    } catch (cause) { message.error((cause as Error).message); }
    finally { setSaving(false); }
  }
  return <>
    <div className="page-heading"><Typography.Title level={3}>License 默认设置</Typography.Title></div>
    <Alert type="info" showIcon message="设置用于后续签发的预填内容；本次签发仍可修改，已生成的授权文件不受影响。" />
    <div className="form-surface">
      <Typography.Title level={5}>通用默认值</Typography.Title>
      <div className="form-grid">
        <label>厂商名称<Input value={settings.vendor} onChange={(e) => setSettings({ ...settings, vendor: e.target.value })} /></label>
        <label>密钥版本<Select style={{ width: '100%' }} allowClear placeholder="自动选择最新有效密钥"
          value={settings.license_version ?? undefined} options={keys.map((key) => ({ label: key, value: key }))}
          onChange={(value) => setSettings({ ...settings, license_version: value ?? null })} /></label>
        <label>默认密钥口令<Input.Password autoComplete="off" value={settings.default_password}
          onChange={(e) => setSettings({ ...settings, default_password: e.target.value })} /></label>
        <label>生效日期<Input value="当天（每次打开生成页重新计算）" readOnly /></label>
        <label>用途<Input value={settings.purpose} onChange={(e) => setSettings({ ...settings, purpose: e.target.value })} /></label>
      </div>
      <Checkbox checked={settings.save_to_directory} onChange={(e) => setSettings({ ...settings, save_to_directory: e.target.checked })}>默认同时保存到服务器目录</Checkbox>
      <label style={{ display: 'block', margin: '12px 0' }}>默认保存目录<Input value={settings.output_directory}
        onChange={(e) => setSettings({ ...settings, output_directory: e.target.value })} /></label>
      <Typography.Paragraph type="secondary">生成文件为 license.dat，同名文件会被替换。</Typography.Paragraph>
      <Typography.Paragraph>默认有效期（从生效日期计算）</Typography.Paragraph>
      <DurationInput value={settings.validity} onChange={(validity) => setSettings({ ...settings, validity })} />
      <Typography.Paragraph style={{ marginTop: 16 }}>常用 MAC 地址（可留空，多地址按行分隔）</Typography.Paragraph>
      <Input.TextArea rows={3} value={macText} onChange={(e) => setMacText(e.target.value)} />
      <Typography.Title level={5}>产品默认值</Typography.Title>
      <Typography.Paragraph type="secondary">版本需与实际产品二进制匹配。这里设置后续签发时自动填写的版本。</Typography.Paragraph>
      {Object.entries(settings.products).map(([name, product]) => <div key={name} style={{ borderTop: '1px solid var(--border-color, #394458)', padding: '16px 0' }}>
        <Space wrap><Typography.Text strong>{name}</Typography.Text>
          <Checkbox checked={product.selected} onChange={(e) => productChange(name, { selected: e.target.checked })}>默认授权</Checkbox></Space>
        <div className="form-grid" style={{ marginTop: 12 }}>
          <label>默认版本<Input value={product.default_version}
            onChange={(e) => productChange(name, { default_version: e.target.value })} /></label>
        </div>
        <Checkbox checked={product.validity === null} onChange={(e) => productChange(name, { validity: e.target.checked ? null : { ...settings.validity } })}>继承全局有效期</Checkbox>
        {product.validity && <div style={{ marginTop: 12 }}><DurationInput value={product.validity}
          onChange={(validity) => productChange(name, { validity })} /></div>}
      </div>)}
      <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>保存默认设置</Button>
    </div>
  </>;
}
