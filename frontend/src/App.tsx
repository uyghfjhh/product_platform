import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import {
  App as AntApp, Button, Drawer, Layout, Menu, Select, Space, Tag, Typography,
} from 'antd';
import {
  ApartmentOutlined, ApiOutlined, DashboardOutlined, DatabaseOutlined,
  FileProtectOutlined, MenuOutlined, PlayCircleOutlined, SettingOutlined,
  ToolOutlined,
} from '@ant-design/icons';

import { api, type Environment, type Product, type Task, statusColor } from './api';
import TaskDrawer from './components/TaskDrawer';
const OverviewPage = lazy(() => import('./views/OverviewPage'));
const EnvironmentsPage = lazy(() => import('./views/EnvironmentsPage'));
const DeploymentPage = lazy(() => import('./views/DeploymentPage'));
const DatabasePage = lazy(() => import('./views/DatabasePage'));
const TestsPage = lazy(() => import('./views/TestsPage'));
const StabilityPage = lazy(() => import('./views/StabilityPage'));
const LicensePage = lazy(() => import('./views/LicensePage'));

const { Header, Sider, Content } = Layout;

type Page = 'overview' | 'environments' | 'deployment' | 'database' | 'tests' | 'stability' | 'license';

const navigation: { key: Page; label: string; icon: React.ReactNode; capability?: string }[] = [
  { key: 'overview', label: '工作台', icon: <DashboardOutlined /> },
  { key: 'environments', label: '产品与环境', icon: <ApartmentOutlined /> },
  { key: 'deployment', label: '部署管理', icon: <ToolOutlined />, capability: 'deployment' },
  { key: 'database', label: '数据库管理', icon: <DatabaseOutlined />, capability: 'database' },
  { key: 'tests', label: '自动化测试', icon: <PlayCircleOutlined />, capability: 'tests' },
  { key: 'stability', label: '压测与常稳', icon: <ApiOutlined />, capability: 'stability' },
  { key: 'license', label: 'License 生成', icon: <FileProtectOutlined /> },
];

function selectedFromStorage(key: string) {
  try { return localStorage.getItem(key) || ''; } catch { return ''; }
}

export default function App() {
  const { message } = AntApp.useApp();
  const [products, setProducts] = useState<Product[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [page, setPage] = useState<Page>('overview');
  const [productId, setProductId] = useState(selectedFromStorage('platform-product'));
  const [environmentId, setEnvironmentId] = useState(selectedFromStorage('platform-environment'));
  const [taskId, setTaskId] = useState<string | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

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
      setProductId((current) => current && productList.some((item) => item.id === current) ? current : productList[0]?.id || '');
    } catch (error) {
      message.error((error as Error).message);
    }
  }, [message]);

  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => {
    const interval = window.setInterval(() => {
      void api<Task[]>('/operations?limit=20').then(setTasks).catch(() => undefined);
    }, 3500);
    return () => window.clearInterval(interval);
  }, []);
  useEffect(() => {
    const candidates = environments.filter((item) => item.product_id === productId);
    if (!candidates.some((item) => item.id === environmentId)) {
      setEnvironmentId(candidates[0]?.id || '');
    }
  }, [environments, productId, environmentId]);
  useEffect(() => { localStorage.setItem('platform-product', productId); }, [productId]);
  useEffect(() => { localStorage.setItem('platform-environment', environmentId); }, [environmentId]);

  const product = products.find((item) => item.id === productId);
  const environment = environments.find((item) => item.id === environmentId);
  const active = tasks.find((item) => ['QUEUED', 'RUNNING', 'CANCELLING'].includes(item.status));
  const menuItems = navigation.filter((item) => !item.capability || product?.capabilities.includes(item.capability));
  const title = navigation.find((item) => item.key === page)?.label || '工作台';

  const content = useMemo(() => {
    const common = { product, environment, reload, openTask: setTaskId };
    switch (page) {
      case 'overview': return <OverviewPage products={products} environments={environments} tasks={tasks} openTask={setTaskId} navigate={setPage} />;
      case 'environments': return <EnvironmentsPage products={products} environments={environments} selectedId={environmentId} onSelect={setEnvironmentId} reload={reload} />;
      case 'deployment': return <DeploymentPage {...common} navigate={(p) => setPage(p as Page)} />;
      case 'database': return <DatabasePage {...common} />;
      case 'tests': return <TestsPage {...common} />;
      case 'stability': return <StabilityPage {...common} />;
      case 'license': return <LicensePage />;
    }
  }, [page, product, environment, reload, products, environments, tasks, environmentId]);

  const menu = (
    <Menu
      mode="inline"
      selectedKeys={[page]}
      items={menuItems.map((item) => ({ key: item.key, icon: item.icon, label: item.label }))}
      onClick={({ key }) => { setPage(key as Page); setMobileMenuOpen(false); }}
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
            <Space size={6}>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>产品:</Typography.Text>
              <Select
                className="context-select"
                value={productId || undefined}
                options={products.map((item) => ({ label: item.title, value: item.id }))}
                onChange={(value) => setProductId(value)}
                placeholder="选择产品"
                aria-label="当前产品"
              />
            </Space>
            <Space size={6}>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>绑定环境:</Typography.Text>
              <Select
                className="context-select"
                style={{ minWidth: 200 }}
                value={environmentId || undefined}
                options={environments
                  .filter((item) => item.product_id === productId)
                  .map((item) => ({ label: `${item.title} (${item.host}:${item.port})`, value: item.id }))}
                onChange={setEnvironmentId}
                placeholder="选择绑定测试环境"
                aria-label="当前绑定环境"
              />
              <Button size="small" type="dashed" onClick={() => setPage('environments')}>
                + 新建环境
              </Button>
            </Space>
            {active && (
              <Button type="text" onClick={() => setTaskId(active.id)}>
                <Tag color={statusColor(active.status)}>{active.status}</Tag>
              </Button>
            )}
          </Space>
        </Header>
        <Content className="platform-content">
          <div className="content-width"><Suspense fallback={<div className="page-loading">加载中…</div>}>{content}</Suspense></div>
        </Content>
      </Layout>
      <Drawer open={mobileMenuOpen} onClose={() => setMobileMenuOpen(false)} placement="left" width={230} title="产品工作台">
        {menu}
      </Drawer>
      <TaskDrawer taskId={taskId} onClose={() => { setTaskId(null); void reload(); }} />
    </Layout>
  );
}
