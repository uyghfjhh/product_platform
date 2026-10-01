import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, App, Button, Drawer, Empty, Progress, Select, Space, Tag, Typography } from 'antd';
import { CloseCircleOutlined, PauseOutlined, PlayCircleOutlined, StepBackwardOutlined, StepForwardOutlined } from '@ant-design/icons';

import { api, post, type Event, type Task, statusColor } from '../platform/api';
import LogViewer from './LogViewer';
import SceneReplay from './SceneReplay';

type Props = { taskId: string | null; onClose: () => void };
type LogResponse = { lines: string[]; available: boolean; path: string };

const terminal = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED', 'RECOVERY_REQUIRED']);

export default function TaskDrawer({ taskId, onClose }: Props) {
  const { message, modal } = App.useApp();
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [log, setLog] = useState<LogResponse | null>(null);
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [follow, setFollow] = useState(true);
  const [speed, setSpeed] = useState(1);
  const sequence = useRef(0);

  useEffect(() => {
    if (!taskId) return;
    let closed = false;
    let timer: number | undefined;
    sequence.current = 0;
    setTask(null);
    setEvents([]);
    setLog(null);
    setPosition(0);
    setPlaying(false);
    setFollow(true);

    async function refresh() {
      if (closed || !taskId) return;
      try {
        const [current, incoming, lines] = await Promise.all([
          api<Task>(`/operations/${taskId}`),
          api<Event[]>(`/operations/${taskId}/events?after=${sequence.current}`),
          api<LogResponse>(`/operations/${taskId}/log?last_lines=500`),
        ]);
        if (closed) return;
        setTask(current);
        setLog(lines);
        if (incoming.length) {
          sequence.current = incoming.at(-1)!.sequence;
          setEvents((previous) => [...previous, ...incoming.filter((item) => item.sequence > (previous.at(-1)?.sequence || 0))]);
        }
        if (!terminal.has(current.status)) timer = window.setTimeout(refresh, 1100);
      } catch (cause) {
        if (!closed) message.error((cause as Error).message);
      }
    }
    void refresh();
    return () => { closed = true; window.clearTimeout(timer); };
  }, [taskId, message]);

  useEffect(() => {
    if (follow && events.length) setPosition(events.length - 1);
  }, [events.length, follow]);

  useEffect(() => {
    if (!playing || events.length < 2) return;
    if (position >= events.length - 1) { setPlaying(false); return; }
    const timer = window.setTimeout(() => setPosition((value) => value + 1), 1100 / speed);
    return () => window.clearTimeout(timer);
  }, [playing, position, events.length, speed]);

  const current = events[position];
  const stepEvents = useMemo(() => events.filter((item) => item.event_type === 'step.started'), [events]);
  // Suite progress: the regression CLI prints `[n/m] suite.case VERDICT t`s per
  // case on stdout; each line arrives as a command.output event.
  const caseProgress = useMemo(() => {
    let done = 0, total = 0, label = '';
    for (const event of events) {
      if (event.event_type !== 'command.output') continue;
      const match = /^\[(\d+)\/(\d+)\]\s+(\S+)/.exec(String(event.payload?.line || ''));
      if (match) { done = Math.max(done, Number(match[1])); total = Number(match[2]); label = match[3]; }
    }
    return total ? { done, total, label } : null;
  }, [events]);
  const selectedStep = current ? stepEvents.filter((item) => item.sequence <= current.sequence).length - 1 : -1;

  function seek(index: number) {
    setPlaying(false);
    setFollow(false);
    setPosition(Math.min(Math.max(index, 0), Math.max(events.length - 1, 0)));
  }

  async function cancel() {
    if (!taskId) return;
    const confirmed = await new Promise<boolean>((resolve) => modal.confirm({
      title: '终止当前操作', content: '将停止正在运行的产品工具并核对结果。',
      okText: '终止', okButtonProps: { danger: true }, onOk: () => resolve(true), onCancel: () => resolve(false),
    }));
    if (!confirmed) return;
    try { await post(`/operations/${taskId}/cancel`, {}); message.info('已请求终止'); }
    catch (cause) { message.error((cause as Error).message); }
  }

  return (
    <Drawer title="操作与证据" open={!!taskId} onClose={onClose} width="min(980px, 96vw)" destroyOnHidden>
      {!task ? <Empty description="正在读取操作状态" /> : <>
        <div className="task-summary">
          <div><Typography.Title level={5}>{task.action}</Typography.Title><Typography.Text type="secondary">{task.environment_id} · {task.target}</Typography.Text></div>
          <Space><Tag color={statusColor(task.status)}>{task.status}</Tag>{!terminal.has(task.status) && <Button danger icon={<CloseCircleOutlined />} onClick={() => void cancel()}>终止操作</Button>}</Space>
        </div>
        {task.reason && <Alert type={task.status === 'SUCCEEDED' ? 'success' : 'warning'} message={task.reason} showIcon />}
        {caseProgress && <div className="task-progress">
          <Progress
            percent={Math.round((caseProgress.done / caseProgress.total) * 100)}
            status={!terminal.has(task.status) ? 'active' : (task.status === 'SUCCEEDED' ? 'success' : 'exception')}
            format={() => `${caseProgress.done}/${caseProgress.total}`}
          />
          <Typography.Text type="secondary">最近完成: {caseProgress.label}</Typography.Text>
        </div>}
        <section className="drawer-section">
          <div className="section-heading"><Typography.Title level={5}>产品状态回放</Typography.Title><Typography.Text type="secondary">{events.length ? `事件 ${position + 1} / ${events.length}` : '等待结构化事件'}</Typography.Text></div>
          <SceneReplay events={events} position={position} />
          {events.length > 0 && <>
            <div className="replay-controls">
              <Button icon={<StepBackwardOutlined />} onClick={() => seek(position - 1)} disabled={position <= 0} aria-label="上一事件" />
              <Button type="primary" icon={playing ? <PauseOutlined /> : <PlayCircleOutlined />} onClick={() => { setFollow(false); if (position >= events.length - 1) setPosition(0); setPlaying(!playing); }}>{playing ? '暂停' : '播放'}</Button>
              <Button icon={<StepForwardOutlined />} onClick={() => seek(position + 1)} disabled={position >= events.length - 1} aria-label="下一事件" />
              <Button onClick={() => { setPlaying(false); setFollow(true); setPosition(events.length - 1); }}>最新</Button>
              <Select value={speed} onChange={setSpeed} options={[1, 2, 4].map((value) => ({ label: `${value}x`, value }))} aria-label="播放速度" />
            </div>
            <div className="timeline-buttons">{stepEvents.map((event, index) => <Button
              key={event.sequence} size="small" type={index === selectedStep ? 'primary' : 'default'}
              onClick={() => seek(events.findIndex((item) => item.sequence === event.sequence))}
            >{index + 1}. {String(event.payload.title || '步骤')}</Button>)}</div>
          </>}
          {current && <div className="event-detail"><Typography.Text type="secondary">{new Date(current.recorded_at).toLocaleString('zh-CN')} · {current.event_type}</Typography.Text><pre>{JSON.stringify(current.payload, null, 2)}</pre></div>}
        </section>
        <section className="drawer-section">
          <div className="section-heading"><Typography.Title level={5}>原始日志</Typography.Title><Typography.Text type="secondary">{log?.available ? log.path : '等待日志文件'}</Typography.Text></div>
          <LogViewer lines={log?.lines || []} filename={log?.available ? log.path : undefined} />
        </section>
      </>}
    </Drawer>
  );
}
