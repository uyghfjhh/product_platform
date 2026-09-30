import { useEffect, useState } from 'react';
import { Alert, Button, Drawer, Empty, Space, Spin, Tag, Typography } from 'antd';

import { api, operationRequest, type Task } from '../platform/api';

type Finding = { text: string; evidence_ids: string[] };
type Candidate = { revision: string; reason: string; evidence_ids: string[] };
type Analysis = {
  summary: string;
  failure_category: string;
  facts: Finding[];
  code_logic: Finding[];
  likely_causes: Finding[];
  candidate_commits: Candidate[];
  solutions: Finding[];
  unknowns: string[];
};
type Evidence = {
  id: string;
  kind: string;
  path?: string | null;
  line_start?: number;
  line_end?: number;
  content: string;
};
type Diagnosis = {
  stale: boolean;
  model: string;
  created_at: string;
  content: {
    analysis: Analysis;
    evidence: Evidence[];
    source_checkout_revision?: string | null;
    running_binary_revision?: string | null;
    commit_attribution: string;
  };
};

function Findings({ title, values }: { title: string; values: Finding[] }) {
  return <section className="diagnosis-section">
    <Typography.Title level={5}>{title}</Typography.Title>
    {values.length ? values.map((item, index) => <div className="diagnosis-finding" key={index}>
      <p>{item.text}</p>
      <div>{item.evidence_ids.map((id) => <a key={id} href={`#evidence-${id}`}><Tag>{id}</Tag></a>)}</div>
    </div>) : <Typography.Text type="secondary">本次没有足够证据。</Typography.Text>}
  </section>;
}

export default function DiagnosisDrawer({ target, environmentId, onClose, openTask }: {
  target: string | null;
  environmentId?: string;
  onClose: () => void;
  openTask: (taskId: string) => void;
}) {
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [configured, setConfigured] = useState(false);
  const [pendingTaskId, setPendingTaskId] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!target || !environmentId) return;
    let cancelled = false;
    setDiagnosis(null);
    setPendingTaskId(null);
    setError('');
    void api<{ configured: boolean }>('/diagnostics/availability')
      .then((value) => { if (!cancelled) setConfigured(value.configured); })
      .catch(() => { if (!cancelled) setConfigured(false); });
    void api<Diagnosis>(`/environments/${encodeURIComponent(environmentId)}/diagnostics/${encodeURIComponent(target)}`)
      .then((value) => { if (!cancelled) setDiagnosis(value); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [target, environmentId]);

  useEffect(() => {
    if (!pendingTaskId || !target || !environmentId) return;
    let stopped = false;
    const timer = window.setInterval(() => {
      void api<Task>(`/operations/${pendingTaskId}`).then(async (task) => {
        if (stopped || !['SUCCEEDED', 'FAILED', 'CANCELLED', 'RECOVERY_REQUIRED'].includes(task.status)) return;
        window.clearInterval(timer);
        setPendingTaskId(null);
        if (task.status !== 'SUCCEEDED') {
          setError(task.reason || 'AI 诊断失败，请查看任务日志');
          return;
        }
        const value = await api<Diagnosis>(`/environments/${encodeURIComponent(environmentId)}/diagnostics/${encodeURIComponent(target)}`);
        if (!stopped) setDiagnosis(value);
      }).catch((cause) => { if (!stopped) setError((cause as Error).message); });
    }, 1800);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [pendingTaskId, target, environmentId]);

  async function start() {
    if (!target || !environmentId) return;
    setError('');
    try {
      const task = await operationRequest(environmentId, 'diagnostics.analyze', target);
      setPendingTaskId(task.id);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  const analysis = diagnosis?.content.analysis;
  return <Drawer title={target ? `AI 诊断 · ${target}` : 'AI 诊断'} open={!!target} onClose={onClose}
    width="min(760px, 96vw)" destroyOnHidden>
    {!environmentId ? <Empty description="请先选择对应产品的测试环境" /> : <>
      <Space wrap style={{ marginBottom: 16 }}>
        <Button type="primary" disabled={!configured || !!pendingTaskId} onClick={() => void start()}>
          {diagnosis ? '重新分析当前结果' : '分析当前结果'}
        </Button>
        {pendingTaskId && <Button onClick={() => openTask(pendingTaskId)}>查看分析任务</Button>}
      </Space>
      {!configured && <Alert type="info" showIcon message="AI 服务尚未配置" description="在服务端设置 OPENAI_API_KEY 和可选的 PRODUCT_PLATFORM_AI_MODEL 后可分析。" />}
      {pendingTaskId && <div className="diagnosis-pending"><Spin /> 正在收集证据、检索代码并生成诊断…</div>}
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} />}
      {diagnosis?.stale && <Alert type="warning" showIcon message="测试结果已经变化；以下诊断对应旧结果" style={{ margin: '12px 0' }} />}
      {!analysis && !pendingTaskId && <Empty description="该结果尚无 AI 诊断" />}
      {analysis && <>
        <div className="diagnosis-summary">
          <Tag>{analysis.failure_category}</Tag>
          <strong>{analysis.summary}</strong>
          <small>模型：{diagnosis.model} · 生成时间：{new Date(diagnosis.created_at).toLocaleString('zh-CN')}</small>
        </div>
        <Findings title="确认的事实" values={analysis.facts} />
        <Findings title="相关代码逻辑" values={analysis.code_logic} />
        <Findings title="可能原因" values={analysis.likely_causes} />
        <Findings title="建议的验证与修复" values={analysis.solutions} />
        <section className="diagnosis-section">
          <Typography.Title level={5}>候选提交</Typography.Title>
          <Typography.Text type="secondary">这些提交仅由源码检索和 Git 历史提示；尚未通过版本二分确认。</Typography.Text>
          {analysis.candidate_commits.map((item) => <div className="diagnosis-finding" key={item.revision}>
            <code>{item.revision}</code><p>{item.reason}</p>
            {item.evidence_ids.map((id) => <a key={id} href={`#evidence-${id}`}><Tag>{id}</Tag></a>)}
          </div>)}
        </section>
        {analysis.unknowns.length > 0 && <section className="diagnosis-section">
          <Typography.Title level={5}>仍需确认</Typography.Title>
          {analysis.unknowns.map((item, index) => <p key={index}>{item}</p>)}
        </section>}
        <section className="diagnosis-section">
          <Typography.Title level={5}>原始证据</Typography.Title>
          {diagnosis.content.evidence.map((item) => <details id={`evidence-${item.id}`} key={item.id} className="diagnosis-evidence">
            <summary>{item.id} · {item.path || item.kind}{item.line_start ? `:${item.line_start}` : ''}</summary>
            <pre>{item.content}</pre>
          </details>)}
        </section>
      </>}
    </>}
  </Drawer>;
}
