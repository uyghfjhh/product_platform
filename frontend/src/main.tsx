import React from 'react';
import ReactDOM from 'react-dom/client';
import { App as AntApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';

import App from './App';
import './style.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{
      token: {
        colorPrimary: '#167a68',
        colorInfo: '#2775a7',
        colorSuccess: '#23845d',
        colorWarning: '#a86d1d',
        colorError: '#bd4848',
        borderRadius: 6,
        fontSize: 14,
      },
    }}>
      <AntApp><App /></AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);
