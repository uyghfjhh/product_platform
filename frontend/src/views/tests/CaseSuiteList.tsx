import { FileSearchOutlined } from '@ant-design/icons';
import { Progress } from 'antd';
import type { Case, Environment, Result, Task } from '../../platform/api';
import type { TestProductAdapter } from '../../products/testRegistry';
import type { FlakyStatus } from './useTestData';

export type CaseGroup = {
  suiteId: string; title: string; description: string; cases: Case[];
  total: number; passCount: number; failCount: number; untestedCount: number;
};
type Props = {
  adapter: TestProductAdapter;
  cases: Case[]; loading: boolean; groupedSuites: CaseGroup[]; expandedSuites: Set<string>;
  environment: Environment | undefined;
  tasks: Task[];
  toggleSuite: (suiteId: string) => void;
  runSuite: (suiteId: string) => Promise<void>;
  runTarget: (target: string, cluster?: string) => Promise<void>;
  getCaseStatus: (target: string) => 'PASS' | 'FAIL' | 'UNTESTED';
  getCaseDuration: (target: string) => string;
  canViewReport: (target: string) => boolean;
  resultByTarget: Map<string, Result>; flakyMap: Record<string, FlakyStatus>;
  setReportTarget: (target: string) => void;
  setEvidenceTarget: (target: string) => void;
  setDiagnosisTarget: (target: string) => void;
};

const ACTIVE_TASK = new Set(['QUEUED', 'RUNNING', 'CANCELLING']);

export default function CaseSuiteList({ adapter, cases, loading, groupedSuites, expandedSuites,
  environment, tasks, toggleSuite, runSuite, runTarget, getCaseStatus, getCaseDuration,
  canViewReport, resultByTarget, flakyMap, setReportTarget, setEvidenceTarget, setDiagnosisTarget }: Props) {
  return (
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
          const running = tasks.find((t) =>
            t.target === s.suiteId && ACTIVE_TASK.has(t.status) &&
            t.environment_id === environment?.id && t.progress);

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

              {running?.progress && (
                <div className="suite-progress" onClick={(e) => e.stopPropagation()}>
                  <Progress
                    percent={Math.round((running.progress.done / running.progress.total) * 100)}
                    status={running.status === 'CANCELLING' ? 'exception' : 'active'}
                    format={() => `${running.progress!.done}/${running.progress!.total}`}
                  />
                  <span className="suite-progress-label">最近完成: {running.progress.label}</span>
                </div>
              )}

              <div className="suite-cases-list">
                {s.cases.map((c) => {
                  const st = getCaseStatus(c.target);
                  const statusClass = st.toLowerCase();
                  const dur = getCaseDuration(c.target);
                  const canView = canViewReport(c.target);
                  const result = resultByTarget.get(c.target);
                  const hasArchive = Boolean(environment && result?.artifact_dir);

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

                        {(adapter.supportsLegacyReports || environment) && (
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
  );
}
