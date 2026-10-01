import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  App as AntApp, Button, Drawer, Layout, Menu, Tag,
  type MenuProps,
} from 'antd';
import {
  DatabaseOutlined, FileProtectOutlined, KeyOutlined, MenuOutlined, PlayCircleOutlined,
  SettingOutlined, ToolOutlined,
} from '@ant-design/icons';

import { api, type Environment, type Product, type RegressionBinding, type Task, statusColor } from './api';
import TaskDrawer from '../components/TaskDrawer';
import SettingsModal from '../components/SettingsModal';
import PlatformErrorBoundary from '../components/PlatformErrorBoundary';
import { testMode } from '../products/testRegistry';

const DeploymentPage = lazy(() => import('../views/DeploymentPage'));
const DatabasePage = lazy(() => import('../views/DatabasePage'));
const TestsPage = lazy(() => import('../views/TestsPage'));
const StabilityPage = lazy(() => import('../views/StabilityPage'));
const LicenseKeysView = lazy(() => import('../views/license/LicenseKeysView'));
const LicenseGenerateView = lazy(() => import('../views/license/LicenseGenerateView'));

const { Sider, Content } = Layout;

type Page = 'deployment' | 'database'
  | 'license:keys' | 'license:generate'
  | `tests:${string}` | `stability:${string}`;
export type ThemeName = 'cman' | 'dark' | 'soft' | 'warm';

const ACTIVE_STATUSES = ['QUEUED', 'RUNNING', 'CANCELLING'];

function selectedFromStorage(key: string, defaultValue: string) {
  try { return localStorage.getItem(key) || defaultValue; } catch { return defaultValue; }
}

function isPage(value: string): value is Page {
  return value === 'deployment' || value === 'database' || value.startsWith('license:')
    || value.startsWith('tests:') || value.startsWith('stability:');
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
    return isPage(saved) ? saved as Page : 'deployment';
  });
  const [environmentId, setEnvironmentId] = useState(selectedFromStorage('platform-environment', ''));
  const [databaseTarget, setDatabaseTarget] = useState<{ environmentId: string; nodeId: string } | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

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
    } catch (error) {
      message.error((error as Error).message);
    }
  }, [message]);

  useEffect(() => { void reload(); }, [reload]);

  const hasActiveTask = tasks.some((item) => ACTIVE_STATUSES.includes(item.status));

  // 按需轮询：仅存在执行中任务时刷新任务/环境状态；静态查阅不做固定轮询。
  useEffect(() => {
    if (!hasActiveTask) return;
    const interval = window.setInterval(() => {
      void Promise.all([
        api<Environment[]>('/environments').then(setEnvironments).catch(() => undefined),
        api<Task[]>('/operations?limit=20').then(setTasks).catch(() => undefined),
        api<RegressionBinding[]>('/regression-bindings').then(setBindings).catch(() => undefined),
      ]);
    }, 3500);
    return () => window.clearInterval(interval);
  }, [hasActiveTask]);

  // 桌面通知：任务从执行中进入终态时提醒（需要浏览器授权，首次出现活动任务时申请）
  const notifiedRef = useRef<Record<string, string>>({});
  useEffect(() => {
    if (!('Notification' in window)) return;
    const hasActive = tasks.some((item) => ACTIVE_STATUSES.includes(item.status));
    if (hasActive && Notification.permission === 'default') {
      void Notification.requestPermission().catch(() => undefined);
    }
    tasks.forEach((task) => {
      const previous = notifiedRef.current[task.id];
      notifiedRef.current[task.id] = task.status;
      const wasActive = !previous || ACTIVE_STATUSES.includes(previous);
      const nowTerminal = !ACTIVE_STATUSES.includes(task.status);
      if (!wasActive || !nowTerminal || Notification.permission !== 'granted') return;
      const title = task.status === 'SUCCEEDED' ? '任务完成' : `任务${task.status}`;
      try {
        const envLabel = environments.find((item) => item.id === task.environment_id)?.title
          || task.environment_id || '';
        new Notification(`[${task.action}] ${title}`, {
          body: `${task.target || ''} · ${envLabel}`,
          tag: task.id,
        });
      } catch { /* Safari 等非标准实现忽略 */ }
    });
  }, [tasks, environments]);

  useEffect(() => {
    if (environments.length > 0 && !environments.some((item) => item.id === environmentId)) {
      setEnvironmentId(environments[0]?.id || '');
    }
  }, [environments, environmentId]);

  useEffect(() => { localStorage.setItem('platform-page', page); }, [page]);
  useEffect(() => { localStorage.setItem('platform-environment', environmentId); }, [environmentId]);

  const environment = environments.find((item) => item.id === environmentId) || environments[0];
  const product = products.find((item) => item.id === environment?.product_id) || products[0];
  const active = tasks.find((item) => ACTIVE_STATUSES.includes(item.status));

  const [, testProductId = '', testProfileId = ''] = page.startsWith('tests:') ? page.split(':') : [];
  const testProduct = products.find((item) => item.id === testProductId);
  const testProfile = testProduct?.test_profiles?.find((item) => item.id === testProfileId);
  const testEnvironments = environments.filter((item) => profileEnvironment(item, testProductId, testProfile));
  const activeBinding = bindings.find((item) => item.product_id === testProductId
    && item.profile_id === testProfileId);
  const selectedTestEnvironment = testProfile
    ? testEnvironments.find((item) => item.id === activeBinding?.environment_id)
    : testEnvironments.find((item) => item.id === environmentId) || testEnvironments[0];

  const stabilityProductId = page.startsWith('stability:') ? page.split(':')[1] : '';
  const stabilityProduct = products.find((item) => item.id === stabilityProductId);
  const stabilityEnvironment = stabilityProduct
    ? environments.find((item) => item.product_id === stabilityProduct.id
        && item.id === (bindings.find((b) => b.product_id === stabilityProduct.id)?.environment_id
          || environmentId))
    : undefined;

  // 固定导航树：不随环境增删跳变（§5.1）
  const menuItems = useMemo<MenuProps['items']>(() => [
    {
      key: 'deployment',
      icon: <ToolOutlined />,
      label: '数据库部署管理',
    },
    { key: 'database', icon: <DatabaseOutlined />, label: '数据库管理' },
    {
      key: 'tests',
      icon: <PlayCircleOutlined />,
      label: '产品测试中心',
      children: products
        .filter((item) => item.capabilities.includes('tests') || item.capabilities.includes('stability'))
        .map((item) => ({
          key: `product:${item.id}`,
          label: item.title,
          children: [
            ...(item.test_profiles || []).map((profile) => ({
              key: `tests:${item.id}:${profile.id}`,
              label: profile.title,
            })),
            ...(item.capabilities.includes('stability')
              ? [{ key: `stability:${item.id}`, label: '稳定性测试' }]
              : []),
          ],
        })),
    },
    {
      key: 'license',
      icon: <FileProtectOutlined />,
      label: 'License 授权管理',
      children: [
        { key: 'license:keys', icon: <KeyOutlined />, label: '密钥管理' },
        { key: 'license:generate', icon: <FileProtectOutlined />, label: 'License 生成' },
      ],
    },
  ], [products]);

  const content = useMemo(() => {
    const common = {
      product,
      environment,
      environments,
      onSelectEnvironment: setEnvironmentId,
      reload,
      openTask: setTaskId,
    };
    if (page === 'database') return <DatabasePage {...common} initialNodeId={databaseTarget?.environmentId === environment?.id ? databaseTarget?.nodeId : undefined} />;
    if (page === 'license:keys') return <LicenseKeysView />;
    if (page === 'license:generate') return <LicenseGenerateView />;
    if (page.startsWith('stability:')) {
      return <StabilityPage key={page} {...common}
        product={stabilityProduct || product}
        environment={stabilityEnvironment || environment}
        environments={environments} bindings={bindings} />;
    }
    if (page.startsWith('tests:')) {
      const subProduct = testProfile?.id || testMode(testProduct, selectedTestEnvironment);
      return <TestsPage key={page} {...common}
        product={testProduct || product}
        environment={selectedTestEnvironment}
        subProduct={subProduct}
        profileId={testProfile?.id}
        bindings={bindings}
        tasks={tasks}
        onOpenEnvironment={(id) => { setEnvironmentId(id); setPage('deployment'); }} />;
    }
    return <DeploymentPage {...common} onOpenDatabase={(node, view) => {
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
      setDatabaseTarget(node && environment ? { environmentId: environment.id, nodeId: node.id } : null);
      if (view === 'sql') window.location.hash = 'view=sql';
      setPage('database');
    }} products={products} />;
  }, [page, product, environment, environments, reload, products, environmentId,
      bindings, testProduct, testProfile, selectedTestEnvironment, stabilityProduct,
      stabilityEnvironment, tasks, databaseTarget]);

  const selectedMenuKey = page === 'deployment' ? 'deployment' : page;

  const menu = (
    <Menu
      mode="inline"
      selectedKeys={[selectedMenuKey]}
      defaultOpenKeys={['tests', 'license', ...products.map((item) => `product:${item.id}`)]}
      items={menuItems}
      onClick={({ key }) => {
        if (key === 'deployment' || key === 'database' || key.startsWith('license:')
            || key.startsWith('tests:') || key.startsWith('stability:')) {
          setPage(key as Page);
          // 测试页跳转到已绑定环境，保证页面上下文与执行上下文一致
          if (key.startsWith('tests:')) {
            const [, productId, profileId] = key.split(':');
            const bound = bindings.find((item) => item.product_id === productId
              && item.profile_id === profileId);
            if (bound) setEnvironmentId(bound.environment_id);
          }
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
        {active && <Button type="text" onClick={() => setTaskId(active.id)}>
          当前任务 <Tag color={statusColor(active.status)}>{active.status}</Tag>
        </Button>}
        <button
          type="button"
          className="sidebar-foot sidebar-settings"
          onClick={() => setSettingsOpen(true)}
        >
          <SettingOutlined /> 系统设置（本地单机版）
        </button>
      </Sider>
      <Layout>
        <Button className="mobile-menu-button" icon={<MenuOutlined />}
          onClick={() => setMobileMenuOpen(true)} aria-label="打开导航" />
        <Content className="platform-content">
          <div className="content-width">
            <PlatformErrorBoundary>
              <Suspense fallback={<div className="page-loading">加载中…</div>}>
                {content}
              </Suspense>
            </PlatformErrorBoundary>
          </div>
        </Content>
      </Layout>
      <Drawer open={mobileMenuOpen} onClose={() => setMobileMenuOpen(false)}
        placement="left" width={230} title="产品工作台"
        footer={<>
          {active && <Button type="text" onClick={() => { setMobileMenuOpen(false); setTaskId(active.id); }}>
            当前任务 <Tag color={statusColor(active.status)}>{active.status}</Tag>
          </Button>}
          <Button icon={<SettingOutlined />} onClick={() => { setMobileMenuOpen(false); setSettingsOpen(true); }}>
            系统设置
          </Button>
        </>}>
        {menu}
      </Drawer>
      <TaskDrawer taskId={taskId} onClose={() => { setTaskId(null); void reload(); }} />
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)}
        themeName={themeName} onThemeChange={onThemeChange} products={products} />
    </Layout>
  );
}
