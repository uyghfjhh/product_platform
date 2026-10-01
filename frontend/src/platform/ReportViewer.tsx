import { useEffect, useState } from 'react';
import { Alert, Button, Empty, Modal, Space, Spin, Tabs, Tag, Typography } from 'antd';
import { PauseOutlined, PlayCircleOutlined, StepBackwardOutlined, StepForwardOutlined } from '@ant-design/icons';
import { api, statusColor, type Event } from './api';
import SceneReplay from '../components/SceneReplay';

type Step = {
  order?: number; title?: string; status?: string; result?: string;
  expected?: unknown; actual?: unknown; execution?: unknown; evidence?: unknown;
};
type Report = {
  target: string; execution_id?: string; verdict: string; reason?: string;
  duration_seconds?: number; cleanup?: { status?: string; reason?: string };
  steps: Step[]; report: string | null; warning?: string;
};
const display = (value: unknown): string => typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? '';

export default function ReportViewer({ target, environmentId, onClose }: {
  target: string | null; environmentId?: string; onClose: () => void;
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
      {report.reason && <Alert type={report.verdict === 'PASS' ? 'info' : 'error'} message={report.reason} style={{ marginBottom: 12 }} />}
      {report.cleanup?.reason && <Alert type="warning" message={`清理 ${report.cleanup.status}: ${report.cleanup.reason}`} style={{ marginBottom: 12 }} />}
      {report.warning && <Alert type="warning" message={report.warning} />}
      <Tabs items={[
        { key: 'steps', label: `执行步骤 (${report.steps.length})`, children: report.steps.length
          ? <div className="report-steps">{report.steps.map((step, index) => {
            const verdict = step.result || step.status || 'UNKNOWN';
            return <div key={index} className={`report-step ${verdict.toLowerCase()}`}>
              <div className="report-step-header"><strong>{step.order ?? index + 1}. {step.title || '执行步骤'}</strong>
                <Tag color={statusColor(verdict)}>{verdict}</Tag></div>
              {([['预期结果', step.expected], ['实际结果', step.actual], ['执行过程', step.execution], ['证据', step.evidence]] as const)
                .filter(([, value]) => value != null && value !== '' && !(Array.isArray(value) && value.length === 0))
                .map(([label, value]) => <div key={label}><Typography.Text type="secondary">{label}</Typography.Text>
                  <pre>{display(value)}</pre></div>)}
            </div>;
          })}</div> : <Empty description="本次执行没有记录步骤，可查看执行结论及原始报告" /> },
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
