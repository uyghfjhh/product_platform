import { useEffect, useState } from 'react';
import { Descriptions, Modal, Space, Tag, Typography } from 'antd';
import { CheckCircleFilled } from '@ant-design/icons';

import { api, type Product } from '../platform/api';
import type { ThemeName } from '../platform/PlatformShell';

const THEMES: { key: ThemeName; title: string; description: string; swatch: string[] }[] = [
  { key: 'cman', title: '极客夜蓝', description: '暗黑冷蓝荧光', swatch: ['#0a0d14', '#38bdf8', '#111726'] },
  { key: 'dark', title: '深色石墨', description: '专业炭黑雅绿', swatch: ['#1f2329', '#83b49c', '#2a2f36'] },
  { key: 'soft', title: '柔和灰绿', description: '低对比度浅色护眼', swatch: ['#f2f5f1', '#537a6c', '#e3e9e2'] },
  { key: 'warm', title: '暖灰护眼', description: '暖灰纸质护眼', swatch: ['#f5f0e8', '#786959', '#ece5d8'] },
];

type Health = {
  status: string;
  storage: string;
  platform_dir: string;
  environment_dir: string;
};

export default function SettingsModal({ open, onClose, themeName, onThemeChange, products }: {
  open: boolean;
  onClose: () => void;
  themeName: ThemeName;
  onThemeChange: (theme: ThemeName) => void;
  products: Product[];
}) {
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    if (!open) return;
    void api<Health>('/health').then(setHealth).catch(() => setHealth(null));
  }, [open]);

  return (
    <Modal
      title="系统设置"
      open={open}
      onCancel={onClose}
      footer={null}
      width={620}
      destroyOnHidden={false}
    >
      <Typography.Title level={5} style={{ marginTop: 4 }}>界面风格</Typography.Title>
      <div className="settings-theme-grid">
        {THEMES.map((theme) => (
          <button
            key={theme.key}
            type="button"
            className={`settings-theme-card${themeName === theme.key ? ' is-active' : ''}`}
            onClick={() => onThemeChange(theme.key)}
          >
            <span className="settings-theme-swatch">
              {theme.swatch.map((color) => (
                <i key={color} style={{ background: color }} />
              ))}
            </span>
            <span className="settings-theme-meta">
              <strong>{theme.title}</strong>
              <small>{theme.description}</small>
            </span>
            {themeName === theme.key && <CheckCircleFilled className="settings-theme-check" />}
          </button>
        ))}
      </div>

      <Typography.Title level={5}>运行环境</Typography.Title>
      <Descriptions size="small" column={1} bordered>
        <Descriptions.Item label="运行模式">本地单机版</Descriptions.Item>
        <Descriptions.Item label="元数据存储">
          {health ? <Tag color="success">{health.storage === 'files' ? '文件存储' : health.storage}</Tag> : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="数据目录">
          <Typography.Text code style={{ fontSize: 12 }}>{health?.platform_dir || '—'}</Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="环境目录">
          <Typography.Text code style={{ fontSize: 12 }}>{health?.environment_dir || '—'}</Typography.Text>
        </Descriptions.Item>
      </Descriptions>

      <Typography.Title level={5}>已安装产品</Typography.Title>
      <Space wrap>
        {products.map((item) => (
          <Tag key={item.id}>{item.title} <small>{item.id}</small></Tag>
        ))}
        {products.length === 0 && <Typography.Text type="secondary">未发现产品包</Typography.Text>}
      </Space>
    </Modal>
  );
}
