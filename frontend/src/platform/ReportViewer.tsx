import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Collapse, Empty, Modal, Space, Spin, Tabs, Tag, Typography } from 'antd';
import { PauseOutlined, PlayCircleOutlined, StepBackwardOutlined, StepForwardOutlined } from '@ant-design/icons';
import { api, statusColor, type Event } from './api';
import SceneReplay from '../components/SceneReplay';
import { StepList } from './StepList';
import { normalizeStep } from './stepNormalizer';

type Step = {
  order?: number; title?: string; status?: string; result?: string;
  intent?: string; expected?: unknown; actual?: unknown; execution?: unknown; evidence?: unknown;
};
type Report = {
  target: string; execution_id?: string; verdict: string; reason?: string;
  duration_seconds?: number; cleanup?: { status?: string; reason?: string };
  case_description?: {purpose?:string;prerequisites?:string[];pass_criteria?:{title:string;expected:unknown;step?:number}[];final_state?:string;scope_note?:string};
  steps: Step[]; report: string | null; warning?: string;
};

export default function ReportViewer({ target, environmentId, onClose, caseInfo }: {
  target: string | null; environmentId?: string; onClose: () => void;
  caseInfo?: { title?: string | null; summary?: string | null; name?: string | null; core_id?: string | null };
}) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [sceneEvents, setSceneEvents] = useState<Event[]>([]);
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const path = environmentId && target
    ? `/environments/${encodeURIComponent(environmentId)}/results/${encodeURIComponent(target)}` : '';

  useEffect(() => {
    let active = true;
    setReport(null);
    setError('');
    setLoading(false);
    setSceneEvents([]);
    setPosition(0);
    setPlaying(false);
    if (!path) return;
    setLoading(true);
    void api<Report>(path + '/report')
      .then((value) => { if (active) setReport(value); })
      .catch((cause: Error) => { if (active) setError(cause.message); })
      .finally(() => { if (active) setLoading(false); });
    void api<{ events: Array<Omit<Event, 'task_id' | 'sequence' | 'recorded_at'>> }>(path + '/scene')
      .then((value) => {
        if (!active) return;
        setSceneEvents(value.events.map((item, index) => ({
          task_id: '', sequence: index, recorded_at: '',
          event_type: item.event_type, payload: item.payload,
        })));
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [path]);

  const hasObservations = sceneEvents.some((item) => item.event_type === 'scene.entity.observed');

  useEffect(() => {
    if (!playing || !sceneEvents.length) return;
    if (position >= sceneEvents.length - 1) { setPlaying(false); return; }
    const timer = window.setTimeout(() => setPosition((value) => value + 1), 1100);
    return () => window.clearTimeout(timer);
  }, [playing, position, sceneEvents.length]);

  useEffect(() => {
    if (sceneEvents.length) setPosition(sceneEvents.length - 1);
  }, [sceneEvents.length]);

  const brief = useMemo(() => {
    if (!report?.steps?.length) return null;
    const normalized = report.steps.map((step, index) => normalizeStep(step, index));
    const purpose=report.case_description?.purpose || (caseInfo?.summary!==caseInfo?.title ? caseInfo?.summary : '');
    return {
      steps:normalized,
      purpose,
      currentPurpose:!report.case_description?.purpose && !!purpose,
      caseTitle:caseInfo?.title || caseInfo?.name || target,
    };
  }, [report, caseInfo]);

  return <Modal title={`测试报告 · ${target || ''}`} open={Boolean(target)} onCancel={onClose}
    footer={null} width={1000}>
    {loading && <Spin />}
    {error && <Alert type="error" message={error} />}
    {report && <>
      <Space wrap style={{ marginBottom: 16 }}>
        <Tag color={statusColor(report.verdict)}>{report.verdict}</Tag>
        {report.duration_seconds != null && <Typography.Text>耗时 {report.duration_seconds.toFixed(2)} 秒</Typography.Text>}
        {report.report !== null && <Button href={`/api/v1${path}/report.txt`}>下载原始报告</Button>}
        <Button href={`/api/v1${path}/bundle`}>下载分析包</Button>
      </Space>
      {brief && <div className="case-brief">
        <Typography.Title level={5} style={{marginTop:0}}>测试目的</Typography.Title>
        <Typography.Paragraph>{brief.purpose || '本次归档未保存独立测试目的；以下显示当次实际步骤与声明期望。'}</Typography.Paragraph>
        {brief.currentPurpose && <Typography.Paragraph type="secondary">以上是当前用例目录说明，不作为历史执行时的声明。</Typography.Paragraph>}
        {report.case_description?.final_state && <Alert type="info" title="预期最终状态" description={report.case_description.final_state} />}
        {report.case_description?.scope_note && <Alert type="warning" title={report.case_description.scope_note} />}
        <Collapse style={{marginTop:12}} items={[
          ...(report.case_description?.prerequisites?.length ? [{key:'prerequisites',label:'前置条件',children:<ol>{report.case_description.prerequisites.map((p,i)=><li key={i}>{p}</li>)}</ol>}] : []),
          {key:'flow',label:`操作步骤与逐步预期（${brief.steps.length}）`,children:<div style={{overflowX:'auto'}}><table className="case-description-table"><thead><tr><th>步骤</th><th>操作／检查内容</th><th>预期结果</th><th>实际结论</th></tr></thead><tbody>{brief.steps.map((s,i)=><tr key={i}><td>{i+1}</td><td>{s.cleanTitle || s.title}</td><td>{s.expected || '当次未保存期望'}</td><td><Tag color={statusColor(s.status)}>{s.status}</Tag>{s.status !== 'PASS' && <div style={{whiteSpace:'pre-wrap',marginTop:8}}>{s.actualSummary || s.analysis || (s.status === 'BLOCKED' ? '未执行：前序步骤失败' : '当次未保存实际结果')}</div>}</td></tr>)}</tbody></table></div>},
          ...(report.case_description?.pass_criteria?.length ? [{key:'criteria',label:'通过标准（执行前声明）',children:<ol>{report.case_description.pass_criteria.map((p,i)=><li key={i}><strong>{p.title}</strong>：{typeof p.expected==='string'?p.expected:JSON.stringify(p.expected)}</li>)}</ol>}] : []),
        ]} />
      </div>}
      {report.reason && <Alert type={report.verdict === 'PASS' ? 'info' : 'error'} message={report.reason} style={{ marginBottom: 12 }} />}
      {report.cleanup?.reason && <Alert type="warning" message={`清理 ${report.cleanup.status}: ${report.cleanup.reason}`} style={{ marginBottom: 12 }} />}
      {report.warning && <Alert type="warning" message={report.warning} />}
      <Tabs items={[
        { key: 'steps', label: `执行步骤 (${report.steps.length})`, children: <StepList steps={report.steps} evidenceBasePath={path + '/evidence'} /> },
        ...(hasObservations ? [{
          key: 'scene', label: '场景回放', children: <>
            <SceneReplay events={sceneEvents} position={position} />
            <div className="replay-controls">
              <Button icon={<StepBackwardOutlined />} disabled={position <= 0}
                onClick={() => { setPlaying(false); setPosition((value) => Math.max(0, value - 1)); }}
                aria-label="上一事件" />
              <Button type="primary" icon={playing ? <PauseOutlined /> : <PlayCircleOutlined />}
                onClick={() => { if (position >= sceneEvents.length - 1) setPosition(0); setPlaying(!playing); }}>
                {playing ? '暂停' : '播放'}
              </Button>
              <Button icon={<StepForwardOutlined />} disabled={position >= sceneEvents.length - 1}
                onClick={() => { setPlaying(false); setPosition((value) => Math.min(sceneEvents.length - 1, value + 1)); }}
                aria-label="下一事件" />
              <Button onClick={() => { setPlaying(false); setPosition(sceneEvents.length - 1); }}>最新</Button>
              <Typography.Text type="secondary">{position + 1} / {sceneEvents.length}</Typography.Text>
            </div>
          </>,
        }] : []),
        { key: 'raw', label: '原始报告', children: report.report !== null
          ? <pre className="raw-report">{report.report}</pre> : <Empty description="本次执行没有生成原始报告" /> },
      ]} />
    </>}
  </Modal>;
}
