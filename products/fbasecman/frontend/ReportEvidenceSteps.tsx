import { useState } from 'react';
import { Alert, Button, Tag } from 'antd';
import { normalizeStep } from '../../../frontend/src/platform/stepNormalizer';
import './reportEvidence.css';

export default function ReportEvidenceSteps({ steps }: { steps: unknown[]; evidenceBasePath?: string }) {
  const [showActions, setShowActions] = useState(false);
  const normalized = steps.map((step, index) => normalizeStep(step, index)).map((step, index) => ({
    ...step,
    // Report sources may contain suite-local order numbers. The viewer owns
    // display numbering and must present one continuous sequence.
    order: index + 1,
  }));
  const checks = normalized.filter(step => step.kind === 'verify');
  const defaultVisible = normalized.filter(step => step.kind !== 'action');
  const actions = normalized.filter(step => step.kind === 'action');
  const diffs = normalized.filter(step => step.kind === 'diff');
  const visible = showActions ? normalized : defaultVisible;
  return <div className="cman-report-evidence">
    <div className="cman-report-toolbar"><strong>业务验证 · {checks.length} 项（执行动作 {actions.length}，配置比对 {diffs.length}）</strong><Button size="small" onClick={() => setShowActions(!showActions)}>{showActions ? '只看业务验证' : `查看全部步骤（含 ${actions.length} 项执行动作）`}</Button></div>
    {!checks.length && <Alert type="warning" showIcon message="本次报告没有记录业务断言" description="SQL 或命令执行成功只能证明操作完成，不能证明用例目标已经满足。执行动作见下方；排查记录可从运行时日志和完整报告查看。" />}
    {(visible.length ? visible : actions).map((step, index) => <section className="cman-evidence-card" key={step.key}>
      <header><div><span className="cman-step-number">{index + 1}</span><strong>{step.cleanTitle}</strong><span className="cman-step-kind">{step.kind === 'action' ? '执行动作' : step.kind === 'diff' ? '配置比对' : '结果验证'}</span></div><Tag color={step.status === 'PASS' ? 'success' : step.status === 'FAIL' ? 'error' : 'default'}>{step.status}</Tag></header>
      {step.exampleCode && <div className="cman-evidence-code"><label>关键代码（节选）</label><pre>{step.exampleCode}</pre></div>}
      <div className="cman-evidence-comparison"><div><label>期望结果</label><p>{step.expected || '本次未记录期望，不能从当前用例补造历史断言'}</p></div><div><label>执行命令、实际结果与检查分析</label>{step.command && !step.exampleCode && <pre className="cman-actual-output cman-inline-command">{step.command}</pre>}{step.actualPayload?.type === 'diff' ? <pre className="cman-actual-output cman-diff-output">{(step.actualPayload.raw || '').split('\n').map((line, i) => <span key={i} className={line.startsWith('+') && !line.startsWith('+++') ? 'diff-add' : line.startsWith('-') && !line.startsWith('---') ? 'diff-remove' : line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++') ? 'diff-header' : ''}>{line}{'\n'}</span>)}</pre> : <pre className="cman-actual-output">{step.actualSummary || step.actualPayload?.raw || '本次未记录实际结果'}</pre>}{step.analysis && <div className="cman-inline-analysis"><strong>检查分析</strong><p>{step.analysis}</p></div>}</div></div>
      {step.evidenceItems.filter(item => item.type !== 'artifact').map((item, evidenceIndex) => <details key={evidenceIndex}><summary>{item.label}</summary><pre>{item.value}</pre></details>)}
    </section>)}
  </div>;
}
