import { useEffect, useMemo, useState } from 'react';
import {
  Alert, App, Modal,
} from 'antd';

import {
  api, operationRequest, type Case, type Environment, type Product,
  type RegressionBinding, type Result, type Task,
} from '../api';
import { testAdapter, testFrontend, type TestMode } from '../products/testRegistry';
import DiagnosisDrawer from '../components/DiagnosisDrawer';
import EvidenceDrawer from '../platform/EvidenceDrawer';
import TestBindingBar from '../components/TestBindingBar';
import { FileSearchOutlined } from '@ant-design/icons';

export type SubProduct = TestMode;

type Props = {
  product: Product | undefined;
  environment: Environment | undefined;
  openTask: (taskId: string) => void;
  reload: () => Promise<void>;
  subProduct?: SubProduct;
  environments?: Environment[];
  bindings?: RegressionBinding[];
  tasks?: Task[];
  profileId?: string;
};

type FilterStatus = 'all' | 'PASS' | 'FAIL' | 'UNTESTED';

const ACTIVE_STATUSES = ['QUEUED', 'RUNNING', 'CANCELLING'];


export default function TestsPage({
  product,
  environment,
  openTask,
  reload,
  subProduct = 'cman',
  environments = [],
  bindings = [],
  tasks = [],
  profileId,
}: Props) {
  const { message, modal } = App.useApp();

  // 1. 数据状态
  const [cases, setCases] = useState<Case[]>([]);
  const [results, setResults] = useState<Result[]>([]);
  const [sourceStatuses, setSourceStatuses] = useState<
    Record<string, { status: string; duration?: string; has_report?: boolean; modified_at: number }>
  >({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // 2. 筛选与展开交互状态
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<FilterStatus>('all');
  const [expandedSuites, setExpandedSuites] = useState<Set<string>>(new Set());
  const [reportTarget, setReportTarget] = useState<string | null>(null);
  const [evidenceTarget, setEvidenceTarget] = useState<string | null>(null);
  const [diagnosisTarget, setDiagnosisTarget] = useState<string | null>(null);
  const [terminalTaskId, setTerminalTaskId] = useState<string | null>(null);
  const [failedModalOpen, setFailedModalOpen] = useState(false);
  const [failedReasons, setFailedReasons] = useState<Record<string, string>>({});
  const [failedSteps, setFailedSteps] = useState<Record<string, Array<{
    status: string; title?: string; actual?: string; expected?: string;
  }>>>({});
  const [flakyMap, setFlakyMap] = useState<Record<string, { flaky: boolean; recent: string[] }>>({});

  useEffect(() => { setFailedReasons({}); setFailedSteps({}); }, [environment?.id]);

  // Flaky 追踪：拉取该环境最近判定历史，标记不稳定用例
  useEffect(() => {
    if (!environment?.id) { setFlakyMap({}); return; }
    let cancelled = false;
    void api<Record<string, { flaky: boolean; recent: string[] }>>(
      `/environments/${encodeURIComponent(environment.id)}/flaky`)
      .then((data) => { if (!cancelled) setFlakyMap(data || {}); })
      .catch(() => { if (!cancelled) setFlakyMap({}); });
    return () => { cancelled = true; };
  }, [environment?.id, results]);

  const adapter = useMemo(() => testAdapter(product, subProduct), [product?.id, subProduct]);
  const ReportDrawer = testFrontend(product)?.ReportViewer;
  const RegressionTerminal = testFrontend(product)?.RegressionTerminal;
  const effectiveProductId = adapter.productId;

  // 3. 获取测试用例清单
  useEffect(() => {
    setError('');
    setLoading(true);
    void api<Case[]>(`/cases?product_id=${encodeURIComponent(effectiveProductId)}`)
      .then((data) => {
        let filtered = data;
        if (adapter.suiteFilter) filtered = data.filter((c) => c.suite === adapter.suiteFilter);
        setCases(filtered);
        const allSuites = Array.from(new Set(filtered.map((c) => c.suite)));
        setExpandedSuites(new Set(allSuites));
      })
      .catch((cause) => {
        setCases([]);
        setError(cause.message);
      })
      .finally(() => setLoading(false));
  }, [effectiveProductId, adapter.suiteFilter]);

  // 4. 刷新执行结果与状态
  async function refreshResults() {
    if (environment) {
      await api<Result[]>(`/environments/${encodeURIComponent(environment.id)}/results`)
        .then(setResults)
        .catch(() => setResults([]));
    } else {
      setResults([]);
    }
    if (adapter.sourceStatusPath) {
      await api<Record<string, { status: string; duration?: string; has_report?: boolean; modified_at: number }>>(
        adapter.sourceStatusPath(environment?.id)
      )
        .then(setSourceStatuses)
        .catch(() => setSourceStatuses({}));
    } else {
      setSourceStatuses({});
    }
  }

  useEffect(() => {
    void refreshResults();
  }, [environment?.id, adapter.sourceStatusPath]);

  // 按需轮询（§8.1）：仅本环境存在执行中任务时刷新结果，静态查阅不轮询。
  const hasActiveEnvTask = tasks.some((item) =>
    item.environment_id === environment?.id && ACTIVE_STATUSES.includes(item.status));
  useEffect(() => {
    if (!hasActiveEnvTask) return;
    const timer = window.setInterval(() => {
      void refreshResults();
    }, 2500);
    return () => window.clearInterval(timer);
  }, [environment?.id, effectiveProductId, hasActiveEnvTask]);

  const resultByTarget = useMemo(() => new Map(results.map((item) => [item.target, item])), [results]);

  // 5. 单用例状态与时长判定
  const getCaseStatus = (target: string): 'PASS' | 'FAIL' | 'UNTESTED' => {
    const r = resultByTarget.get(target);
    if (r) return r.status === 'PASS' ? 'PASS' : 'FAIL';
    const s = sourceStatuses[target];
    if (s) return s.status === 'PASS' ? 'PASS' : s.status === 'FAIL' ? 'FAIL' : 'UNTESTED';
    return 'UNTESTED';
  };

  const getCaseDuration = (target: string): string => {
    const s = sourceStatuses[target];
    if (s && s.duration && s.duration !== '-') return s.duration;
    return '-';
  };

  const canViewReport = (target: string): boolean => {
    if (!adapter.supportsLegacyReports) return false;
    const s = sourceStatuses[target];
    if (s && (s.has_report || s.status === 'PASS' || s.status === 'FAIL')) return true;
    const r = resultByTarget.get(target);
    if (r) return true;
    return false;
  };

  // 6. 统计汇总指标
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

  // 7. 套件分组与搜索过滤
  const groupedSuites = useMemo(() => {
    const q = search.trim().toLowerCase();
    const map = new Map<string, Case[]>();

    cases.forEach((c) => {
      const st = getCaseStatus(c.target);
      if (statusFilter !== 'all' && st !== statusFilter) return;

      if (q) {
        const matchTarget = c.target.toLowerCase().includes(q);
        const matchTitle = (c.title || '').toLowerCase().includes(q);
        const matchName = (c.name || '').toLowerCase().includes(q);
        const matchCore = (c.core_id || '').toLowerCase().includes(q);
        const matchSum = (c.summary || '').toLowerCase().includes(q);
        if (!matchTarget && !matchTitle && !matchName && !matchCore && !matchSum) return;
      }

      if (!map.has(c.suite)) map.set(c.suite, []);
      map.get(c.suite)!.push(c);
    });

    return Array.from(map.entries()).map(([suiteId, suiteCases]) => {
      const pCount = suiteCases.filter((c) => getCaseStatus(c.target) === 'PASS').length;
      const fCount = suiteCases.filter((c) => getCaseStatus(c.target) === 'FAIL').length;
      const uCount = suiteCases.filter((c) => getCaseStatus(c.target) === 'UNTESTED').length;
      const firstCase = suiteCases[0];
      return {
        suiteId,
        title: firstCase?.suite_title || suiteId,
        description: firstCase?.suite_description || '',
        cases: suiteCases,
        total: suiteCases.length,
        passCount: pCount,
        failCount: fCount,
        untestedCount: uCount,
      };
    });
  }, [cases, search, statusFilter, resultByTarget, sourceStatuses]);

  // 搜索时自动展开匹配分类
  useEffect(() => {
    if (search.trim()) {
      setExpandedSuites(new Set(groupedSuites.map((s) => s.suiteId)));
    }
  }, [search, groupedSuites]);

  // 8. 失败用例清单（用于弹窗）
  const failedCasesList = useMemo(() => {
    const list: Array<Case & { suiteTitle: string; duration: string }> = [];
    cases.forEach((c) => {
      if (getCaseStatus(c.target) === 'FAIL') {
        list.push({
          ...c,
          suiteTitle: c.suite_title || c.suite,
          duration: getCaseDuration(c.target),
        });
      }
    });
    return list;
  }, [cases, resultByTarget, sourceStatuses]);

  // 打开失败弹窗时，异步获取失败原因与步骤断言
  useEffect(() => {
    if (!failedModalOpen || failedCasesList.length === 0) return;
    let cancelled = false;
    failedCasesList.forEach((c) => {
      if (failedReasons[c.target]) return;
      api<{
        summary?: { reason?: string };
        parsed?: { reason?: string; steps?: Array<{ status: string; title?: string; actual?: string; expected?: string }> };
      }>(adapter.artifactPath ? adapter.artifactPath(c.target, environment?.id) : '')
        .then((data) => {
          if (cancelled) return;
          let reason = data.summary?.reason || data.parsed?.reason;
          if (!reason && data.parsed?.steps) {
            const failedStep = data.parsed.steps.find((s) => s.status === 'FAIL');
            if (failedStep) {
              reason = failedStep.actual || failedStep.expected || '步骤执行失败';
            }
          }
          if (data.parsed?.steps?.length) {
            setFailedSteps((prev) => ({ ...prev, [c.target]: data.parsed!.steps! }));
          }
          setFailedReasons((prev) => ({
            ...prev,
            [c.target]: reason || '未捕获到显式失败原因，请点击【查看完整报告】查看详细日志',
          }));
        })
        .catch(() => {
          if (!cancelled) {
            setFailedReasons((prev) => ({ ...prev, [c.target]: '获取失败原因失败或暂无报告' }));
          }
        });
    });
    return () => {
      cancelled = true;
    };
  }, [failedModalOpen, failedCasesList, environment]);

  // 9. 展开/折叠控制
  const toggleSuite = (suiteId: string) => {
    setExpandedSuites((prev) => {
      const next = new Set(prev);
      if (next.has(suiteId)) next.delete(suiteId);
      else next.add(suiteId);
      return next;
    });
  };

  const expandAll = () => {
    setExpandedSuites(new Set(groupedSuites.map((s) => s.suiteId)));
  };

  const collapseAll = () => {
    setExpandedSuites(new Set());
  };

  // 10. 执行单个用例
  async function runTarget(target: string, cluster?: string) {
    if (!adapter.action) {
      message.error('此产品没有注册可执行的前端测试动作');
      return;
    }
    if (!environment) {
      message.warning('请先在顶部选择绑定测试环境');
      return;
    }
    const effectiveCluster = cluster || adapter.clusterForSuite('');
    try {
      const task = await operationRequest(
        environment.id,
        adapter.action,
        target,
        adapter.mode === 'cman' ? {} : { cluster: effectiveCluster },
        true,
      );
      if (adapter.supportsTerminal) setTerminalTaskId(task.id);
      else openTask(task.id);
    } catch (cause) {
      message.error((cause as Error).message);
    }
  }

  // 11. 执行整套用例
  async function runSuite(suiteId: string) {
    if (!environment) {
      message.warning('请先在顶部选择绑定测试环境');
      return;
    }
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
    if (confirmed) {
      const cluster = adapter.clusterForSuite(suiteId);
      await runTarget(suiteId, cluster);
    }
  }

  return (
    <>
      <TestBindingBar
        product={product}
        profileId={profileId}
        environments={environments}
        bindings={bindings}
        onChanged={reload}
      />
      {!environment && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 18 }}
          message="请先选择测试环境"
          description="在顶部选择所要绑定的环境后，即可向该环境提交执行测试用例；用例清单与历史报告在未选环境时仍可离线查阅。"
        />
      )}

      {error && (
        <Alert type="warning" showIcon style={{ marginBottom: 18 }} message="用例来源加载异常" description={error} />
      )}

      {/* =====================================================================
          沉浸式暗黑开发者控制台主看板
          ===================================================================== */}
      <div className="regress-console-wrapper">
        {/* 1. 顶部 5 项指标卡片 (Metrics Grid) */}
        <section className="metrics-grid">
          {/* 总测试用例 */}
          <div
            className={`metric-card card-total clickable ${statusFilter === 'all' ? 'active-filter' : ''}`}
            onClick={() => setStatusFilter('all')}
            title="点击查看全部用例"
          >
            <div className="metric-meta">
              <span className="metric-label">总测试用例</span>
              <span className="metric-sub">{groupedSuites.length} 个分类</span>
            </div>
            <div className="metric-value">{stats.total}</div>
          </div>

          {/* 已通过 (PASS) */}
          <div
            className={`metric-card card-pass clickable ${statusFilter === 'PASS' ? 'active-filter' : ''}`}
            onClick={() => setStatusFilter('PASS')}
            title="点击筛选已通过用例"
          >
            <div className="metric-meta">
              <span className="metric-label">已通过 (PASS)</span>
              <span className="metric-sub">验证合格</span>
            </div>
            <div className="metric-value text-pass">{stats.passCount}</div>
          </div>

          {/* 已失败 (FAIL) */}
          <div
            className={`metric-card card-fail clickable ${statusFilter === 'FAIL' ? 'active-filter' : ''}`}
            onClick={() => {
              setStatusFilter('FAIL');
              setFailedModalOpen(true);
            }}
            title="点击快速查看并筛选全部失败用例"
          >
            <div className="metric-meta">
              <span className="metric-label">已失败 (FAIL)</span>
              <span className="metric-action-tag">点击查看 ❯</span>
            </div>
            <div className="metric-value text-fail">{stats.failCount}</div>
          </div>

          {/* 未执行 (UNTESTED) */}
          <div
            className={`metric-card card-untested clickable ${statusFilter === 'UNTESTED' ? 'active-filter' : ''}`}
            onClick={() => setStatusFilter('UNTESTED')}
            title="点击筛选未执行用例"
          >
            <div className="metric-meta">
              <span className="metric-label">未执行 (UNTESTED)</span>
              <span className="metric-sub">等待执行</span>
            </div>
            <div className="metric-value text-untested">{stats.untestedCount}</div>
          </div>

          {/* 综合通过率 */}
          <div className="metric-card card-rate" title="综合通过率">
            <div className="metric-meta">
              <span className="metric-label">综合通过率</span>
              <span className="metric-sub">{stats.passCount} / {stats.total}</span>
            </div>
            <div className="metric-value text-rate">{stats.rate}%</div>
            <div className="progress-bar-bg">
              <div className="progress-bar-fill" style={{ width: `${stats.rate}%` }} />
            </div>
          </div>
        </section>

        {/* 2. 失败用例筛选高亮 Banner (仅在筛选 FAIL 时显示) */}
        {statusFilter === 'FAIL' && (
          <div className="filter-banner">
            <div className="filter-banner-left">
              <span className="filter-banner-icon">⚠️</span>
              <span className="filter-banner-text">当前已筛选出失败用例 ({stats.failCount} 项)</span>
            </div>
            <div className="filter-banner-right">
              <button className="filter-banner-btn primary" onClick={() => setFailedModalOpen(true)}>
                📋 失败用例清单弹窗
              </button>
              {adapter.supportsLegacyReports && (
                <button
                  className="filter-banner-btn danger"
                  disabled={!environment || stats.failCount === 0}
                  onClick={() => void runTarget('failed')}
                >
                  ▶ 重跑所有失败项
                </button>
              )}
              <button className="filter-banner-btn" onClick={() => setStatusFilter('all')}>
                ✕ 显示全部用例
              </button>
            </div>
          </div>
        )}

        {/* 3. 工具与筛选操作栏 (Toolbar) */}
        <section className="toolbar">
          <div className="search-box">
            <span className="search-icon">🔍</span>
            <input
              type="text"
              placeholder="搜索用例名称、Core ID (如 CORE-13)、中文描述..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              autoComplete="off"
            />
            {search && (
              <button className="clear-search-btn" onClick={() => setSearch('')} title="清空搜索">
                ✕
              </button>
            )}
          </div>

          <div className="filter-group">
            <button
              className={`filter-pill ${statusFilter === 'all' ? 'active' : ''}`}
              onClick={() => setStatusFilter('all')}
            >
              全部 <span className="pill-count">{stats.total}</span>
            </button>
            <button
              className={`filter-pill ${statusFilter === 'PASS' ? 'active' : ''}`}
              onClick={() => setStatusFilter('PASS')}
            >
              通过 <span className="pill-count">{stats.passCount}</span>
            </button>
            <button
              className={`filter-pill ${statusFilter === 'FAIL' ? 'active' : ''}`}
              onClick={() => setStatusFilter('FAIL')}
            >
              失败 <span className="pill-count">{stats.failCount}</span>
            </button>
            <button
              className={`filter-pill ${statusFilter === 'UNTESTED' ? 'active' : ''}`}
              onClick={() => setStatusFilter('UNTESTED')}
            >
              未执行 <span className="pill-count">{stats.untestedCount}</span>
            </button>
          </div>

          <div className="toolbar-actions">
            <button className="tool-btn" onClick={expandAll} title="展开全部用例分类">
              ➕ 全部展开
            </button>
            <button className="tool-btn" onClick={collapseAll} title="折叠全部用例分类">
              ➖ 全部折叠
            </button>
            <button className="tool-btn" onClick={() => void refreshResults()} title="重新拉取测试状态">
              🔄 刷新状态
            </button>

            {adapter.supportsLegacyReports && (
              <button
                className="tool-btn danger"
                disabled={!environment || stats.failCount === 0}
                onClick={() => void runTarget('failed')}
                title="批量执行当前全部失败用例"
              >
                ⚠️ 重跑失败项 ({stats.failCount})
              </button>
            )}

            {adapter.reportPath && environment && (
              <>
                <a
                  className="tool-btn"
                  href={`/api/v1${adapter.reportPath(environment.id, 'junit')}`}
                  target="_blank"
                  rel="noreferrer"
                  title="导出标准 JUnit XML 报告"
                >
                  ⬇️ 导出 JUnit
                </a>
                <a
                  className="tool-btn"
                  href={`/api/v1${adapter.reportPath(environment.id, 'html')}`}
                  target="_blank"
                  rel="noreferrer"
                  title="导出沉浸式 HTML 报告"
                >
                  📄 导出 HTML
                </a>
              </>
            )}
          </div>
        </section>

        {/* 4. 套件树与用例列表 (Tree & Accordion Cards) */}
        <section className="tree-container">
          {loading && cases.length === 0 ? (
            <div className="loading-state">
              <span>正在加载测试清单与执行状态...</span>
            </div>
          ) : groupedSuites.length === 0 ? (
            <div className="loading-state">
              <span>🔍 没有符合当前筛选条件的测试用例</span>
            </div>
          ) : (
            groupedSuites.map((s) => {
              const isExpanded = expandedSuites.has(s.suiteId);

              return (
                <div key={s.suiteId} className={`suite-card ${isExpanded ? 'expanded' : ''}`}>
                  <div className="suite-header" onClick={() => toggleSuite(s.suiteId)}>
                    <div className="suite-header-left">
                      <span className="suite-chevron">▶</span>
                      <div className="suite-title-group">
                        <div className="suite-title-row">
                          <span className="suite-title">{s.title}</span>
                          <span className="suite-id-tag">{s.suiteId}</span>
                        </div>
                        {s.description && <span className="suite-description">{s.description}</span>}
                      </div>
                    </div>

                    <div className="suite-header-right">
                      <div className="suite-stats-pill">
                        <span className="suite-stat-item pass">✓ {s.passCount}</span>
                        {s.failCount > 0 && <span className="suite-stat-item fail">✗ {s.failCount}</span>}
                        <span className="suite-stat-item untested">○ {s.untestedCount}</span>
                        <span className="suite-stat-item total">共 {s.total} 项</span>
                      </div>

                      <button
                        className="suite-action-btn"
                        disabled={!environment}
                        onClick={(e) => {
                          e.stopPropagation();
                          void runSuite(s.suiteId);
                        }}
                        title="依次执行该分类下的全部用例"
                      >
                        <span>▶</span> 执行整组
                      </button>
                    </div>
                  </div>

                  <div className="suite-cases-list">
                    {s.cases.map((c) => {
                      const st = getCaseStatus(c.target);
                      const statusClass = st.toLowerCase();
                      const dur = getCaseDuration(c.target);
                      const canView = canViewReport(c.target);
                      const result = resultByTarget.get(c.target);
                      const hasArchive = Boolean(environment && result?.artifact_dir?.endsWith(
                        `/regression/${environment.id}/${c.target}`));

                      return (
                        <div key={c.target} className="case-row">
                          <div className="case-left">
                            {c.core_id ? (
                              <span className="core-badge" title="方案用例编号">
                                {c.core_id}
                              </span>
                            ) : (
                              <span className="core-badge core-badge-placeholder" aria-hidden="true">
                                -
                              </span>
                            )}
                            <div className="case-name-group">
                              <span className="case-name" title={c.target}>
                                {c.name || c.target}
                              </span>
                              <span className="case-summary" title={c.summary || c.title}>
                                {c.summary || c.title}
                              </span>
                            </div>
                          </div>

                          <div className="case-right">
                            <span className={`result-badge ${statusClass}`}>
                              {st === 'PASS' ? '✓ PASS' : st === 'FAIL' ? '✗ FAIL' : '○ UNTESTED'}
                            </span>
                            {flakyMap[c.target]?.flaky && (
                              <span className="flaky-badge"
                                title={`最近判定不一致: ${(flakyMap[c.target].recent || []).join(' → ')}`}>
                                ⚡ flaky
                              </span>
                            )}
                            <span className="duration-label" title="测试耗时">
                              {dur}
                            </span>

                            <button
                              className="btn-run-case"
                              disabled={!environment || !c.enabled}
                              onClick={() => void runTarget(c.target, c.suite)}
                              title="单独执行此测试项"
                            >
                              ▶ 执行
                            </button>

                            {adapter.supportsLegacyReports && (
                              <button
                                className="btn-view-report"
                                disabled={!canView}
                                onClick={() => setReportTarget(c.target)}
                                title={canView ? '点击查看沉浸式步骤报告' : '用例尚未执行，暂无报告'}
                              >
                                📄 查看报告
                              </button>
                            )}
                            {hasArchive && <button className="btn-view-report"
                              onClick={() => setEvidenceTarget(c.target)} title="查看本次平台归档证据">
                              <FileSearchOutlined /> 证据
                            </button>}
                            {st === 'FAIL' && resultByTarget.has(c.target) && (
                              <button className="btn-diagnose" onClick={() => setDiagnosisTarget(c.target)} title="结合证据与源码分析失败">
                                AI 分析
                              </button>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })
          )}
        </section>
      </div>

      {/* 5. 已失败测试用例清单弹窗 (Failed Cases Modal) */}
      <Modal
        open={failedModalOpen}
        onCancel={() => setFailedModalOpen(false)}
        footer={null}
        width={920}
        className="regress-dark-modal"
        title={
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingRight: 32 }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span className="failed-modal-title">已失败测试用例清单</span>
                <span className="result-badge fail" style={{ minWidth: 60, padding: '2px 8px' }}>
                  {failedCasesList.length} 项失败
                </span>
              </div>
              <div className="failed-modal-description">
                点击【查看完整报告】深入分析失败原因与日志，或点击【单独重跑】进行针对性复测
              </div>
            </div>
            {adapter.supportsLegacyReports && (
              <button
                className="filter-banner-btn danger"
                style={{ padding: '0.45rem 0.95rem', fontSize: '0.82rem', marginLeft: 16 }}
                disabled={!environment || failedCasesList.length === 0}
                onClick={() => {
                  setFailedModalOpen(false);
                  void runTarget('failed');
                }}
              >
                ▶ 批量重跑所有失败项
              </button>
            )}
          </div>
        }
      >
        <div className="failed-cases-container">
          {failedCasesList.length === 0 ? (
            <div className="loading-state">
              <span style={{ fontSize: '2.2rem' }}>🎉</span>
              <span style={{ color: 'var(--color-pass)', fontWeight: 600, fontSize: '1.1rem' }}>
                太棒了！当前没有任何失败的测试用例。
              </span>
              <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>所有已执行的用例均已通过验证。</span>
            </div>
          ) : (
            failedCasesList.map((c) => (
              <div key={c.target} className="failed-case-card">
                <div className="failed-card-top">
                  <div className="failed-card-info">
                    {c.core_id && <span className="core-badge">{c.core_id}</span>}
                    <span className="failed-card-title">{c.target}</span>
                    <span className="failed-card-suite">{c.suite}</span>
                  </div>
                  <span className="result-badge fail">✗ FAIL</span>
                </div>

                <div className="failed-card-description">{c.summary || c.title}</div>

                <div className="failed-card-reason-box">
                  {failedReasons[c.target] || '正在载入失败原因与检测项...'}
                </div>

                {(failedSteps[c.target] || []).some((s) => s.status === 'FAIL') && (
                  <div className="failed-step-diff-list">
                    {(failedSteps[c.target] || [])
                      .filter((s) => s.status === 'FAIL')
                      .map((step, index) => (
                        <div key={index} className="failed-step-diff">
                          <div className="failed-step-diff-title">
                            步骤{step.title ? `：${step.title}` : ` ${index + 1}`}
                          </div>
                          <div className="failed-step-diff-grid">
                            <div className="diff-col">
                              <div className="diff-col-label">预期</div>
                              <pre>{step.expected || '（无）'}</pre>
                            </div>
                            <div className="diff-col actual">
                              <div className="diff-col-label">实际</div>
                              <pre>{step.actual || '（无）'}</pre>
                            </div>
                          </div>
                        </div>
                      ))}
                  </div>
                )}

                <div className="failed-card-bottom">
                  <span>测试耗时: {c.duration}</span>
                  <div className="failed-card-actions">
                    <button
                      className="btn-run-case"
                      disabled={!environment}
                      onClick={() => {
                        setFailedModalOpen(false);
                        void runTarget(c.target, c.suite);
                      }}
                    >
                      ▶ 单独重跑
                    </button>
                    {adapter.supportsLegacyReports && (
                      <button
                        className="btn-view-report"
                        onClick={() => {
                          setFailedModalOpen(false);
                          setReportTarget(c.target);
                        }}
                      >
                        📄 查看完整报告
                      </button>
                    )}
                    {environment && resultByTarget.has(c.target) && (
                      <button className="btn-diagnose" onClick={() => {
                        setFailedModalOpen(false);
                        setDiagnosisTarget(c.target);
                      }}>AI 分析</button>
                    )}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </Modal>

      {/* 6. 测试报告抽屉 */}
      {ReportDrawer && <ReportDrawer
        target={reportTarget}
        environmentId={environment?.id}
        onClose={() => setReportTarget(null)}
      />}
      <EvidenceDrawer environmentId={environment?.id} target={evidenceTarget}
        onClose={() => setEvidenceTarget(null)} />
      <DiagnosisDrawer target={diagnosisTarget} environmentId={environment?.id}
        onClose={() => setDiagnosisTarget(null)} openTask={openTask} />
      {adapter.supportsTerminal && RegressionTerminal && <RegressionTerminal taskId={terminalTaskId}
        onInspect={openTask} onFinished={() => void refreshResults()} />}
    </>
  );
}
