import { useEffect, useRef, useState } from 'react';
import { Button, Tag } from 'antd';
import { api, post, type Event, type Task } from '../../api';

const finished = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED', 'RECOVERY_REQUIRED']);

export default function RegressionTerminal({ taskId, onInspect, onFinished }: {
  taskId: string | null;
  onInspect: (taskId: string) => void;
  onFinished: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [task, setTask] = useState<Task | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [follow, setFollow] = useState(true);
  const [error, setError] = useState('');
  const body = useRef<HTMLDivElement>(null);
  const reported = useRef<string | null>(null);
  const sequence = useRef(0);
  const finishedCallback = useRef(onFinished);
  finishedCallback.current = onFinished;

  useEffect(() => {
    if (!taskId) return;
    setExpanded(true);
    setTask(null);
    setLines([]);
    setError('');
    reported.current = null;
    sequence.current = 0;
    let closed = false;
    let timer: number | undefined;
    async function refresh() {
      try {
        const [current, events] = await Promise.all([
          api<Task>(`/operations/${taskId}`),
          api<Event[]>(`/operations/${taskId}/events?after=${sequence.current}`),
        ]);
        if (closed) return;
        setTask(current);
        if (events.length) {
          sequence.current = events.at(-1)!.sequence;
          const output = events.filter((item) => item.event_type === 'command.output')
            .map((item) => String(item.payload.line || ''));
          if (output.length) setLines((previous) => [...previous, ...output]);
        }
        setError('');
        if (finished.has(current.status)) {
          if (reported.current !== taskId) { reported.current = taskId; finishedCallback.current(); }
        } else timer = window.setTimeout(refresh, 1000);
      } catch (cause) {
        if (!closed) {
          setError((cause as Error).message);
          timer = window.setTimeout(refresh, 3000);
        }
      }
    }
    void refresh();
    return () => { closed = true; window.clearTimeout(timer); };
  }, [taskId]);

  useEffect(() => {
    if (follow && body.current) body.current.scrollTop = body.current.scrollHeight;
  }, [lines, expanded, follow]);

  if (!taskId) return null;
  const running = !task || !finished.has(task.status);
  return <aside className={`regression-terminal${expanded ? ' expanded' : ''}`} aria-label="执行终端日志">
    <div className="regression-terminal-header" onClick={() => setExpanded(!expanded)}>
      <span className={running ? 'terminal-live-dot' : 'terminal-live-dot stopped'} />
      <strong>{running ? '正在执行' : task?.status === 'SUCCEEDED' ? '执行完成 (SUCCESS)' : '执行结束'}：{task?.target || '准备中'}</strong>
      {task && <Tag>{task.status}</Tag>}
      <div className="regression-terminal-actions" onClick={(event) => event.stopPropagation()}>
        <Button size="small" onClick={() => setFollow(!follow)}>{follow ? '跟随日志' : '继续跟随'}</Button>
        <Button size="small" onClick={() => navigator.clipboard.writeText(lines.join('\n'))}>复制</Button>
        <Button size="small" onClick={() => { setLines([]); setFollow(true); }}>清屏</Button>
        <Button size="small" onClick={() => onInspect(taskId)}>详情</Button>
        {task && running && <Button size="small" danger onClick={() => void post(`/operations/${taskId}/cancel`, {}).catch((cause) => setError((cause as Error).message))}>停止</Button>}
        <Button size="small" onClick={() => setExpanded(!expanded)}>{expanded ? '收起' : '展开'}</Button>
      </div>
    </div>
    {expanded && <div className="regression-terminal-body" ref={body}>
      {error && <div className="terminal-error">{error}</div>}
      <pre>{lines.length ? lines.join('\n') : '等待测试任务输出...'}</pre>
    </div>}
  </aside>;
}
