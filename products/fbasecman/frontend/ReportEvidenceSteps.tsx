import { useState } from 'react';
import { Alert, Button, Tag } from 'antd';
import { normalizeStep } from '../../../frontend/src/platform/stepNormalizer';
import './reportEvidence.css';

export default function ReportEvidenceSteps({ steps }: { steps: unknown[] }) {
  const [showActions, setShowActions] = useState(false);
  const normalized = steps.map((step, index) => normalizeStep(step, index));
  const checks = normalized.filter(step => step.kind !== 'action');
  const actions = normalized.filter(step => step.kind === 'action');
  const visible = showActions ? normalized : checks;
  return <div className="cman-report-evidence">
    <div className="cman-report-toolbar"><strong>关键验证 · {checks.length} 项</strong><Button size="small" onClick={() => setShowActions(!showActions)}>{showActions ? '只看关键验证' : `查看全部步骤（含 ${actions.length} 项执行动作）`}</Button></div>
    {!checks.length && <Alert type="warning" showIcon message="本次报告没有记录业务断言" description="SQL 或命令执行成功只能证明操作完成，不能证明用例目标已经满足。执行动作见下方；排查记录可从运行时日志和完整报告查看。" />}
    {(visible.length ? visible : actions).map(step => <section className="cman-evidence-card" key={step.key}>
      <header><div><span className="cman-step-number">{step.order}</span><strong>{step.cleanTitle}</strong><span className="cman-step-kind">{step.kind === 'action' ? '执行动作' : '结果验证'}</span></div><Tag color={step.status === 'PASS' ? 'success' : step.status === 'FAIL' ? 'error' : 'default'}>{step.status}</Tag></header>
      {step.exampleCode && <div className="cman-evidence-code"><label>关键代码（节选）</label><pre>{step.exampleCode}</pre></div>}
      <div className="cman-evidence-comparison"><div><label>期望结果</label><p>{step.expected || '本次未记录期望，不能从当前用例补造历史断言'}</p></div><div><label>实际结果</label><p>{step.actualSummary || step.actualPayload?.raw || '本次未记录实际结果'}</p></div></div>
      <div className="cman-evidence-analysis"><label>判定依据</label><p>{step.analysis || (step.kind === 'action' ? '这是执行记录，业务结果由独立验证步骤判断。' : '本次未记录判定依据；PASS 标签不足以说明如何满足期望。')}</p></div>
      {step.command && !step.exampleCode && <details><summary>查看执行命令</summary><pre>{step.command}</pre></details>}
    </section>)}
  </div>;
}
