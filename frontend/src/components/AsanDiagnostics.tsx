import { useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Select, Space, Typography } from 'antd';
import { api } from '../platform/api';

type Report = {
  name: string; path: string; pid: number | null; type: string | null;
  access: string | null; size: number | null; source: string | null; complete: boolean;
};
type Reports = { build: { version: string; sha256: string; resolved_path: string } | null; files: Report[] };

export default function AsanDiagnostics({ endpoint, running }: { endpoint: string; running: boolean }) {
  const [data, setData] = useState<Reports>();
  const [selected, setSelected] = useState('');
  const [raw, setRaw] = useState('');
  const [issue, setIssue] = useState('');
  const [copied, setCopied] = useState('');
  const report = data?.files.find((item) => item.name === selected)
    ?? data?.files.find((item) => item.type) ?? data?.files[0];
  const url = report ? `/api/v1${endpoint}/asan/${encodeURIComponent(report.name)}` : '';

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    async function refresh() {
      try {
        const next = await api<Reports>(endpoint + '/asan', { signal: controller.signal });
        if (!controller.signal.aborted) setData(next);
      } catch (cause) {
        if (!controller.signal.aborted) setIssue((cause as Error).message);
      } finally {
        if (running && !controller.signal.aborted) timer = window.setTimeout(refresh, 2500);
      }
    }
    void refresh();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [endpoint, running]);

  useEffect(() => {
    setRaw('');
    if (!url) return;
    const controller = new AbortController();
    let timer: number | undefined;
    async function refresh() {
      try {
        const response = await fetch(url, { signal: controller.signal, cache: 'no-store' });
        if (!response.ok) throw new Error('原始报告读取失败');
        const text = await response.text();
        if (!controller.signal.aborted) setRaw(text);
      } catch (cause) {
        if (!controller.signal.aborted) setIssue((cause as Error).message);
      } finally {
        if (running && !controller.signal.aborted) timer = window.setTimeout(refresh, 2500);
      }
    }
    void refresh();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [url, running]);

  async function copy(value: string, label: string) {
    try {
      if (navigator.clipboard) await navigator.clipboard.writeText(value);
      else {
        const field = document.createElement('textarea');
        field.value = value;
        field.style.position = 'fixed';
        field.style.opacity = '0';
        document.body.append(field);
        try {
          field.select();
          if (!document.execCommand('copy')) throw new Error('复制失败');
        } finally { field.remove(); }
      }
      setCopied(label + '已复制');
    } catch { setIssue('复制失败，可以选中文字或下载原始报告'); }
  }

  return <Card title="ASan 踩内存报告" style={{ marginTop: 16 }}>
    {issue && <Alert type="warning" showIcon message={issue} />}
    {data?.build && <Typography.Paragraph>
      <code>{data.build.resolved_path}</code><br />
      {data.build.version} · SHA256 <code>{data.build.sha256}</code>
    </Typography.Paragraph>}
    {!report ? <Empty description={running ? '等待 ASan 报告' : '本任务没有采集到 ASan 报告'} /> : <>
      <Space wrap>
        <Select aria-label="ASan 报告" value={report.name} onChange={setSelected}
          options={data?.files.map((item) => ({ value: item.name, label: item.pid ? `PID ${item.pid}` : '启动输出' }))} />
        <Button onClick={() => void copy(report.path, '路径')}>复制文件路径</Button>
        <Button disabled={!raw} onClick={() => void copy(raw, '原文')}>复制原文</Button>
        <Button href={url} download={report.name}>下载原始报告</Button>
        <Typography.Text type="secondary" aria-live="polite">{copied}</Typography.Text>
      </Space>
      <Alert style={{ marginTop: 12 }} type={report.type ? 'error' : 'warning'} showIcon
        message={report.type ?? '未提取到 ASan 错误类型'}
        description={<>{report.access && `${report.access === 'READ' ? '读取' : '写入'} ${report.size} 字节`}{report.source && <div><code>{report.source}</code></div>}
          {!report.complete && <div>原文没有 ASan SUMMARY，报告可能仍在写入或未完整落盘。</div>}</>} />
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 680, overflow: 'auto' }}>{raw}</pre>
    </>}
  </Card>;
}
