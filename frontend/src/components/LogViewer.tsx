import { useMemo, useState } from 'react';
import { Empty, Input, Select, Typography } from 'antd';

type Props = { lines: string[]; filename?: string };

export default function LogViewer({ lines, filename }: Props) {
  const [query, setQuery] = useState('');
  const [level, setLevel] = useState('all');
  const filtered = useMemo(() => lines.map((line, index) => ({ line, index })).filter(({ line }) => {
    if (query && !line.toLowerCase().includes(query.toLowerCase())) return false;
    if (level === 'error' && !/(FATAL|PANIC|ERROR)/i.test(line)) return false;
    if (level === 'warning' && !/WARN(?:ING)?/i.test(line)) return false;
    return true;
  }), [lines, query, level]);

  return <>
    <div className="filter-toolbar">
      <Input.Search value={query} onChange={(event) => setQuery(event.target.value)} allowClear placeholder="搜索原始日志" />
      <Select value={level} onChange={setLevel} options={[
        { label: '全部级别', value: 'all' },
        { label: '错误', value: 'error' },
        { label: '警告', value: 'warning' },
      ]} />
    </div>
    {filename && <Typography.Text type="secondary" className="log-source">{filename}</Typography.Text>}
    {lines.length ? <div className="raw-log" role="log" aria-label="原始日志">
      {filtered.length ? filtered.map(({ line, index }) => {
        const parts = line.split(/(FATAL|PANIC|ERROR|WARN(?:ING)?)/gi);
        return <div className="log-line" key={index}>
          <span className="log-number">{index + 1}</span>
          <span>{parts.map((part, partIndex) => /^(FATAL|PANIC|ERROR)$/i.test(part)
            ? <mark className="log-error" key={partIndex}>{part}</mark>
            : /^WARN(?:ING)?$/i.test(part)
              ? <mark className="log-warning" key={partIndex}>{part}</mark>
              : <span key={partIndex}>{part}</span>)}</span>
        </div>;
      }) : <div className="log-empty">没有符合条件的日志行</div>}
    </div> : <Empty description="暂无原始日志" />}
  </>;
}
