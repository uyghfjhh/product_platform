import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Empty, Input, Modal, Select, Space, Table, Tabs, Typography } from 'antd';

import { api } from '../platform/api';
import './liveMemoryDiagnostics.css';

type Point = [seconds: number, value: number];
type Sample = { id: string; elapsed: number; bytes: number };
type Timeline = { enabled: boolean; rss: Point[]; samples: Sample[]; issue: string | null; measurement_start?: number | null };
type StackRow = { id: string; label: string; callers?: string[]; before: number; after: number; delta: number };
type IgnoredStack = { id: string; label: string; callers?: string[] };
type Stack = StackRow & { frames: string[]; series: Point[]; locations: Record<string, string> };
type Branch = { frame: string; bytes: number; stack?: string; children: Branch[] };
type Comparison = { start?: string; end?: string; rows: StackRow[]; selected: Stack | null; tree: Branch[]; matched: number };

const mib = (value: number) => value / 1048576;
const format = (value: number, signed = false) => mib(value).toLocaleString('zh-CN', {
  maximumFractionDigits: 3, signDisplay: signed ? 'exceptZero' : 'auto',
});
const timeLabel = (seconds: number) => {
  const total = Math.floor(seconds);
  const min = Math.floor(total / 60);
  return min >= 60
    ? `${Math.floor(min / 60)}h ${min % 60}m`
    : `${min}m ${total % 60}s`;
};
const frameName = (frame: string) => frame.replace(/<[0-9a-f]+>$/i, '').replace('[inline]', '（内联）');

async function copyStack(text: string) {
  if (navigator.clipboard) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const field = document.createElement('textarea');
  field.value = text;
  field.style.position = 'fixed';
  field.style.opacity = '0';
  document.body.append(field);
  try {
    field.select();
    if (!document.execCommand('copy')) throw new Error('copy failed');
  } finally {
    field.remove();
  }
}

function readIgnored(key: string): IgnoredStack[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) ?? '[]');
    return Array.isArray(value) ? value.filter((item): item is IgnoredStack =>
      Boolean(item && typeof item.id === 'string' && typeof item.label === 'string'
        && (item.callers == null || (Array.isArray(item.callers) && item.callers.every((name: unknown) => typeof name === 'string'))))) : [];
  } catch {
    return [];
  }
}

function TrendChart({ title, series }: { title: string; series: { label: string; points: Point[] }[] }) {
  const points = series.flatMap((line) => line.points);
  if (!points.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="等待采样" />;
  const values = points.map(([, value]) => value);
  const low = series.length === 1 ? Math.floor(Math.min(...values)) : 0;
  const high = Math.max(low + 1, Math.ceil(Math.max(...values)));
  const first = Math.min(...points.map(([seconds]) => seconds));
  const last = Math.max(...points.map(([seconds]) => seconds));
  const x = (seconds: number) => 48 + 525 * (seconds - first) / Math.max(1, last - first);
  const y = (value: number) => 147 - 125 * (value - low) / (high - low);

  return <figure className="memory-trend">
    <svg viewBox="0 0 592 173" role="img" aria-label={title}>
      <text x="8" y="13">MiB</text>
      {[low, (low + high) / 2, high].map((value) => <g key={value}>
        <line x1="48" y1={y(value)} x2="573" y2={y(value)} className="memory-gridline" />
        <text x="43" y={y(value) + 4} textAnchor="end">{value.toFixed(1)}</text>
      </g>)}
      <text x="48" y="168">{timeLabel(first)}</text>
      <text x="573" y="168" textAnchor="end">{timeLabel(last)}</text>
      {series.map((line, index) => line.points.length === 1
        ? <circle key={line.label} className={`memory-series-${index}`} cx={x(line.points[0][0])} cy={y(line.points[0][1])} r="3" />
        : <polyline key={line.label} className={`memory-series-${index}`} fill="none"
            points={line.points.map(([seconds, value]) => `${x(seconds)},${y(value)}`).join(' ')} />)}
    </svg>
    {series.length > 1 && <figcaption><Space wrap>{series.map((line, index) =>
      <span key={line.label}><i className={`memory-swatch memory-series-${index}`} />{line.label}</span>)}</Space></figcaption>}
  </figure>;
}

function Tree({ branches, onSelect, depth = 0 }: { branches: Branch[]; onSelect: (id: string) => void; depth?: number }) {
  return <ul className="memory-tree">
    {branches.map((branch) => <li key={branch.frame}>
      {branch.children.length ? <details open={depth < 2}>
        <summary><code>{frameName(branch.frame)}</code> · {format(branch.bytes)} MiB
          {branch.stack && <Button type="link" size="small" onClick={(event) => {
            event.preventDefault();
            onSelect(branch.stack!);
          }}>选择此栈</Button>}
        </summary>
        <Tree branches={branch.children} onSelect={onSelect} depth={depth + 1} />
      </details> : <button type="button" className="memory-tree-leaf"
        onClick={() => { if (branch.stack) onSelect(branch.stack); }}>
        <code>{frameName(branch.frame)}</code> · {format(branch.bytes)} MiB
      </button>}
    </li>)}
  </ul>;
}

function Flame({ tree, onSelect }: { tree: Branch[]; onSelect: (id: string) => void }) {
  const [focused, setFocused] = useState<string>();
  const frames: { node: Branch; key: string; left: number; width: number; depth: number }[] = [];
  function layout(branches: Branch[], left: number, width: number, total: number, depth: number, parent: string) {
    let offset = left;
    for (const branch of branches) {
      const size = width * branch.bytes / total;
      const key = parent + '/' + branch.frame;
      frames.push({ node: branch, key, left: offset, width: size, depth });
      layout(branch.children, offset, size, branch.bytes, depth + 1, key);
      offset += size;
    }
  }
  layout(tree, 0, 100, tree.reduce((total, node) => total + node.bytes, 0), 0, '');
  const depth = Math.max(0, ...frames.map((frame) => frame.depth));
  const active = frames.find((frame) => frame.key === focused);
  return <>
    <div className="memory-flame-scroll">
      <div className="memory-flame" style={{ height: (depth + 1) * 25 }} aria-label="差分火焰图">
        {frames.map(({ node, key, left, width, depth: row }) => <button type="button" key={key}
          aria-pressed={focused === key} title={`${frameName(node.frame)} · ${format(node.bytes)} MiB`}
          style={{ left: left + '%', width: width + '%', bottom: row * 25 }}
          onClick={() => { setFocused(key); if (node.stack) onSelect(node.stack); }}>
          {frameName(node.frame)}
        </button>)}
      </div>
    </div>
    {active && <Typography.Text><code>{frameName(active.node.frame)}</code> · {format(active.node.bytes)} MiB</Typography.Text>}
  </>;
}

export default function LiveMemoryDiagnostics({ endpoint, running }: { endpoint: string; running: boolean }) {
  const [timeline, setTimeline] = useState<Timeline>();
  const [result, setResult] = useState<Comparison>();
  const [start, setStart] = useState<string>();
  const [end, setEnd] = useState<string>();
  const [stack, setStack] = useState<string>();
  const [query, setQuery] = useState('');
  const [direction, setDirection] = useState('growth');
  const [issue, setIssue] = useState('');
  const ignoredKey = 'memory-ignored:' + endpoint;
  const [ignored, setIgnored] = useState(() => readIgnored(ignoredKey));
  const [showIgnored, setShowIgnored] = useState(false);
  const ignoredIds = useMemo(() => new Set(ignored.map(({ id }) => id)), [ignored]);

  function updateIgnored(next: IgnoredStack[]) {
    setIgnored(next);
    try {
      localStorage.setItem(ignoredKey, JSON.stringify(next));
    } catch {
      setIssue('浏览器未能保存忽略列表，本次页面内仍生效');
    }
  }


  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    async function poll() {
      try {
        const data = await api<Timeline>(endpoint + '/memory-live', { signal: controller.signal });
        if (!controller.signal.aborted) { setTimeline(data); setIssue(''); }
      } catch (cause) {
        if (!controller.signal.aborted) setIssue((cause as Error).message);
      } finally {
        if (running && !controller.signal.aborted) timer = window.setTimeout(poll, 5000);
      }
    }
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [endpoint, running]);

  const samples = timeline?.samples ?? [];
  const measurementStart = timeline?.measurement_start;
  const startSample = samples.find((sample) => sample.id === start)
    ?? samples.find((sample) => measurementStart == null || sample.elapsed >= measurementStart);
  const endSample = samples.find((sample) => sample.id === end) ?? samples.at(-1);
  const startId = startSample?.id;
  const endId = endSample?.id;
  const latestId = samples.at(-1)?.id;

  useEffect(() => {
    if (!startId || !endId || Number(startId) >= Number(endId)) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const filters = { start: startId, end: endId, query, direction, ...(stack ? { stack } : {}) };
      const excluded = [...ignoredIds];
      const url = endpoint + '/memory-analysis';
      const request = excluded.length
        ? api<Comparison>(url, { method: 'POST', body: JSON.stringify({ ...filters, excluded }), signal: controller.signal })
        : api<Comparison>(url + '?' + new URLSearchParams(filters), { signal: controller.signal });
      void request
        .then((data) => { if (!controller.signal.aborted) { setResult(data); setIssue(''); } })
        .catch((cause: Error) => { if (!controller.signal.aborted) setIssue(cause.message); });
    }, 150);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [endpoint, startId, endId, latestId, stack, query, direction, ignoredIds]);

  if (!timeline?.enabled && !timeline?.rss.length && !samples.length) return null;
  const selected = result?.selected && !ignoredIds.has(result.selected.id) ? result.selected : undefined;
  const chainPoints: Point[] = selected?.series.map(([seconds, bytes]) => [seconds, mib(bytes)]) ?? [];
  const windowPoints = selected?.series.filter(([seconds]) =>
    seconds >= (startSample?.elapsed ?? 0) && seconds <= (endSample?.elapsed ?? Infinity)).slice(-4) ?? [];
  const sustained = windowPoints.length === 4 && windowPoints.slice(1).every((point, index) => point[1] > windowPoints[index][1]);
  const options = samples.map(({ id, elapsed }) => ({
    value: id,
    label: `${timeLabel(elapsed)} · #${id}${measurementStart != null && elapsed < measurementStart ? ' · 启动阶段' : ''}`,
  }));
  const canCompare = samples.length >= 2 && Number(startId) < Number(endId);
  const chooseStack = (id: string) => setStack(id);

  return <Card title="运行内存诊断" className="live-memory-card">
    <div className="memory-panels">
      <section><Typography.Title level={5}>进程 RSS</Typography.Title>
        <TrendChart title="进程 RSS 趋势" series={[{ label: 'RSS', points: timeline?.rss ?? [] }]} />
      </section>
      <section><Typography.Title level={5}>存活堆趋势</Typography.Title>
        <TrendChart title="存活堆与所选调用链趋势" series={[
          { label: '存活堆总量', points: samples.map(({ elapsed, bytes }) => [elapsed, mib(bytes)]) },
          ...(selected ? [{ label: frameName(selected.label), points: chainPoints }] : []),
        ]} />
      </section>
    </div>
    <Typography.Text type="secondary">heap 为采样估算{samples.length ? ` · 最近快照 ${timeLabel(samples.at(-1)!.elapsed)}` : ''}</Typography.Text>
    {(issue || timeline?.issue) && <Alert type="warning" showIcon message={issue || timeline?.issue} />}
    {samples.length >= 2 && <Space wrap className="memory-controls">
        <Select aria-label="开始快照" value={start ?? 'auto'} options={[
          { value: 'auto', label: measurementStart != null ? '预热后起点' : '最早快照' },
          ...options.filter((item) => Number(item.value) < Number(endId)),
        ]} onChange={(id) => setStart(id === 'auto' ? undefined : id)} />
        <Select aria-label="结束快照" value={end ?? 'latest'} options={[
          { value: 'latest', label: '最新快照' }, ...options.filter((item) => !startId || Number(item.value) > Number(startId)),
        ]} onChange={(id) => setEnd(id === 'latest' ? undefined : id)} />
        <Select aria-label="变化方向" value={direction} options={[
          { value: 'growth', label: '净增调用链' }, { value: 'reduction', label: '净减调用链' },
        ]} onChange={(value) => { setDirection(value); setStack(undefined); }} />
        <Input aria-label="搜索函数" placeholder="搜索栈中的函数" allowClear value={query} onChange={(event) => setQuery(event.target.value)} />
        {ignored.length > 0 && <Button onClick={() => setShowIgnored(true)}>已忽略 {ignored.length} 条</Button>}
      </Space>}
    {!canCompare ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={
      measurementStart != null && !start ? '等待预热后两份可对比的 heap 快照，可手动选择启动阶段' :
        running ? '等待两份运行中的 heap 快照' : '本次没有两份可对比的运行中 heap 快照'
    } /> : <>
      <Table<StackRow> aria-label="完整调用栈排行" size="small" rowKey="id" dataSource={result?.rows.filter(({ id }) => !ignoredIds.has(id)) ?? []}
        pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 500 }}
        columns={[
          { title: '分配调用栈', render: (_, row) => <button type="button" className="memory-stack-button"
            aria-pressed={selected?.id === row.id} onClick={() => chooseStack(row.id)}>
              <code>{frameName(row.label)}</code>
              {Boolean(row.callers?.length) && <small>{row.callers!.map(frameName).join(' → ')}</small>}
            </button> },
          { title: '起点 MiB', align: 'right', render: (_, row) => format(row.before) },
          { title: '终点 MiB', align: 'right', render: (_, row) => format(row.after) },
          { title: '净变化 MiB', align: 'right', render: (_, row) => format(row.delta, true) },
        ]} />
      {result && result.matched > result.rows.length && <Typography.Text type="secondary">
        共 {result.matched} 条，展示前 {result.rows.length} 条。搜索可查找其他栈。
      </Typography.Text>}
      {selected && <section className="memory-detail">
        <Typography.Title level={5}>完整调用栈</Typography.Title>
        <Space wrap><Typography.Text strong>{format(selected.delta, true)} MiB</Typography.Text>
          {sustained && <Typography.Text>最近 3 个快照间隔持续增长 · 疑似持续保留</Typography.Text>}
          <Button size="small" onClick={() => {
            const lines = selected.frames.map((frame) => [frameName(frame), selected.locations?.[frame]].filter(Boolean).join('  '));
            void copyStack(lines.join('\n')).catch(() => setIssue('复制失败，请手动选择调用栈'));
          }}>复制调用栈</Button>
          <Button size="small" onClick={() => {
            updateIgnored([...ignored, { id: selected.id, label: selected.label, callers: selected.callers }]);
            setStack(undefined);
            setResult(undefined);
          }}>忽略此调用链</Button>
        </Space>
        <ol aria-label="从入口到分配点">{selected.frames.map((frame, index) => <li key={index}>
          <code>{frameName(frame)}</code>{selected.locations?.[frame] && <> · <span>{selected.locations[frame]}</span></>}
        </li>)}</ol>
        <Typography.Text type="secondary">源码位置为可解析的函数定义。调用栈以 jeprof 符号化结果为准。对象归属和回收行为仍需验证。</Typography.Text>
      </section>}
      {Boolean(result?.tree.length) && <Tabs items={[
        { key: 'tree', label: '调用树', children: <Tree branches={result!.tree} onSelect={chooseStack} /> },
        { key: 'flame', label: '差分火焰图', children: <Flame tree={result!.tree} onSelect={chooseStack} /> },
      ]} />}
      {Boolean(result?.tree.length) && <Typography.Text type="secondary">调用树和火焰图展示当前方向前 10 条栈，宽度为净变化绝对值。</Typography.Text>}
    </>}
    <Modal title="本任务忽略的调用链" open={showIgnored} footer={null} onCancel={() => setShowIgnored(false)}>
      {ignored.length ? <Space direction="vertical" style={{ display: 'flex' }}>
        {ignored.map((item) => <Space key={item.id} wrap>
          <Typography.Text code>{frameName(item.label)}</Typography.Text>
          {item.callers?.length ? <Typography.Text type="secondary">{item.callers.map(frameName).join(' → ')}</Typography.Text> : null}
          <Button size="small" onClick={() => updateIgnored(ignored.filter(({ id }) => id !== item.id))}>恢复跟踪</Button>
        </Space>)}
        <Button onClick={() => updateIgnored([])}>恢复全部</Button>
      </Space> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有忽略的调用链" />}
    </Modal>
  </Card>;
}
