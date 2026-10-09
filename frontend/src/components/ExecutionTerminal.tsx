import { useEffect, useRef, useState } from 'react';
import { Button, Progress, Tag } from 'antd';
import { CloseOutlined, DownOutlined, UpOutlined } from '@ant-design/icons';
import { api, post, type Event, type Task } from '../platform/api';

const finished = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED', 'RECOVERY_REQUIRED']);

/** 平台通用执行终端：任务日志流 + 头部常驻进度条 + 折叠瘦身 + 取消与关闭，产品无关 */
export default function ExecutionTerminal({ taskId, onInspect, onFinished, onClose }: {
  taskId: string | null;
  onInspect: (taskId: string) => void;
  onFinished: () => void;
  onClose?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [dismissedId, setDismissedId] = useState<string | null>(null);
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
    setDismissedId(null);
    setExpanded(false); // 默认收起为 38px 底部常驻状态栏，保证左侧拓扑画布完整展示，不遮挡下层节点
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

  if (!taskId || dismissedId === taskId) return null;
  const running = !task || !finished.has(task.status);

  const handleClose = () => {
    setDismissedId(taskId);
    onClose?.();
  };

  return (
    <aside className={`regression-terminal${expanded ? ' expanded' : ' collapsed'}`} aria-label="执行终端日志">
      <div className="regression-terminal-header" onClick={() => setExpanded(!expanded)}>
        <span className={running ? 'terminal-live-dot' : 'terminal-live-dot stopped'} />
        <strong className="terminal-title">
          {running ? '正在执行' : task?.status === 'SUCCEEDED' ? '执行完成 (SUCCESS)' : '执行结束'}：{task?.target || '准备中'}
        </strong>
        {task && (
          <Tag color={running ? 'processing' : task.status === 'SUCCEEDED' ? 'success' : 'error'}>
            {task.status}
          </Tag>
        )}

        {/* 头部常驻进度条：类似测试套件执行进度条，收起状态下依然实时可见 */}
        {task?.progress && (
          <div
            className="regression-terminal-header-progress"
            onClick={(event) => event.stopPropagation()}
            title={`任务进度: ${task.progress.done}/${task.progress.total} - ${task.progress.label}`}
          >
            <Progress
              percent={Math.round((task.progress.done / task.progress.total) * 100)}
              size="small"
              status={running ? 'active' : (task.status === 'SUCCEEDED' ? 'success' : 'exception')}
              format={() => `${task.progress!.done}/${task.progress!.total}`}
            />
            <span className="regression-terminal-progress-label">{task.progress.label}</span>
          </div>
        )}

        <div className="regression-terminal-actions" onClick={(event) => event.stopPropagation()}>
          {expanded && (
            <>
              <Button size="small" onClick={() => setFollow(!follow)}>{follow ? '跟随日志' : '继续跟随'}</Button>
              <Button size="small" onClick={() => navigator.clipboard.writeText(lines.join('\n'))}>复制</Button>
              <Button size="small" onClick={() => { setLines([]); setFollow(true); }}>清屏</Button>
            </>
          )}
          <Button size="small" onClick={() => onInspect(taskId)}>任务详情</Button>
          {task && running && (
            <Button
              size="small"
              danger
              onClick={() => void post(`/operations/${taskId}/cancel`, {}).catch((cause) => setError((cause as Error).message))}
            >
              停止
            </Button>
          )}
          <Button
            size="small"
            icon={expanded ? <DownOutlined /> : <UpOutlined />}
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? '收起日志' : '展开日志'}
          </Button>
          <Button
            size="small"
            type="text"
            icon={<CloseOutlined />}
            onClick={handleClose}
            title="关闭状态条"
            style={{ color: '#94a3b8' }}
          />
        </div>
      </div>

      {expanded && (
        <div className="regression-terminal-body" ref={body}>
          {error && <div className="terminal-error">{error}</div>}
          <pre>{lines.length ? lines.join('\n') : task && finished.has(task.status) ? (task.reason || `任务已结束（${task.status}），未产生进程日志。`) : '等待任务输出...'}</pre>
        </div>
      )}
    </aside>
  );
}
