import { useEffect, useMemo, useState } from 'react';
import {
  Alert, App, Button, Card, Collapse, Input, Space, Tag, Typography, Empty,
} from 'antd';
import {
  CheckCircleOutlined, CloseCircleOutlined, FileTextOutlined,
  MinusCircleOutlined, PlayCircleOutlined, ReloadOutlined, SearchOutlined,
} from '@ant-design/icons';

import {
  api, operationRequest, type Case, type Environment, type Product, type Result, statusColor,
} from '../api';
import ReportDrawer from '../components/ReportDrawer';

type Props = {
  product: Product | undefined;
  environment: Environment | undefined;
  openTask: (taskId: string) => void;
  reload: () => Promise<void>;
};

type FilterStatus = 'all' | 'PASS' | 'FAIL' | 'UNTESTED';

export default function TestsPage({ product, environment, openTask }: Props) {
  const { message, modal } = App.useApp();

  // 用例与结果状态
  const [cases, setCases] = useState<Case[]>([]);
  const [results, setResults] = useState<Result[]>([]);
  const [sourceStatuses, setSourceStatuses] = useState<Record<string, { status: string; modified_at: number }>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // 筛选与搜索状态
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<FilterStatus>('all');
  const [activeSuites, setActiveSuites] = useState<string[]>([]);
  const [reportTarget, setReportTarget] = useState<string | null>(null);

  // 1. 获取当前产品下的所有测试用例清单
  useEffect(() => {
    if (!product) return;
    setError('');
    setLoading(true);
    void api<Case[]>(`/cases?product_id=${encodeURIComponent(product.id)}`)
      .then((data) => {
        setCases(data);
        // 默认展开前两个套件
        const initialSuites = Array.from(new Set(data.map((c) => c.suite))).slice(0, 2);
        setActiveSuites(initialSuites);
      })
      .catch((cause) => {
        setCases([]);
        setError(cause.message);
      })
      .finally(() => setLoading(false));
  }, [product]);

  // 2. 刷新当前选中环境下的测试结果与产物状态
  async function refreshResults() {
    if (environment) {
      await api<Result[]>(`/environments/${encodeURIComponent(environment.id)}/results`)
        .then(setResults)
        .catch(() => setResults([]));
    } else {
      setResults([]);
    }
    if (product?.id === 'fbasecman') {
      const query = environment ? `?environment_id=${encodeURIComponent(environment.id)}` : '';
      await api<Record<string, { status: string; modified_at: number }>>(`/fbasecman/case-statuses${query}`)
        .then(setSourceStatuses)
        .catch(() => setSourceStatuses({}));
    } else {
      setSourceStatuses({});
    }
  }

  useEffect(() => {
    void refreshResults();
  }, [environment?.id, product?.id]);

  // 定时刷新结果
  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshResults();
    }, 10000);
    return () => window.clearInterval(timer);
  }, [environment?.id, product?.id]);

  const resultByTarget = useMemo(() => new Map(results.map((item) => [item.target, item])), [results]);

  // 3. 计算各个用例的最终状态
  const getCaseStatus = (target: string): 'PASS' | 'FAIL' | 'UNTESTED' => {
    const r = resultByTarget.get(target);
    if (r) return r.status === 'PASS' ? 'PASS' : 'FAIL';
    const s = sourceStatuses[target];
    if (s) return s.status === 'PASS' ? 'PASS' : s.status === 'FAIL' ? 'FAIL' : 'UNTESTED';
    return 'UNTESTED';
  };

  // 4. 汇总统计指标 (全部、通过、失败、未执行、通过率)
  const stats = useMemo(() => {
    let passCount = 0;
    let failCount = 0;
    let untestedCount = 0;

    cases.forEach((c) => {
      const st = getCaseStatus(c.target);
      if (st === 'PASS') passCount++;
      else if (st === 'FAIL') failCount++;
      else untestedCount++;
    });

    const total = cases.length;
    const rate = total > 0 ? ((passCount / total) * 100).toFixed(1) : '0.0';
    return { total, passCount, failCount, untestedCount, rate };
  }, [cases, resultByTarget, sourceStatuses]);

  // 5. 按照套件分组并结合搜索、状态筛选过滤
  const groupedSuites = useMemo(() => {
    const q = search.trim().toLowerCase();
    const map = new Map<string, Case[]>();

    cases.forEach((c) => {
      const st = getCaseStatus(c.target);
      // 状态筛选
      if (statusFilter !== 'all' && st !== statusFilter) return;

      // 文本搜索
      if (q) {
        const matchTarget = c.target.toLowerCase().includes(q);
        const matchTitle = (c.title || '').toLowerCase().includes(q);
        if (!matchTarget && !matchTitle) return;
      }

      if (!map.has(c.suite)) map.set(c.suite, []);
      map.get(c.suite)!.push(c);
    });

    return Array.from(map.entries()).map(([suiteId, suiteCases]) => {
      const pCount = suiteCases.filter((c) => getCaseStatus(c.target) === 'PASS').length;
      const fCount = suiteCases.filter((c) => getCaseStatus(c.target) === 'FAIL').length;
      const uCount = suiteCases.filter((c) => getCaseStatus(c.target) === 'UNTESTED').length;
      return {
        suiteId,
        cases: suiteCases,
        total: suiteCases.length,
        passCount: pCount,
        failCount: fCount,
        untestedCount: uCount,
      };
    });
  }, [cases, search, statusFilter, resultByTarget, sourceStatuses]);

  // 6. 执行单个测试用例
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

  // 7. 执行整套测试用例
  async function runSuite(suiteId: string) {
    if (!environment) return;
    const confirmed = await new Promise<boolean>((resolve) =>
      modal.confirm({
        title: '执行整套用例',
        content: `确认在环境【${environment.title}】执行套件【${suiteId}】的全部用例？`,
        okText: '确认执行',
        cancelText: '取消',
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      }),
    );
    if (confirmed) await runTarget(suiteId, suiteId);
  }

  return (
    <>
      {/* 页面标题与当前环境绑定标识 */}
      <div className="page-heading">
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            自动化回归测试
          </Typography.Title>
          <Typography.Text type="secondary">
            当前绑定环境：
            {environment ? (
              <Tag color="cyan" style={{ marginLeft: 6, fontWeight: 500 }}>
                {environment.title} ({environment.host}:{environment.port})
              </Tag>
            ) : (
              <Tag color="default" style={{ marginLeft: 6 }}>未选择环境</Tag>
            )}
          </Typography.Text>
        </div>
      </div>

      {!environment && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 18 }}
          message="请先选择测试环境"
          description="在顶部选择所要绑定的环境后，即可向该环境提交执行测试用例；用例清单与历史报告在未选环境时仍可离线查阅。"
        />
      )}

      {error && <Alert type="warning" showIcon style={{ marginBottom: 18 }} message="用例来源加载异常" description={error} />}

      {/* 1. 商务简洁风格指标药丸栏 (Metrics Summary Pills) */}
      <div className="metrics-pill-bar">
        <div
          className={`metric-pill-item ${statusFilter === 'all' ? 'active' : ''}`}
          onClick={() => setStatusFilter('all')}
        >
          <span className="pill-label">全部用例</span>
          <span className="pill-count">{stats.total}</span>
        </div>
        <div
          className={`metric-pill-item pass ${statusFilter === 'PASS' ? 'active' : ''}`}
          onClick={() => setStatusFilter('PASS')}
        >
          <span className="pill-label"><CheckCircleOutlined /> 通过</span>
          <span className="pill-count">{stats.passCount}</span>
        </div>
        <div
          className={`metric-pill-item fail ${statusFilter === 'FAIL' ? 'active' : ''}`}
          onClick={() => setStatusFilter('FAIL')}
        >
          <span className="pill-label"><CloseCircleOutlined /> 失败</span>
          <span className="pill-count">{stats.failCount}</span>
        </div>
        <div
          className={`metric-pill-item untested ${statusFilter === 'UNTESTED' ? 'active' : ''}`}
          onClick={() => setStatusFilter('UNTESTED')}
        >
          <span className="pill-label"><MinusCircleOutlined /> 未执行</span>
          <span className="pill-count">{stats.untestedCount}</span>
        </div>
        <div className="metric-pill-item rate">
          <span className="pill-label">通过率</span>
          <span className="pill-count">{stats.rate}%</span>
        </div>
      </div>

      {/* 2. 搜索与工具操作栏 */}
      <section className="work-section">
        <div className="filter-toolbar" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <Space wrap size="middle">
            <Input
              prefix={<SearchOutlined style={{ color: '#88948f' }} />}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              allowClear
              placeholder="搜索用例目标 (target) 或验证目的"
              style={{ width: 360 }}
            />
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void refreshResults()}
              loading={loading}
              aria-label="刷新结果"
            >
              刷新状态
            </Button>
          </Space>

          <Space wrap size="middle">
            {product?.id === 'fbasecman' && (
              <Button
                danger
                onClick={() => void runTarget('failed')}
                disabled={!environment || stats.failCount === 0}
              >
                重跑失败项 ({stats.failCount})
              </Button>
            )}
            {product?.id === 'fbasecman' && environment && (
              <>
                <Button
                  href={`/api/v1/fbasecman/environments/${encodeURIComponent(environment.id)}/reports/junit`}
                  target="_blank"
                >
                  导出 JUnit XML
                </Button>
                <Button
                  href={`/api/v1/fbasecman/environments/${encodeURIComponent(environment.id)}/reports/html`}
                  target="_blank"
                >
                  导出 HTML 报告
                </Button>
              </>
            )}
          </Space>
        </div>

        {/* 3. 套件折叠卡片列表 (Suite Accordion Cards) */}
        {groupedSuites.length === 0 ? (
          <Card style={{ marginTop: 16, textAlign: 'center', background: '#fff' }}>
            <Empty description="没有匹配当前筛选条件的测试用例" />
          </Card>
        ) : (
          <div style={{ marginTop: 16 }}>
            <Collapse
              activeKey={activeSuites}
              onChange={(keys) => setActiveSuites(Array.isArray(keys) ? (keys as string[]) : [keys])}
              className="suite-accordion"
              bordered={false}
              items={groupedSuites.map((s) => ({
                key: s.suiteId,
                label: (
                  <div className="suite-accordion-header">
                    <Space size="middle">
                      <Typography.Text strong style={{ fontSize: 15 }}>
                        {s.suiteId}
                      </Typography.Text>
                      <Space orientation="horizontal" size={4}>
                        <Tag color="success" style={{ margin: 0 }}>✓ {s.passCount}</Tag>
                        {s.failCount > 0 && <Tag color="error" style={{ margin: 0 }}>✗ {s.failCount}</Tag>}
                        <Tag color="default" style={{ margin: 0 }}>○ {s.untestedCount}</Tag>
                        <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 4 }}>
                          共 {s.total} 项
                        </Typography.Text>
                      </Space>
                    </Space>
                  </div>
                ),
                extra: (
                  <Space onClick={(e) => e.stopPropagation()}>
                    <Button
                      size="small"
                      type="primary"
                      icon={<PlayCircleOutlined />}
                      disabled={!environment}
                      onClick={() => void runSuite(s.suiteId)}
                    >
                      执行整组
                    </Button>
                  </Space>
                ),
                children: (
                  <div className="suite-case-list">
                    {s.cases.map((c) => {
                      const st = getCaseStatus(c.target);
                      const res = resultByTarget.get(c.target);
                      const src = sourceStatuses[c.target];

                      return (
                        <div key={c.target} className="case-row-item">
                          <div className="case-row-left">
                            <Typography.Text code className="case-target-code">
                              {c.target}
                            </Typography.Text>
                            <Typography.Text className="case-title-desc" ellipsis={{ tooltip: c.title }}>
                              {c.title || c.target}
                            </Typography.Text>
                          </div>

                          <div className="case-row-right">
                            <Tag color={statusColor(st)}>
                              {st === 'PASS' ? '通过' : st === 'FAIL' ? '失败' : '未执行'}
                            </Tag>

                            <Space size="small">
                              <Button
                                size="small"
                                type="primary"
                                icon={<PlayCircleOutlined />}
                                disabled={!environment || !c.enabled}
                                onClick={() => void runTarget(c.target, c.suite)}
                              >
                                执行
                              </Button>
                              {product?.id === 'fbasecman' && (
                                <Button
                                  size="small"
                                  icon={<FileTextOutlined />}
                                  disabled={!src && !res}
                                  onClick={() => setReportTarget(c.target)}
                                >
                                  报告
                                </Button>
                              )}
                            </Space>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ),
              }))}
            />
          </div>
        )}
      </section>

      {/* 4. 测试报告抽屉 */}
      <ReportDrawer
        target={reportTarget}
        environmentId={environment?.id}
        onClose={() => setReportTarget(null)}
      />
    </>
  );
}
