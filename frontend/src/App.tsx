import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import {
  App as AntApp, Button, Drawer, Layout, Menu, Select, Space, Tag, Typography,
  type MenuProps,
} from 'antd';
import {
  FileProtectOutlined, MenuOutlined, PlayCircleOutlined, SettingOutlined,
  ToolOutlined,
} from '@ant-design/icons';

import { api, type Environment, type Product, type Task, statusColor } from './api';
import TaskDrawer from './components/TaskDrawer';

const DeploymentPage = lazy(() => import('./views/DeploymentPage'));
const TestsPage = lazy(() => import('./views/TestsPage'));
const LicensePage = lazy(() => import('./views/LicensePage'));

const { Header, Sider, Content } = Layout;

type Page = 'deployment' | 'tests-mmr' | 'tests-mac' | 'tests-cman' | 'license';
export type ThemeName = 'cman' | 'dark' | 'soft' | 'warm';

function selectedFromStorage(key: string, defaultValue: string) {
  try { return localStorage.getItem(key) || defaultValue; } catch { return defaultValue; }
}

export default function App({ themeName, onThemeChange }: {
  themeName: ThemeName;
  onThemeChange: (theme: ThemeName) => void;
}) {
  const { message } = AntApp.useApp();
  const [products, setProducts] = useState<Product[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [page, setPage] = useState<Page>(() => {
    const saved = selectedFromStorage('platform-page', 'deployment');
    if (['deployment', 'tests-mmr', 'tests-mac', 'tests-cman', 'license'].includes(saved)) {
      return saved as Page;
    }
    return 'deployment';
  });
  const [environmentId, setEnvironmentId] = useState(selectedFromStorage('platform-environment', ''));
  const [taskId, setTaskId] = useState<string | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get('task');
    if (requested) setTaskId(requested);
  }, []);

  const reload = useCallback(async () => {
    try {
      const [productList, environmentList, taskList] = await Promise.all([
        api<Product[]>('/products'),
        api<Environment[]>('/environments'),
        api<Task[]>('/operations?limit=20'),
      ]);
      setProducts(productList);
      setEnvironments(environmentList);
      setTasks(taskList);
      if (environmentList.length > 0 && !environmentList.some((item) => item.id === environmentId)) {
        setEnvironmentId(environmentList[0].id);
      }
    } catch (error) {
      message.error((error as Error).message);
    }
  }, [environmentId, message]);

  useEffect(() => { void reload(); }, [reload]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      void Promise.all([
        api<Environment[]>('/environments').then(setEnvironments).catch(() => undefined),
        api<Task[]>('/operations?limit=20').then(setTasks).catch(() => undefined),
      ]);
    }, 3500);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (environments.length > 0 && !environments.some((item) => item.id === environmentId)) {
      setEnvironmentId(environments[0]?.id || '');
    }
  }, [environments, environmentId]);

  useEffect(() => { localStorage.setItem('platform-page', page); }, [page]);
  useEffect(() => { localStorage.setItem('platform-environment', environmentId); }, [environmentId]);

  const environment = environments.find((item) => item.id === environmentId) || environments[0];
  const product = products.find((item) => item.id === environment?.product_id) || products[0];
  const active = tasks.find((item) => ['QUEUED', 'RUNNING', 'CANCELLING'].includes(item.status));

  const title = useMemo(() => {
    switch (page) {
      case 'deployment': return '数据库部署管理';
      case 'tests-mmr': return '测试 · 多活';
      case 'tests-mac': return '测试 · 等保';
      case 'tests-cman': return '测试 · fbasecman';
      case 'license': return 'License 管理';
      default: return '数据库部署管理';
    }
  }, [page]);

  const menuItems = useMemo<MenuProps['items']>(() => [
    {
      key: 'deployment',
      icon: <ToolOutlined />,
      label: '数据库部署管理',
      children: environments.length > 0
        ? environments.map((env) => ({
            key: `deploy:${env.id}`,
            label: `${env.title} (:${env.port})`,
          }))
        : [{ key: 'deploy:none', label: '默认部署管理' }],
    },
    {
      key: 'tests',
      icon: <PlayCircleOutlined />,
      label: '测试',
      children: [
        { key: 'tests-mmr', label: '多活' },
        { key: 'tests-mac', label: '等保' },
        { key: 'tests-cman', label: 'fbasecman' },
      ],
    },
    {
      key: 'license',
      icon: <FileProtectOutlined />,
      label: 'License 管理',
    },
  ], [environments]);

  const content = useMemo(() => {
    const common = {
      product,
      environment,
      environments,
      onSelectEnvironment: setEnvironmentId,
      reload,
      openTask: setTaskId,
    };
    switch (page) {
      case 'deployment':
        return <DeploymentPage {...common} navigate={(p) => setPage(p as Page)} />;
      case 'tests-mmr':
        return (
          <TestsPage
            {...common}
            product={products.find((p) => p.id === 'fbase-database') || product}
            environment={environment?.product_id === 'fbase-database' ? environment : environments.find((e) => e.id === 'fbase-mmr')}
            subProduct="mmr"
          />
        );
      case 'tests-mac':
        return (
          <TestsPage
            {...common}
            product={products.find((p) => p.id === 'fbase-database') || product}
            environment={environment?.product_id === 'fbase-database' ? environment : environments.find((e) => e.id === 'fbase-mac')}
            subProduct="mac"
          />
        );
      case 'tests-cman':
        return (
          <TestsPage
            {...common}
            product={products.find((p) => p.id === 'fbasecman') || product}
            environment={environment?.product_id === 'fbasecman' ? environment : environments.find((e) => e.product_id === 'fbasecman')}
            subProduct="cman"
          />
        );
      case 'license':
        return <LicensePage />;
      default:
        return <DeploymentPage {...common} navigate={(p) => setPage(p as Page)} />;
    }
  }, [page, product, environment, environments, reload, products]);

  const selectedMenuKey = useMemo(() => {
    if (page === 'deployment') {
      return environment?.id ? `deploy:${environment.id}` : 'deployment';
    }
    return page;
  }, [page, environment?.id]);

  const menu = (
    <Menu
      mode="inline"
      selectedKeys={[selectedMenuKey]}
      defaultOpenKeys={['deployment', 'tests']}
      items={menuItems}
      onClick={({ key }) => {
        if (key.startsWith('deploy:')) {
          const envId = key.slice(7);
          setPage('deployment');
          if (envId !== 'none') setEnvironmentId(envId);
        } else if (key === 'deployment') {
          setPage('deployment');
        } else if (key === 'tests-mmr') {
          setPage('tests-mmr');
          const target = environments.find((e) => e.id === 'fbase-mmr');
          if (target) setEnvironmentId(target.id);
        } else if (key === 'tests-mac') {
          setPage('tests-mac');
          const target = environments.find((e) => e.id === 'fbase-mac');
          if (target) setEnvironmentId(target.id);
        } else if (key === 'tests-cman') {
          setPage('tests-cman');
          const target = environments.find((e) => e.product_id === 'fbasecman');
          if (target) setEnvironmentId(target.id);
        } else {
          setPage(key as Page);
        }
        setMobileMenuOpen(false);
      }}
    />
  );

  return (
    <Layout className="platform-shell">
      <Sider className="platform-sidebar" width={220} theme="light">
        <div className="platform-brand">
          <span className="platform-brand-mark">F</span>
          <span><strong>产品工作台</strong><small>内部管理平台</small></span>
        </div>
        {menu}
        <div className="sidebar-foot"><SettingOutlined /> 本地单机版</div>
      </Sider>
      <Layout>
        <Header className="platform-header">
          <Space className="header-left">
            <Button className="mobile-menu-button" icon={<MenuOutlined />} onClick={() => setMobileMenuOpen(true)} aria-label="打开导航" />
            <Typography.Text strong className="header-title">{title}</Typography.Text>
          </Space>
          <Space className="header-controls" wrap size="middle">
            <Select
              className="theme-select"
              value={themeName}
              onChange={onThemeChange}
              options={[
                { label: '极客夜蓝', value: 'cman' },
                { label: '深色石墨', value: 'dark' },
                { label: '柔和灰绿', value: 'soft' },
                { label: '暖灰护眼', value: 'warm' },
              ]}
              aria-label="界面风格"
            />
            <Space size={6}>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>当前环境:</Typography.Text>
              <Select
                className="context-select"
                style={{ minWidth: 230 }}
                value={environmentId || undefined}
                options={environments.map((item) => ({
                  label: `${item.title} (${item.host}:${item.port})`,
                  value: item.id,
                }))}
                onChange={setEnvironmentId}
                placeholder="选择测试环境"
                aria-label="当前环境"
              />
            </Space>
            {active && (
              <Button type="text" onClick={() => setTaskId(active.id)}>
                <Tag color={statusColor(active.status)}>{active.status}</Tag>
              </Button>
            )}
          </Space>
        </Header>
        <Content className="platform-content">
          <div className="content-width">
            <Suspense fallback={<div className="page-loading">加载中…</div>}>
              {content}
            </Suspense>
          </div>
        </Content>
      </Layout>
      <Drawer open={mobileMenuOpen} onClose={() => setMobileMenuOpen(false)} placement="left" width={230} title="产品工作台">
        {menu}
      </Drawer>
      <TaskDrawer taskId={taskId} onClose={() => { setTaskId(null); void reload(); }} />
    </Layout>
  );
}
