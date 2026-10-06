import { useEffect, useState } from 'react';
import { Alert, App, Button, Form, Input, InputNumber, Select, Space, Statistic, Table, Typography } from 'antd';
import { api, operationRequest, type Environment } from '../platform/api';

type Sample = { elapsed: number; tps: number; latency_ms: number };
type Metrics = { samples: Sample[]; summary: { tps?: number; latency_ms?: number }; status: string; reason?: string; available: boolean };
function Curve({ samples, field, label }: { samples: Sample[]; field: 'tps' | 'latency_ms'; label: string }) {
  if (!samples.length) return <Typography.Paragraph type="secondary">{label}：尚无采样</Typography.Paragraph>;
  const maximum = Math.max(1, ...samples.map((row) => row[field]));
  const points = samples.map((row, i) => `${30 + i * 540 / Math.max(samples.length - 1, 1)},${170 - row[field] * 140 / maximum}`).join(' ');
  return <div><Typography.Text>{label}</Typography.Text><svg role="img" aria-label={label} viewBox="0 0 600 200" style={{ width: '100%', maxWidth: 700 }}><path d="M30 20V170H580" stroke="var(--border-medium)" fill="none" /><polyline points={points} fill="none" stroke="var(--border-active)" strokeWidth="2" /><text x="30" y="195" fill="var(--text-muted)">执行时间（秒）</text><text x="5" y="20" fill="var(--text-muted)">{maximum.toFixed(1)}</text></svg></div>;
}
export default function WorkloadsPage({ environments, environment, onSelectEnvironment, openTask }: { environments: Environment[]; environment?: Environment; onSelectEnvironment?: (id: string) => void; openTask: (id: string) => void }) {
  const { message, modal } = App.useApp();
  const [form] = Form.useForm();
  const driver = Form.useWatch('driver', form);
  const [taskId, setTaskId] = useState('');
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!taskId) return;
    let active = true; let timer: number;
    async function refresh() {
      try {
        const result = await api<Metrics>(`/operations/${taskId}/workload`);
        if (!active) return;
        setMetrics(result);
        if (['QUEUED', 'RUNNING', 'CANCELLING'].includes(result.status)) timer = window.setTimeout(() => void refresh(), 1200);
      } catch (cause) { if (active) setError((cause as Error).message); }
    }
    void refresh(); return () => { active = false; window.clearTimeout(timer); };
  }, [taskId]);
  async function start() {
    const values = await form.validateFields();
    const environment = environments.find((e) => e.id === values.environment_id);
    if (!environment) return;
    modal.confirm({ title: '启动数据库工作负载', content: `${environment.title} · ${values.clients} 个客户端 · ${values.duration_seconds} 秒。将占用数据库连接及执行资源。`, onOk: async () => {
      setBusy(true); setError('');
      try {
        const { environment_id, driver: selected, ...parameters } = values;
        const task = await operationRequest(environment_id, `workload.${selected}`, undefined, parameters, true);
        setTaskId(task.id); setMetrics(null); message.success('工作负载已提交');
      } catch (cause) { setError((cause as Error).message); } finally { setBusy(false); }
    } });
  }
  return <>
    <Typography.Title level={3}>数据库压测</Typography.Title>
    <Alert type="info" message="pgbench 与 JDBC 使用真实客户端负载，采集 TPS 和平均延迟并按阈值判定；不初始化业务数据库。" style={{ marginBottom: 16 }} />
    {error && <Alert type="error" message={error} />}
    <Form form={form} layout="vertical" initialValues={{ environment_id: environments.some((e) => e.id === environment?.id) ? environment?.id : undefined, driver: 'pgbench', preset: 'connectivity', clients: 4, duration_seconds: 30, minimum_tps: 0, max_average_latency_ms: 0, jdbc_jar: '' }}>
      <Form.Item name="environment_id" label="环境" rules={[{ required: true }]}><Select options={environments.map((e) => ({ value: e.id, label: e.title }))} onChange={onSelectEnvironment} /></Form.Item>
      <Space wrap><Form.Item name="driver" label="客户端"><Select style={{ width: 150 }} options={[{ value: 'pgbench', label: 'pgbench' }, { value: 'jdbc', label: 'JDBC' }]} /></Form.Item><Form.Item name="preset" label="工作负载"><Select style={{ width: 180 }} options={[{ value: 'connectivity', label: '连接与轻查询' }, { value: 'catalog', label: '系统目录查询' }]} /></Form.Item><Form.Item name="clients" label="并发客户端"><InputNumber min={1} max={128} /></Form.Item><Form.Item name="duration_seconds" label="时长（秒）"><InputNumber min={1} max={3600} /></Form.Item></Space>
      {driver === 'jdbc' && <Form.Item name="jdbc_jar" label="控制机 JDBC 驱动 jar 路径" rules={[{ required: true }]}><Input /></Form.Item>}
      <Space wrap><Form.Item name="minimum_tps" label="最低 TPS（0 不设阈值）"><InputNumber min={0} /></Form.Item><Form.Item name="max_average_latency_ms" label="最高平均延迟 ms（0 不设阈值）"><InputNumber min={0} /></Form.Item></Space>
      <Button type="primary" loading={busy} onClick={() => void start().catch((cause: Error) => setError(cause.message))}>启动压测</Button>
    </Form>
    {taskId && <Space style={{ margin: '16px 0' }}><Button onClick={() => openTask(taskId)}>任务、取消与原始日志</Button><Typography.Text>{metrics?.status}</Typography.Text></Space>}
    {metrics && <><Space wrap size={40}><Statistic title="汇总 TPS" value={metrics.summary.tps ?? '—'} precision={2} /><Statistic title="平均延迟（ms）" value={metrics.summary.latency_ms ?? '—'} precision={3} /></Space>
      {!metrics.available && <Alert type="info" message="采样尚未产生或运行产物已清理；任务状态仍可查看。" />}
      <Curve samples={metrics.samples} field="tps" label="TPS 实际采样" /><Curve samples={metrics.samples} field="latency_ms" label="平均延迟实际采样（ms）" />
      <Table size="small" rowKey={(_, i) => String(i)} dataSource={metrics.samples} columns={[{ title: '秒', dataIndex: 'elapsed' }, { title: 'TPS', dataIndex: 'tps' }, { title: '平均延迟 ms', dataIndex: 'latency_ms' }]} />
    </>}
  </>;
}
