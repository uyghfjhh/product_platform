import { useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Popconfirm, Select, Space, Tag, Typography } from 'antd';

import AsanDiagnostics from '../components/AsanDiagnostics';
import LiveMemoryDiagnostics from '../components/LiveMemoryDiagnostics';
import { api, statusColor, type Environment, type Task } from '../platform/api';

type MemoryReport = {
  available: boolean;
  samples: number;
  snapshots: number;
  rss_change_mib: number | null;
  issue: string | null;
  files: { name: string; title: string }[];
};

const activeStatuses = ['QUEUED', 'RUNNING', 'CANCELLING'];
const isMemoryTask = (task: Task) => task.action === 'stability.fbasecman'
  && task.target === 'upstream-7x24' && (task.parameters.memory_profile === true || Boolean(task.parameters.asan_build_id));

export default function MemoryDiagnosticsPage({ environments, openTask }: {
  environments: Environment[];
  openTask: (id: string) => void;
}) {
  const [selected, setSelected] = useState(() => new URLSearchParams(window.location.search).get('memory_task'));
  const [current, setCurrent] = useState<Task>();
  const [report, setReport] = useState<MemoryReport>();
  const [error, setError] = useState('');
  const [reportError, setReportError] = useState('');
  const [recent, setRecent] = useState<Task[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState('');
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void api<{ tasks: Task[]; next: string | null }>('/fbasecman-memory-tasks', { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) {
          setRecent(data.tasks);
          setCursor(data.next);
        }
      })
      .catch((cause: Error) => { if (!controller.signal.aborted) setHistoryError(cause.message); });
    return () => controller.abort();
  }, []);

  async function loadMore() {
    if (!cursor) return;
    try {
      const result = await api<{ tasks: Task[]; next: string | null }>(
        `/fbasecman-memory-tasks?before=${encodeURIComponent(cursor)}`,
      );
      setRecent((items) => [...items, ...result.tasks.filter((next) => !items.some((item) => item.id === next.id))]);
      setCursor(result.next);
      setHistoryError('');
    } catch (cause) {
      setHistoryError((cause as Error).message);
    }
  }

  const taskId = selected || recent[0]?.id;
  const task = current?.id === taskId ? current : recent.find((item) => item.id === taskId);
  const running = Boolean(task && activeStatuses.includes(task.status));

  useEffect(() => {
    if (!taskId) return;
    const controller = new AbortController();
    let timer: number | undefined;
    async function poll() {
      try {
        const task = await api<Task>(`/operations/${encodeURIComponent(taskId!)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        if (!isMemoryTask(task)) throw new Error('这项任务未开启内存诊断');
        setCurrent(task);
        setError('');
        if (activeStatuses.includes(task.status)) timer = window.setTimeout(poll, 3500);
      } catch (cause) {
        if (!controller.signal.aborted) setError((cause as Error).message);
      }
    }
    setError('');
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [taskId]);

  const endpoint = task ? `/environments/${encodeURIComponent(task.environment_id)}/fbasecman-longrun/${task.id}` : '';
  useEffect(() => {
    setReport(undefined);
    setReportError('');
    if (!endpoint || running) return;
    const controller = new AbortController();
    void api<MemoryReport>(endpoint + '/memory', { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setReport(data); })
      .catch((cause: Error) => { if (!controller.signal.aborted) setReportError(cause.message); });
    return () => controller.abort();
  }, [endpoint, running]);

  async function deleteReport() {
    if (!task || running || deleting) return;
    setDeleting(true);
    try {
      await api(endpoint + '/memory', { method: 'DELETE' });
      setRecent((items) => items.filter((item) => item.id !== task.id));
      setCurrent(undefined);
      setSelected(null);
      const url = new URL(window.location.href);
      url.searchParams.delete('memory_task');
      window.history.replaceState(null, '', url);
    } catch (cause) {
      setReportError((cause as Error).message);
    } finally {
      setDeleting(false);
    }
  }

  const options = task && !recent.some((item) => item.id === task.id) ? [task, ...recent] : recent;
  return <div className="workload-page">
    <header className="workload-page-heading">
      <Typography.Title level={3} style={{ margin: 0 }}>fbasecman 内存诊断</Typography.Title>
      <Button href={`?page=stability%3Afbasecman${task ? '&env=' + encodeURIComponent(task.environment_id) : ''}`}>返回稳定性测试</Button>
    </header>
    <Space wrap>
      {(options.length > 0 || selected) && <Select aria-label="选择常稳诊断任务" placeholder="选择常稳诊断任务"
        value={taskId} style={{ minWidth: 260 }} disabled={deleting}
        options={options.map((item) => ({ value: item.id,
          label: `${environments.find((env) => env.id === item.environment_id)?.title ?? item.environment_id} · ${new Date(item.created_at).toLocaleString('zh-CN')}`,
        }))}
        onChange={(id) => {
          setSelected(id);
          const url = new URL(window.location.href);
          url.searchParams.set('memory_task', id);
          window.history.replaceState(null, '', url);
        }} />}
      {cursor && <Button onClick={() => void loadMore()} disabled={deleting}>加载更多任务</Button>}
      {task && <><Tag color={statusColor(task.status)}>{task.status}</Tag>
        <Button type="link" onClick={() => openTask(task.id)}>任务详情</Button>
        <Popconfirm title="删除这项内存诊断？" description="会移除本次内存诊断的采样、报告和分析结果。常稳任务及执行日志保留。"
          okText="删除" cancelText="取消" okButtonProps={{ danger: true }} disabled={running || deleting}
          onConfirm={deleteReport}>
          <Button danger disabled={running || deleting} loading={deleting}>删除诊断</Button>
        </Popconfirm></>}
    </Space>
    {(error || reportError || historyError) && <Alert type="warning" showIcon message={error || reportError || historyError} />}
    {!taskId && <Empty description="暂无开启内存诊断的常稳任务" />}
    {task && (task.parameters.asan_build_id
      ? <AsanDiagnostics key={task.id} endpoint={endpoint} running={running} />
      : <LiveMemoryDiagnostics key={task.id} endpoint={endpoint} running={running} />)}
    {report?.available && <Card style={{ marginTop: 16 }}>
      <details>
        <summary>归档证据和原始采样</summary>
        <Space wrap style={{ marginTop: 12 }}>
          <Typography.Text>RSS 采样 {report.samples} 次 · 保留 heap 快照 {report.snapshots} 份</Typography.Text>
          {report.rss_change_mib !== null && <Typography.Text>RSS 变化 {report.rss_change_mib} MiB</Typography.Text>}
          {report.files.map(({ name, title }) => <a key={name} href={`/api/v1${endpoint}/memory/${name}`}
            target="_blank" rel="noreferrer">{title}</a>)}
        </Space>
        {report.issue && <Alert type="warning" showIcon message={report.issue} />}
      </details>
    </Card>}
  </div>;
}
