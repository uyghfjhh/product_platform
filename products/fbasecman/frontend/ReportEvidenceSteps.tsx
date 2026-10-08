import { useMemo, useState } from 'react';
import { Alert, Button, Pagination, Tag } from 'antd';
import { normalizeStep } from '../../../frontend/src/platform/stepNormalizer';
import './reportEvidence.css';

type OutcomeRow = { operation: string; client: string; parameter: string; expected: string; actual: string; host: string; port: string; pid: string; role: string; status: string; sql?: string; evidence?: string };

type BusinessCheck = { operation: string; command?: string; expected: string; actual: string; analysis: string;
  status: string; driver?: string; evidence?: string; measurement_sql?: string; measurement_evidence?: string };

export default function ReportEvidenceSteps({ steps }: { steps: unknown[]; evidenceBasePath?: string }) {
  const [showActions, setShowActions] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const normalized = useMemo(() => steps.map((step, index) => normalizeStep(step, index)).map((step, index) => ({
    ...step,
    outcomeRows: Array.isArray((steps[index] as Record<string, unknown>)?.outcome_rows)
      ? (steps[index] as { outcome_rows: OutcomeRow[] }).outcome_rows : [],
    processSteps: Array.isArray((steps[index] as Record<string, unknown>)?.process_steps)
      ? (steps[index] as { process_steps: BusinessCheck[] }).process_steps : [],
    businessChecks: Array.isArray((steps[index] as Record<string, unknown>)?.business_checks)
      ? (steps[index] as { business_checks: BusinessCheck[] }).business_checks : [],
    baselineContext: String((steps[index] as Record<string, unknown>)?.baseline_context || ''),
    reportScope: (steps[index] as { report_scope?: Record<string, string> })?.report_scope,
    // Report sources may contain suite-local order numbers. The viewer owns
    // display numbering and must present one continuous sequence.
    order: index + 1,
  })), [steps]);
  const isGuc = normalized.some(step => /MMR|主备|mmr\//.test(step.cleanTitle)
    && /hint|sql_parse/.test(step.cleanTitle));
  const summaries = normalized.filter(step => step.cleanTitle.includes('验证汇总'));
  const compact = isGuc && summaries.length > 0;
  const checks = normalized.filter(step => step.kind === 'verify');
  const rank = (status: string) => status === 'FAIL' ? 0 : status === 'BLOCKED' ? 1 : 2;
  const defaultVisible = normalized.filter(step => step.kind !== 'action').sort((left, right) => rank(left.status) - rank(right.status));
  const actions = normalized.filter(step => step.kind === 'action');
  const diffs = normalized.filter(step => step.kind === 'diff');
  const visible = showActions ? normalized : compact
    ? [...summaries, ...defaultVisible.filter(step => !summaries.includes(step) && ['FAIL', 'ERROR', 'BLOCKED'].includes(step.status))]
    : defaultVisible;
  const displayed = visible.length ? visible : actions;
  const currentPage = Math.min(page, Math.max(1, Math.ceil(displayed.length / pageSize)));
  const offset = (currentPage - 1) * pageSize;
  const paged = displayed.slice(offset, offset + pageSize);
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
    <div className="cman-report-toolbar"><strong>{compact && !showActions ? `场景汇总 · ${summaries.length} 项（完整业务验证 ${checks.length} 项）` : `业务验证 · ${checks.length} 项（执行动作 ${actions.length}，配置比对 ${diffs.length}）`}</strong><Button size="small" onClick={() => { setShowActions(!showActions); setPage(1); }}>{showActions ? '只看业务验证' : `查看完整步骤与协议证据（${normalized.length} 项）`}</Button></div>
    {!checks.length && <Alert type="warning" showIcon message="本次报告没有记录业务断言" description="SQL 或命令执行成功只能证明操作完成，不能证明用例目标已经满足。执行动作见下方；排查记录可从运行时日志和完整报告查看。" />}
    {pager && <div className="cman-report-pagination">{pager}</div>}
    {Array.from(groups, ([label, items]) => {
      const content = items.map(step => <section className="cman-evidence-card" key={step.key}>
      <header><div><span className="cman-step-number">{offset + paged.indexOf(step) + 1}</span><strong>{label ? step.cleanTitle.slice(label.length + 1) : step.cleanTitle}</strong><span className="cman-step-kind">{step.kind === 'action' ? '执行动作' : step.kind === 'diff' ? '配置比对' : '结果验证'}</span></div><Tag color={step.status === 'PASS' ? 'success' : step.status === 'FAIL' ? 'error' : 'default'}>{step.status}</Tag></header>
      {step.exampleCode && <div className="cman-evidence-code"><label>关键代码（节选）</label><pre>{step.exampleCode}</pre></div>}
      {compact && !showActions && step.processSteps.length > 0 ? <div className="cman-business-summary">
        {step.expected && <><label>测试目的与通过条件</label><p>{step.expected}</p></>}
        {step.reportScope && <><strong>本场景核心配置</strong><p className="cman-business-scope">{Object.entries(step.reportScope).map(([key, value]) => `${key}：${value}`).join(' · ')}</p></>}
        {step.baselineContext && <details><summary>默认值与会话初始状态（本次实测）</summary><pre>{step.baselineContext}</pre></details>}
        {step.outcomeRows.length > 0 && <div className="cman-business-table-wrap"><table className="cman-business-table"><thead><tr>
          <th>操作／客户端</th><th>实际后端</th><th>参数</th><th>预期</th><th>实测</th><th>判定</th>
        </tr></thead><tbody>{step.outcomeRows.map((row, index) => <tr key={index}>
          <td><strong>{row.client}</strong><p>{row.operation}</p><details><summary>执行详情</summary><pre>{row.sql}</pre>{row.evidence && <p>响应附件：{row.evidence}</p>}</details></td>
          <td>{row.host}:{row.port}<br />PID {row.pid}<br />{row.role}</td>
          <td>{row.parameter}</td><td>{row.expected}</td><td>{row.actual}</td>
          <td><Tag color={row.status === 'PASS' ? 'success' : row.status === 'FAIL' ? 'error' : 'default'}>{row.status}</Tag></td>
        </tr>)}</tbody></table></div>}
        <details className="cman-process-details"><summary>完整执行过程与技术证据</summary>
        <div className="cman-process-flow">{step.processSteps.map((check, index) => <section className="cman-process-step" key={index}>
          <header><strong>{index + 1}. {check.operation}</strong><Tag>{check.driver}</Tag></header>
          {check.command && <><label>实际执行命令／SQL</label><pre className="cman-inline-command">{check.command}</pre></>}
          <label>执行前声明的预期</label><pre>{check.expected}</pre>
          <label>实际返回内容</label><pre>{check.actual}</pre>
          <div className="cman-inline-analysis"><strong>分析与判定</strong><p>{check.analysis}</p>
            <Tag color={check.status === 'PASS' ? 'success' : check.status === 'FAIL' ? 'error' : 'default'}>{check.status}</Tag></div>
          {check.evidence && <details><summary>原始响应附件</summary><p>{check.evidence}</p></details>}
        </section>)}</div></details>
      </div> : <div className="cman-evidence-comparison"><div>{step.command && !step.exampleCode && <><label>执行内容</label><pre className="cman-actual-output cman-inline-command">{step.command}</pre></>}{step.expected && <><label>期望结果</label><p>{step.expected}</p></>}<label>实际结果</label>{step.actualPayload?.type === 'diff' ? <pre className="cman-actual-output cman-diff-output">{(step.actualPayload.raw || '').split('\n').map((line, i) => <span key={i} className={line.startsWith('+') && !line.startsWith('+++') ? 'diff-add' : line.startsWith('-') && !line.startsWith('---') ? 'diff-remove' : line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++') ? 'diff-header' : ''}>{line}{'\n'}</span>)}</pre> : <pre className="cman-actual-output">{step.actualSummary || step.actualPayload?.raw || '本次未记录实际结果'}</pre>}{step.analysis && <div className="cman-inline-analysis"><strong>分析与结论</strong><p>{step.analysis}</p></div>}</div></div>}
      {step.evidenceItems.filter(item => item.type !== 'artifact').map((item, evidenceIndex) => <details key={evidenceIndex}><summary>{item.label}</summary><pre>{item.value}</pre></details>)}
    </section>);
      return label ? <details className="cman-scenario-group" key={label}
        open={items.some(step => ['FAIL', 'ERROR', 'BLOCKED'].includes(step.status))}>
        <summary>{label}<span>{items.length} 项 · {items.filter(step => step.status === 'PASS').length} 通过 · {items.filter(step => step.status === 'SKIP').length} 跳过{items.some(step => ['FAIL', 'ERROR', 'BLOCKED'].includes(step.status)) ? ' · 有未通过检查' : ''}</span></summary>
        {content}
      </details> : <div key={label}>{content}</div>;
    })}
    {pager && <div className="cman-report-pagination">{pager}</div>}
  </div>;
}
