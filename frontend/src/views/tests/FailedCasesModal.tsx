import { useEffect, useRef, useState } from 'react';
import { Modal } from 'antd';

import { api, type Case, type Environment, type Result } from '../../platform/api';
import type { TestProductAdapter } from '../../products/testRegistry';

export type FailedCaseItem = Case & { suiteTitle: string; duration: string };

type FailedStep = {
  status: string;
  title?: string;
  actual?: string;
  expected?: string;
};

type Props = {
  open: boolean;
  onClose: () => void;
  failedCasesList: FailedCaseItem[];
  environment: Environment | undefined;
  adapter: TestProductAdapter;
  resultByTarget: Map<string, Result>;
  canViewReport: (target: string) => boolean;
  runTarget: (target: string, suiteId?: string) => void | Promise<void>;
  onSelectReport: (target: string) => void;
  onSelectDiagnosis: (target: string) => void;
};

export default function FailedCasesModal({
  open,
  onClose,
  failedCasesList,
  environment,
  adapter,
  resultByTarget,
  canViewReport,
  runTarget,
  onSelectReport,
  onSelectDiagnosis,
}: Props) {
  const [failedReasons, setFailedReasons] = useState<Record<string, string>>({});
  const [failedSteps, setFailedSteps] = useState<Record<string, FailedStep[]>>({});
  const fetchedTargetsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    fetchedTargetsRef.current.clear();
    setFailedReasons({});
    setFailedSteps({});
  }, [environment?.id]);

  useEffect(() => {
    if (!open || failedCasesList.length === 0 || (!adapter.artifactPath && !environment)) return;
    let cancelled = false;
    failedCasesList.forEach((c) => {
      if (fetchedTargetsRef.current.has(c.target)) return;
      fetchedTargetsRef.current.add(c.target);
      api<{
        reason?: string;
        steps?: Array<{ result?: string; status: string; title?: string; actual?: string; expected?: string }>;
        summary?: { reason?: string };
        parsed?: { reason?: string; steps?: FailedStep[] };
      }>(adapter.artifactPath ? adapter.artifactPath(c.target, environment?.id)
        : `/environments/${encodeURIComponent(environment!.id)}/results/${encodeURIComponent(c.target)}/report`)
        .then((data) => {
          if (cancelled) {
            fetchedTargetsRef.current.delete(c.target);
            return;
          }
          if (data.steps) data.parsed = { reason: data.reason, steps: data.steps.map((step) => ({ ...step, status: step.result || step.status })) };
          let reason = data.reason || data.summary?.reason || data.parsed?.reason;
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
          if (cancelled) {
            fetchedTargetsRef.current.delete(c.target);
            return;
          }
          setFailedReasons((prev) => ({ ...prev, [c.target]: '获取失败原因失败或暂无报告' }));
        });
    });
    return () => {
      cancelled = true;
    };
  }, [open, failedCasesList, environment, adapter]);

  return (
    <Modal
      open={open}
      onCancel={onClose}
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
          {(adapter.supportsLegacyReports || environment) && (
            <button
              className="filter-banner-btn danger"
              style={{ padding: '0.45rem 0.95rem', fontSize: '0.82rem', marginLeft: 16 }}
              disabled={!environment || failedCasesList.length === 0}
              onClick={() => {
                onClose();
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
                      onClose();
                      void runTarget(c.target, c.suite);
                    }}
                  >
                    ▶ 单独重跑
                  </button>
                  {canViewReport(c.target) && (
                    <button
                      className="btn-view-report"
                      onClick={() => {
                        onClose();
                        onSelectReport(c.target);
                      }}
                    >
                      📄 查看完整报告
                    </button>
                  )}
                  {environment && resultByTarget.has(c.target) && (
                    <button className="btn-diagnose" onClick={() => {
                      onClose();
                      onSelectDiagnosis(c.target);
                    }}>AI 分析</button>
                  )}
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </Modal>
  );
}
