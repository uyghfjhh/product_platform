import { useMemo, useState } from 'react';
import { Alert, Button, Pagination, Tag } from 'antd';
import { normalizeStep } from '../../../frontend/src/platform/stepNormalizer';
import './reportEvidence.css';

export default function ReportEvidenceSteps({ steps }: { steps: unknown[]; evidenceBasePath?: string }) {
  const [showActions, setShowActions] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const normalized = useMemo(() => steps.map((step, index) => normalizeStep(step, index)).map((step, index) => ({
    ...step,
    // Report sources may contain suite-local order numbers. The viewer owns
    // display numbering and must present one continuous sequence.
    order: index + 1,
  })), [steps]);
  const checks = normalized.filter(step => step.kind === 'verify');
  const rank = (status: string) => status === 'FAIL' ? 0 : status === 'BLOCKED' ? 1 : 2;
  const defaultVisible = normalized.filter(step => step.kind !== 'action').sort((left, right) => rank(left.status) - rank(right.status));
  const actions = normalized.filter(step => step.kind === 'action');
  const diffs = normalized.filter(step => step.kind === 'diff');
  const visible = showActions ? normalized : defaultVisible;
  const displayed = visible.length ? visible : actions;
  const currentPage = Math.min(page, Math.max(1, Math.ceil(displayed.length / pageSize)));
  const offset = (currentPage - 1) * pageSize;
  const paged = displayed.slice(offset, offset + pageSize);
  const isGuc = normalized.some(step => /MMR|主备|mmr\//.test(step.cleanTitle)
    && /hint|sql_parse/.test(step.cleanTitle));
  const groups = new Map<string, typeof paged>();
  for (const step of paged) {
    const label = isGuc && !showActions ? step.cleanTitle.split('：')[0] : '';
    groups.set(label, [...(groups.get(label) || []), step]);
  }
  const pager = displayed.length > pageSize ? <Pagination current={currentPage} pageSize={pageSize}
    total={displayed.length} showSizeChanger pageSizeOptions={[20, 50, 100]}
    showTotal={(total, range) => `第 ${range[0]}—${range[1]} 项，共 ${total} 项`}
    onChange={(nextPage, nextSize) => { setPage(nextPage); setPageSize(nextSize); }} /> : null;
  return <div className="cman-report-evidence">
    <div className="cman-report-toolbar"><strong>业务验证 · {checks.length} 项（执行动作 {actions.length}，配置比对 {diffs.length}）</strong><Button size="small" onClick={() => { setShowActions(!showActions); setPage(1); }}>{showActions ? '只看业务验证' : `查看全部步骤（含 ${actions.length} 项执行动作）`}</Button></div>
    {!checks.length && <Alert type="warning" showIcon message="本次报告没有记录业务断言" description="SQL 或命令执行成功只能证明操作完成，不能证明用例目标已经满足。执行动作见下方；排查记录可从运行时日志和完整报告查看。" />}
    {pager && <div className="cman-report-pagination">{pager}</div>}
    {Array.from(groups, ([label, items]) => {
      const content = items.map(step => <section className="cman-evidence-card" key={step.key}>
      <header><div><span className="cman-step-number">{offset + paged.indexOf(step) + 1}</span><strong>{label ? step.cleanTitle.slice(label.length + 1) : step.cleanTitle}</strong><span className="cman-step-kind">{step.kind === 'action' ? '执行动作' : step.kind === 'diff' ? '配置比对' : '结果验证'}</span></div><Tag color={step.status === 'PASS' ? 'success' : step.status === 'FAIL' ? 'error' : 'default'}>{step.status}</Tag></header>
      {step.exampleCode && <div className="cman-evidence-code"><label>关键代码（节选）</label><pre>{step.exampleCode}</pre></div>}
      <div className="cman-evidence-comparison"><div>{step.command && !step.exampleCode && <><label>执行内容</label><pre className="cman-actual-output cman-inline-command">{step.command}</pre></>}{step.expected && <><label>期望结果</label><p>{step.expected}</p></>}<label>实际结果</label>{step.actualPayload?.type === 'diff' ? <pre className="cman-actual-output cman-diff-output">{(step.actualPayload.raw || '').split('\n').map((line, i) => <span key={i} className={line.startsWith('+') && !line.startsWith('+++') ? 'diff-add' : line.startsWith('-') && !line.startsWith('---') ? 'diff-remove' : line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++') ? 'diff-header' : ''}>{line}{'\n'}</span>)}</pre> : <pre className="cman-actual-output">{step.actualSummary || step.actualPayload?.raw || '本次未记录实际结果'}</pre>}{step.analysis && <div className="cman-inline-analysis"><strong>分析与结论</strong><p>{step.analysis}</p></div>}</div></div>
      {step.evidenceItems.filter(item => item.type !== 'artifact').map((item, evidenceIndex) => <details key={evidenceIndex}><summary>{item.label}</summary><pre>{item.value}</pre></details>)}
    </section>);
      return label ? <details className="cman-scenario-group" key={label}
        open={items.some(step => step.status !== 'PASS')}>
        <summary>{label}<span>{items.length} 项 · {items.every(step => step.status === 'PASS') ? 'PASS' : '存在未通过检查'}</span></summary>
        {content}
      </details> : <div key={label}>{content}</div>;
    })}
    {pager && <div className="cman-report-pagination">{pager}</div>}
  </div>;
}
