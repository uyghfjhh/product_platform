import React, { useEffect, useState } from 'react';
import ReactDOM from 'react-dom/client';
import { App as AntApp, ConfigProvider, theme as antdTheme } from 'antd';
import zhCN from 'antd/locale/zh_CN';

import PlatformShell, { type ThemeName } from './platform/PlatformShell';
import './style.css';

// crypto.randomUUID 仅存在于安全上下文（HTTPS/localhost）；平台常经
// http://<局域网IP> 访问，第三方组件（Prisma Studio）依赖该 API——
// 在模块加载最早期补齐，getRandomValues 在非安全上下文可用。
if (typeof crypto !== 'undefined' && !crypto.randomUUID) {
  const fromBytes = (bytes: Uint8Array): `${string}-${string}-${string}-${string}-${string}` => {
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}` as `${string}-${string}-${string}-${string}-${string}`;
  };
  crypto.randomUUID = crypto.getRandomValues
    ? () => fromBytes(crypto.getRandomValues(new Uint8Array(16)))
    : () => fromBytes(Uint8Array.from({ length: 16 }, () => Math.floor(Math.random() * 256)));
}

function PlatformRoot() {
  const [themeName, setThemeName] = useState<ThemeName>(() => {
    const saved = localStorage.getItem('platform-theme');
    if (saved === 'cman' || saved === 'dark' || saved === 'soft' || saved === 'warm') {
      return saved as ThemeName;
    }
    return 'cman';
  });

  useEffect(() => {
    document.documentElement.dataset.theme = themeName;
    localStorage.setItem('platform-theme', themeName);
  }, [themeName]);

  const isDarkLike = themeName === 'dark' || themeName === 'cman';

  return (
    <ConfigProvider locale={zhCN} theme={{
      algorithm: isDarkLike ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
      token: {
        colorPrimary:
          themeName === 'cman'
            ? '#38bdf8'
            : themeName === 'warm'
            ? '#786959'
            : themeName === 'dark'
            ? '#83b49c'
            : '#537a6c',
        colorInfo: themeName === 'cman' ? '#38bdf8' : themeName === 'dark' ? '#84afc5' : '#53788a',
        colorSuccess: themeName === 'cman' ? '#10b981' : themeName === 'dark' ? '#81bd96' : '#3f7b60',
        colorWarning: themeName === 'cman' ? '#f59e0b' : themeName === 'dark' ? '#d1ae72' : '#96744e',
        colorError: themeName === 'cman' ? '#ef4444' : themeName === 'dark' ? '#d8958d' : '#a75652',
        ...(themeName === 'cman'
          ? {
              colorBgBase: '#0a0d14',
              colorBgContainer: '#111726',
              colorBgElevated: '#182235',
              colorBorder: '#1e293b',
              colorText: '#f1f5f9',
              colorTextSecondary: '#94a3b8',
            }
          : {}),
        borderRadius: 6,
        fontSize: 14,
      },
    }}>
      <AntApp><PlatformShell themeName={themeName} onThemeChange={setThemeName} /></AntApp>
    </ConfigProvider>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <PlatformRoot />
  </React.StrictMode>,
);
