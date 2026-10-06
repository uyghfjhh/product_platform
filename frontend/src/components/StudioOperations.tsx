import { useEffect, useState } from 'react';
import { Alert, App, Button, Drawer, Input, Space, Table, Tabs } from 'antd';
import { ReloadOutlined, StopOutlined, ToolOutlined } from '@ant-design/icons';
import { api } from '../platform/api';

type Row = Record<string, unknown>;
const views = [['sessions', '会话'], ['locks', '锁与阻塞'], ['replication', '发送复制'], ['receiver', '接收复制'], ['settings', '数据库参数']];
export default function StudioOperations({ url }: { url: string }) {
  const { message, modal } = App.useApp();
  const [open, setOpen] = useState(false);
  const [view, setView] = useState('sessions');
  const [rows, setRows] = useState<Row[]>([]);
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setBusy(true); setError(''); setRows([]);
    void api<[null | { message: string }, Row[]]>(url, { method: 'POST', signal: controller.signal,
      body: JSON.stringify({ procedure: 'inspect', view }) }).then(([failure, result]) => {
        if (controller.signal.aborted) return;
        if (failure) setError(failure.message); else setRows(result || []);
      }).catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [url, open, view, revision]);
  useEffect(() => { setOpen(false); setRows([]); setSearch(''); }, [url]);
  function cancel(row: Row) {
    modal.confirm({ title: `取消会话 ${String(row.pid)} 的当前查询`,
      content: '只取消查询，保留数据库连接。', okText: '取消查询', cancelText: '返回',
      onOk: async () => {
        const [failure, result] = await api<[null | { message: string }, Row[]]>(url, { method: 'POST',
          body: JSON.stringify({ procedure: 'cancel-session', pid: row.pid, backend_start: row.backend_start, acknowledge_change: true }) });
        if (failure) throw new Error(failure.message);
        if (result?.[0]?.cancelled) message.success('已发送取消请求');
        else message.warning('会话已变化或查询已经结束');
        setRevision((value) => value + 1);
      },
    });
  }
  const columns = Object.keys(rows[0] || {}).map((name) => ({ title: name, dataIndex: name,
    render: (value: unknown) => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value) }));
  return <>
    <Button icon={<ToolOutlined />} onClick={() => setOpen(true)}>运维</Button>
    <Drawer title="Studio 数据库运维" open={open} onClose={() => setOpen(false)} width="min(1200px, 100vw)">
      <Tabs activeKey={view} items={views.map(([key, label]) => ({ key, label }))} onChange={(key) => { setView(key); setSearch(''); }} />
      <Space style={{ marginBottom: 12 }}><Input.Search aria-label="筛选运维数据" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="筛选当前快照" />
        <Button icon={<ReloadOutlined />} onClick={() => setRevision((value) => value + 1)}>刷新</Button></Space>
      {view === 'settings' && <Alert type="info" message="参数变更通过部署方案审阅、执行及验收；此处查看数据库实际值。" style={{ marginBottom: 12 }} />}
      {error && <Alert type="error" message={error} />}
      <Table loading={busy} size="small" scroll={{ x: 'max-content' }} rowKey={(_, index) => String(index)}
        dataSource={rows.filter((row) => JSON.stringify(row).toLowerCase().includes(search.toLowerCase()))}
        columns={view === 'sessions' ? [...columns, { title: '操作', dataIndex: 'cancel', render: (_, row) =>
          <Button size="small" icon={<StopOutlined />} disabled={row.state !== 'active'} onClick={() => cancel(row)}>取消查询</Button> }] : columns}
        pagination={{ pageSize: 20 }} />
    </Drawer>
  </>;
}
