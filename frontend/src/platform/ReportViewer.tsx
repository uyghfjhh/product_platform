import { useEffect, useState } from 'react';
import { Alert, Button, Empty, Modal, Space, Spin, Tabs, Tag, Typography } from 'antd';
import { api, statusColor } from './api';

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
  const path = environmentId && target
    ? `/environments/${encodeURIComponent(environmentId)}/results/${encodeURIComponent(target)}` : '';

  useEffect(() => {
    let active = true;
    setReport(null);
    setError('');
    setLoading(false);
    if (!path) return;
    setLoading(true);
    void api<Report>(path + '/report')
      .then((value) => { if (active) setReport(value); })
      .catch((cause: Error) => { if (active) setError(cause.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [path]);

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
        { key: 'raw', label: '原始报告', children: report.report !== null
          ? <pre className="raw-report">{report.report}</pre> : <Empty description="本次执行没有生成原始报告" /> },
      ]} />
    </>}
  </Modal>;
}
