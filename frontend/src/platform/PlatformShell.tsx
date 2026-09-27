import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import {
  App as AntApp, Button, Drawer, Layout, Menu, Select, Space, Tag, Typography,
  type MenuProps,
} from 'antd';
import {
  FileProtectOutlined, MenuOutlined, PlayCircleOutlined, SettingOutlined,
  ToolOutlined,
} from '@ant-design/icons';

import { api, type Environment, type Product, type RegressionBinding, type Task, statusColor } from './api';
import TaskDrawer from '../components/TaskDrawer';
import { testMode } from '../products/testRegistry';

const DeploymentPage = lazy(() => import('../views/DeploymentPage'));
const TestsPage = lazy(() => import('../views/TestsPage'));
const LicensePage = lazy(() => import('../views/LicensePage'));

const { Header, Sider, Content } = Layout;

type Page = 'deployment' | 'license' | `tests:${string}`;
export type ThemeName = 'cman' | 'dark' | 'soft' | 'warm';

function selectedFromStorage(key: string, defaultValue: string) {
  try { return localStorage.getItem(key) || defaultValue; } catch { return defaultValue; }
}

function profileEnvironment(environment: Environment, productId: string,
                            profile?: NonNullable<Product['test_profiles']>[number]): boolean {
  if (environment.product_id !== productId) return false;
  if (!profile) return true;
  return profile.deployment_targets.some((pattern) => pattern.endsWith('*')
    ? Boolean(environment.deployment_target?.startsWith(pattern.slice(0, -1)))
    : environment.deployment_target === pattern);
}

export default function PlatformShell({ themeName, onThemeChange }: {
  themeName: ThemeName;
  onThemeChange: (theme: ThemeName) => void;
}) {
  const { message } = AntApp.useApp();
  const [products, setProducts] = useState<Product[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [bindings, setBindings] = useState<RegressionBinding[]>([]);
  const [page, setPage] = useState<Page>(() => {
    const saved = selectedFromStorage('platform-page', 'deployment');
    if (saved === 'deployment' || saved === 'license' || saved.startsWith('tests:')) {
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
      const [productList, environmentList, taskList, bindingList] = await Promise.all([
        api<Product[]>('/products'),
        api<Environment[]>('/environments'),
        api<Task[]>('/operations?limit=20'),
        api<RegressionBinding[]>('/regression-bindings'),
      ]);
      setProducts(productList);
      setEnvironments(environmentList);
      setTasks(taskList);
      setBindings(bindingList);
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

  const [, testProductId = '', testProfileId = ''] = page.startsWith('tests:') ? page.split(':') : [];
  const testProduct = products.find((item) => item.id === testProductId);
  const testProfile = testProduct?.test_profiles?.find((item) => item.id === testProfileId);
  const testEnvironments = environments.filter((item) => profileEnvironment(item, testProductId, testProfile));
  const activeBinding = bindings.find((item) => item.product_id === testProductId
    && item.profile_id === testProfileId);
  const selectedTestEnvironment = testProfile
    ? testEnvironments.find((item) => item.id === activeBinding?.environment_id)
    : testEnvironments.find((item) => item.id === environmentId) || testEnvironments[0];
  const title = page === 'license'
    ? 'License 管理'
    : page === 'deployment'
      ? '数据库部署管理'
      : `测试 · ${testProfile?.title || testProduct?.title || testProductId}`;

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
      children: products
        .filter((item) => item.capabilities.includes('tests'))
        .flatMap((item) => item.test_profiles?.length
          ? item.test_profiles.map((profile) => ({
              key: `tests:${item.id}:${profile.id}`, label: profile.title,
            }))
          : [{ key: `tests:${item.id}`, label: item.title }]),
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
        return <DeploymentPage {...common} productBindings={bindings} navigate={(p) => setPage(p as Page)} />;
      case 'license':
        return <LicensePage />;
      default:
        if (page.startsWith('tests:')) {
          const selected = testProduct;
          const selectedEnvironment = selectedTestEnvironment;
          const subProduct = testProfile?.id || testMode(selected, selectedEnvironment);
          return <TestsPage key={page} {...common} product={selected || product}
            environment={selectedEnvironment} subProduct={subProduct} />;
        }
        return <DeploymentPage {...common} productBindings={bindings} navigate={(p) => setPage(p as Page)} />;
    }
  }, [page, product, environment, environments, reload, products, environmentId, bindings]);

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
        } else if (key.startsWith('tests:')) {
          setPage(key as Page);
          const [, productId, profileId] = key.split(':');
          const selectedProduct = products.find((item) => item.id === productId);
          const profile = selectedProduct?.test_profiles?.find((item) => item.id === profileId);
          const candidates = environments.filter((item) => profileEnvironment(item, productId, profile));
          const bound = bindings.find((item) => item.product_id === productId && item.profile_id === profileId);
          const target = candidates.find((item) => item.id === bound?.environment_id);
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
                value={page.startsWith('tests:') ? selectedTestEnvironment?.id : environmentId || undefined}
                options={(page.startsWith('tests:') ? testEnvironments : environments).map((item) => ({
                  label: `${item.title} (${item.host}:${item.port})`,
                  value: item.id,
                }))}
                onChange={(id) => {
                  setEnvironmentId(id);
                  if (page.startsWith('tests:') && testProfile) {
                    void api<RegressionBinding>(`/regression-bindings/${encodeURIComponent(testProductId)}/${encodeURIComponent(testProfile.id)}`, {
                      method: 'PUT', body: JSON.stringify({ environment_id: id }),
                    }).then((binding) => {
                      setBindings((current) => [...current.filter((item) => item.product_id !== binding.product_id
                        || item.profile_id !== binding.profile_id), binding]);
                    }).catch((cause) => message.error((cause as Error).message));
                  }
                }}
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
