import { useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Input, Select, Space, Table, Tag, Typography } from 'antd';
import { FileTextOutlined, PlayCircleOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';

import { api, operationRequest, type Case, type Environment, type Product, type Result, statusColor } from '../api';
import ReportDrawer from '../components/ReportDrawer';

type Props = {
  product: Product | undefined;
  environment: Environment | undefined;
  openTask: (taskId: string) => void;
  reload: () => Promise<void>;
};

export default function TestsPage({ product, environment, openTask }: Props) {
  const { message, modal } = App.useApp();
  const [cases, setCases] = useState<Case[]>([]);
  const [results, setResults] = useState<Result[]>([]);
  const [search, setSearch] = useState('');
  const [suite, setSuite] = useState('all');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [sourceStatuses, setSourceStatuses] = useState<Record<string, { status: string; modified_at: number }>>({});
  const [reportTarget, setReportTarget] = useState<string | null>(null);

  useEffect(() => {
    if (!product) return;
    setError('');
    setLoading(true);
    void api<Case[]>(`/cases?product_id=${encodeURIComponent(product.id)}`)
      .then(setCases).catch((cause) => { setCases([]); setError(cause.message); })
      .finally(() => setLoading(false));
  }, [product]);

  async function refreshResults() {
    if (environment) {
      await api<Result[]>(`/environments/${encodeURIComponent(environment.id)}/results`).then(setResults).catch(() => setResults([]));
    } else {
      setResults([]);
    }
    if (product?.id === 'fbasecman') {
      await api<Record<string, { status: string; modified_at: number }>>(`/fbasecman/case-statuses${environment ? `?environment_id=${encodeURIComponent(environment.id)}` : ''}`).then(setSourceStatuses).catch(() => setSourceStatuses({}));
    } else {
      setSourceStatuses({});
    }
  }

  useEffect(() => { void refreshResults(); }, [environment?.id, product?.id]);
  useEffect(() => {
    const timer = window.setInterval(() => { void refreshResults(); }, 10000);
    return () => window.clearInterval(timer);
  }, [environment?.id, product?.id]);

  const suites = useMemo(() => Array.from(new Set(cases.map((item) => item.suite))).sort(), [cases]);
  const filtered = cases.filter((item) => (suite === 'all' || item.suite === suite)
    && (`${item.target} ${item.title}`.toLowerCase().includes(search.trim().toLowerCase())));
  const resultByTarget = new Map(results.map((item) => [item.target, item]));

  async function runTarget(target: string, cluster?: string) {
    if (!product || !environment) return;
    try {
      const task = await operationRequest(
        environment.id,
        product.id === 'fbasecman' ? 'tests.fbasecman' : 'tests.fbase',
        target,
        product.id === 'fbase-database' ? { cluster } : {},
        true,
      );
      openTask(task.id);
    } catch (cause) {
      message.error((cause as Error).message);
    }
  }

  async function runSuite() {
    if (!environment || suite === 'all') return;
    const confirmed = await new Promise<boolean>((resolve) => modal.confirm({
      title: '执行整套用例',
      content: `${suite} · ${environment.title}。本套件会顺序执行多条用例。`,
      okText: '执行', cancelText: '取消',
      onOk: () => resolve(true), onCancel: () => resolve(false),
    }));
    if (confirmed) await runTarget(suite, suite);
  }

  return (
    <>
      <div className="page-heading"><div><Typography.Title level={3}>自动化测试</Typography.Title><Typography.Text type="secondary">现有产品用例目录与当前结果</Typography.Text></div><Typography.Text type="secondary">{filtered.length} 项</Typography.Text></div>
      {!environment && <Alert type="info" showIcon message="选择环境后可运行用例" description="用例清单和已有报告仍可查看。" />}
      <>
        {error && <Alert type="warning" showIcon message="用例来源暂不可用" description={error} />}
        <section className="work-section">
          <div className="filter-toolbar">
            <Input prefix={<SearchOutlined />} value={search} onChange={(event) => setSearch(event.target.value)} allowClear placeholder="搜索目标或用例说明" />
            <Select value={suite} onChange={setSuite} options={[{ value: 'all', label: '全部套件' }, ...suites.map((value) => ({ value, label: value }))]} />
            <Button icon={<ReloadOutlined />} onClick={() => void refreshResults()} aria-label="刷新测试结果" />
          </div>
          <div className="test-actions">
            <Space wrap>
              <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => void runSuite()} disabled={!environment || suite === 'all'}>执行当前套件</Button>
              {product?.id === 'fbasecman' && <Button onClick={() => void runTarget('failed')} disabled={!environment}>重跑失败项</Button>}
              {product?.id === 'fbasecman' && environment && <Button href={`/api/v1/fbasecman/environments/${encodeURIComponent(environment.id)}/reports/junit`} target="_blank">导出 JUnit</Button>}
              {product?.id === 'fbasecman' && environment && <Button href={`/api/v1/fbasecman/environments/${encodeURIComponent(environment.id)}/reports/html`} target="_blank">导出 HTML</Button>}
              {product?.id === 'fbasecman' && <Typography.Text type="secondary">报告来自现有 fbasecman 测试目录</Typography.Text>}
            </Space>
          </div>
          <Table
            rowKey="target" loading={loading} dataSource={filtered} pagination={{ pageSize: 15, showSizeChanger: false }} size="middle" scroll={{ x: 'max-content' }}
            columns={[
              { title: '用例目标', dataIndex: 'target', render: (value: string) => <Typography.Text code>{value}</Typography.Text> },
              { title: '验证内容', dataIndex: 'title', responsive: ['md'], ellipsis: true },
              { title: '当前结果', render: (_: unknown, row: Case) => {
                const result = resultByTarget.get(row.target);
                const source = sourceStatuses[row.target];
                return result ? <Tag color={statusColor(result.status)}>{result.status}</Tag>
                  : source ? <Space size={4}><Tag color={statusColor(source.status)}>{source.status}</Tag><Typography.Text type="secondary">源报告</Typography.Text></Space>
                    : <Typography.Text type="secondary">未执行</Typography.Text>;
              } },
              { title: '', render: (_: unknown, row: Case) => <Space>
                <Button size="small" type="primary" icon={<PlayCircleOutlined />} disabled={!environment || !row.enabled} onClick={() => void runTarget(row.target, row.suite)}>执行</Button>
                {product?.id === 'fbasecman' && <Button size="small" icon={<FileTextOutlined />} disabled={!sourceStatuses[row.target]} onClick={() => setReportTarget(row.target)}>报告</Button>}
              </Space> },
            ]}
          />
        </section>
      </>
      <ReportDrawer target={reportTarget} environmentId={environment?.id} onClose={() => setReportTarget(null)} />
    </>
  );
}
