import React, { useEffect, useState } from 'react';
import ReactDOM from 'react-dom/client';
import { App as AntApp, ConfigProvider, theme as antdTheme } from 'antd';
import zhCN from 'antd/locale/zh_CN';

import App, { type ThemeName } from './App';
import './style.css';

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
      <AntApp><App themeName={themeName} onThemeChange={setThemeName} /></AntApp>
    </ConfigProvider>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <PlatformRoot />
  </React.StrictMode>,
);
