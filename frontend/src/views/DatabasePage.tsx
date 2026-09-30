import { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { Alert, App, Badge, Button, Empty, Input, Popconfirm, Select, Space, Table, Tabs, Tag, Tree, Typography } from 'antd';
import { PlayCircleOutlined, ReloadOutlined, DatabaseOutlined, ThunderboltOutlined, StopOutlined, ExpandOutlined, CompressOutlined } from '@ant-design/icons';

const StudioPanel = lazy(() => import('../components/StudioPanel'));

import { api, post, type Environment, type Product } from '../platform/api';
import CodeEditor from '../components/LazyCodeEditor';
import PlatformErrorBoundary from '../components/PlatformErrorBoundary';

type Props = {
  product: Product | undefined;
  environment: Environment | undefined;
  openTask: (taskId: string) => void;
  reload: () => Promise<void>;
  environments?: Environment[];
  onSelectEnvironment?: (id: string) => void;
};

type QueryResult = {
  columns: string[];
  rows: (string | null)[][];
  row_count: number;
  truncated: boolean;
  command_tag: string;
  elapsed_ms: number;
};

type DatabaseObject = { schema: string; name: string; kind: string };
type Column = { name: string; type: string; nullable: boolean };

type TopologyNode = {
  id: string;
  label: string;
  host: string;
  port: number;
  role: string;
  group?: string;
};

type NodeStatusMap = Record<string, { running: boolean | null }>;
type Row = Record<string, string | number | boolean | null>;
type ReplicationInfo = { senders: Row[]; receivers: Row[]; slots: Row[] };

function quoteIdentifier(value: string) {
  return `"${value.replaceAll('"', '""')}"`;
}

const SQL_TEMPLATES = [
  {
    name: '📡 流复制监控 (pg_stat_replication)',
    sql: 'SELECT pid, usename, application_name, client_addr, state, sync_state, sync_priority FROM pg_stat_replication;',
  },
  {
    name: '📥 WAL接收进度 (pg_stat_wal_receiver)',
    sql: 'SELECT pid, status, receive_start_lsn, written_lsn, flushed_lsn, sender_host, sender_port FROM pg_stat_wal_receiver;',
  },
  {
    name: '🪐 MMR节点列表 (fdd.mmr_node)',
    sql: 'SELECT node_id, node_name, node_state, dsn FROM fdd.mmr_node;',
  },
  {
    name: '🌐 MMR集群组状态 (fdd.mmr_group)',
    sql: 'SELECT group_id, group_name, group_uuid FROM fdd.mmr_group;',
  },
  {
    name: '📋 MMR复制集 (fdd.mmr_replication_set)',
    sql: 'SELECT set_id, set_name, auto_add_tables FROM fdd.mmr_replication_set;',
  },
  {
    name: '🔍 内核模式与系统时间',
    sql: 'SELECT version(), current_timestamp, pg_is_in_recovery() AS is_standby;',
  },
];

export default function DatabasePage({ environment, environments = [], onSelectEnvironment }: Props) {
  const { message } = App.useApp();
  const [sql, setSql] = useState('SELECT version(), current_timestamp, pg_is_in_recovery() AS is_standby;');
  const [result, setResult] = useState<QueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [objects, setObjects] = useState<DatabaseObject[]>([]);
  const [columns, setColumns] = useState<Column[]>([]);
  const [selected, setSelected] = useState<DatabaseObject | null>(null);
  const [objectsLoading, setObjectsLoading] = useState(false);
  const [objectsError, setObjectsError] = useState('');
  const [nodes, setNodes] = useState<TopologyNode[]>([]);
  const [nodeStatus, setNodeStatus] = useState<NodeStatusMap>({});
  const [selectedPort, setSelectedPort] = useState<number | null>(null);
  const [studioFullscreen, setStudioFullscreen] = useState(false);

  useEffect(() => {
    if (!studioFullscreen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setStudioFullscreen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [studioFullscreen]);
  const [sessions, setSessions] = useState<Row[]>([]);
  const [locks, setLocks] = useState<Row[]>([]);
  const [replication, setReplication] = useState<ReplicationInfo>({ senders: [], receivers: [], slots: [] });
  const [settings, setSettings] = useState<Row[]>([]);
  const [adminLoading, setAdminLoading] = useState(false);
  const [adminTab, setAdminTab] = useState('sessions');
  const [settingsSearch, setSettingsSearch] = useState('');

  useEffect(() => {
    setObjects([]);
    setSelected(null);
    setColumns([]);
    setResult(null);
    setObjectsError('');
    setNodes([]);
    setNodeStatus({});
    setSessions([]); setLocks([]);
    setReplication({ senders: [], receivers: [], slots: [] });
    setSettings([]); setSettingsSearch('');

    if (environment) {
      // Check if arriving from DeploymentPage with specific node
      const savedPort = sessionStorage.getItem('sql_target_port');
      if (savedPort) {
        setSelectedPort(Number(savedPort));
        sessionStorage.removeItem('sql_target_port');
      } else {
        setSelectedPort(environment.port);
      }

      // Fetch topology nodes to populate target switcher
      api<{ nodes: TopologyNode[] }>(`/environments/${encodeURIComponent(environment.id)}/topology`)
        .then((res) => {
          if (res && res.nodes) {
            setNodes(res.nodes);
          }
        })
        .catch(() => undefined);
      api<NodeStatusMap>(`/environments/${encodeURIComponent(environment.id)}/topology/status`)
        .then((res) => setNodeStatus(res || {}))
        .catch(() => undefined);
    }
  }, [environment?.id]);

  async function refreshObjects() {
    if (!environment) return;
    setObjectsLoading(true);
    setObjectsError('');
    try {
      setObjects(await api<DatabaseObject[]>(`/environments/${encodeURIComponent(environment.id)}/objects`));
    } catch (error) {
      setObjectsError((error as Error).message);
    } finally {
      setObjectsLoading(false);
    }
  }

  async function selectObject(item: DatabaseObject) {
    if (!environment) return;
    setSelected(item);
    setSql(`SELECT * FROM ${quoteIdentifier(item.schema)}.${quoteIdentifier(item.name)} LIMIT 100;`);
    try {
      setColumns(await api<Column[]>(`/environments/${encodeURIComponent(environment.id)}/objects/${encodeURIComponent(item.schema)}/${encodeURIComponent(item.name)}/columns`));
    } catch (error) {
      setColumns([]);
      message.error((error as Error).message);
    }
  }

  async function run(customSql?: string) {
    if (!environment) return;
    const targetSql = customSql || sql;
    if (!targetSql.trim()) return;

    setLoading(true);
    try {
      const activePort = selectedPort || environment.port;
      const res = await post<QueryResult>(`/environments/${encodeURIComponent(environment.id)}/query`, {
        sql: targetSql,
        max_rows: 200,
        port: activePort,
      });
      setResult(res);
    } catch (error) {
      setResult(null);
      message.error((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function handleSelectTemplate(tplSql: string) {
    setSql(tplSql);
    void run(tplSql);
  }

  async function refreshAdmin(tab = adminTab, search = settingsSearch) {
    if (!environment) return;
    const port = selectedPort || environment.port;
    const query = `?port=${port}`;
    setAdminLoading(true);
    try {
      if (tab === 'sessions') setSessions(await api<Row[]>(`/environments/${encodeURIComponent(environment.id)}/sessions${query}`));
      else if (tab === 'locks') setLocks(await api<Row[]>(`/environments/${encodeURIComponent(environment.id)}/locks${query}`));
      else if (tab === 'replication') setReplication(await api<ReplicationInfo>(`/environments/${encodeURIComponent(environment.id)}/replication${query}`));
      else setSettings(await api<Row[]>(`/environments/${encodeURIComponent(environment.id)}/settings${query}&search=${encodeURIComponent(search)}`));
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setAdminLoading(false);
    }
  }

  async function cancelBackend(pid: number, terminate: boolean) {
    if (!environment) return;
    try {
      await post(`/environments/${encodeURIComponent(environment.id)}/sessions/${pid}/cancel`, {
        terminate, port: selectedPort || environment.port,
      });
      message.success(terminate ? `已终止会话 ${pid}` : `已取消查询 ${pid}`);
      void refreshAdmin('sessions');
    } catch (error) {
      message.error((error as Error).message);
    }
  }

  function rowColumns(rows: Row[], order: string[], widths?: Record<string, number>) {
    const seen = new Set(order.filter((key) => rows.some((row) => key in row)));
    rows.forEach((row) => Object.keys(row).forEach((key) => seen.add(key)));
    return Array.from(seen).map((key) => ({
      title: key, dataIndex: key, key,
      width: widths?.[key],
      render: (value: Row[string]) => value === null || value === undefined
        ? <Typography.Text type="secondary">NULL</Typography.Text>
        : String(value),
    }));
  }

  const rows = result?.rows.map((row, index) => ({
    key: index,
    ...Object.fromEntries(row.map((value, column) => [String(column), value])),
  })) || [];

  const tree = useMemo(() => Array.from(new Set(objects.map((item) => item.schema))).map((schema) => ({
    title: schema,
    key: `schema:${schema}`,
    children: objects.filter((item) => item.schema === schema).map((item) => ({
      title: `${item.name} ${item.kind === 'v' || item.kind === 'm' ? '(视图)' : ''}`,
      key: JSON.stringify([item.schema, item.name]),
      isLeaf: true,
    })),
  })), [objects]);

  const nodeOptions = useMemo(() => {
    if (!environment) return [];
    const baseOption = {
      label: `默认入口 (${environment.host}:${environment.port})`,
      value: environment.port,
    };
    if (!nodes.length) return [baseOption];

    const topoOptions = nodes.map((n) => {
      const running = nodeStatus[n.id]?.running;
      return {
        label: (
          <Space size={6}>
            <Badge
              status={running === true ? 'success' : running === false ? 'error' : 'default'}
              title={running === true ? '运行中' : running === false ? '已停止' : '状态未知'}
            />
            <span>
              [{n.role.toUpperCase()}] {n.label} ({n.host}:{n.port})
              {running === false && <Typography.Text type="danger">（已停止）</Typography.Text>}
            </span>
          </Space>
        ),
        value: n.port,
      };
    });
    return [baseOption, ...topoOptions];
  }, [environment, nodes, nodeStatus]);

  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>数据库管理</Typography.Title>
          <Typography.Text type="secondary">对象浏览、SQL 查询与运行状态</Typography.Text>
        </div>
        {environment && (
          <Space>
            <span style={{ fontSize: 13, color: '#64748b' }}>直连目标实例:</span>
            <Select
              style={{ minWidth: 320 }}
              value={selectedPort || environment.port}
              onChange={(val) => setSelectedPort(Number(val))}
              options={nodeOptions}
            />
          </Space>
        )}
      </div>

      {!environment ? <Empty description="先选择数据库环境" /> : <>
        <Space wrap style={{ marginBottom: 16 }}>
          <Typography.Text strong>连接环境</Typography.Text>
          <Select aria-label="选择数据库环境" style={{ minWidth: 240 }} value={environment?.id}
            options={environments.map((item) => ({ value: item.id, label: `${item.title} · ${item.host}:${item.port}` }))}
            onChange={(id) => onSelectEnvironment?.(id)} />
        </Space>
        {/* Quick SQL Templates Bar */}
        <div style={{ marginBottom: 14, display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: '#64748b', fontWeight: 600 }}>快捷模板:</span>
          {SQL_TEMPLATES.map((tpl) => (
            <Button
              key={tpl.name}
              size="small"
              icon={<ThunderboltOutlined style={{ color: '#0284c7' }} />}
              onClick={() => handleSelectTemplate(tpl.sql)}
            >
              {tpl.name}
            </Button>
          ))}
        </div>

        <section className="work-section database-workspace">
          <div className="database-objects">
            <div className="section-heading">
              <Typography.Title level={5}>数据库对象</Typography.Title>
              <Button icon={<ReloadOutlined />} loading={objectsLoading} onClick={() => void refreshObjects()} aria-label="读取数据库对象" />
            </div>
            {objectsError && <Alert type="warning" showIcon message="无法读取对象" description={objectsError} />}
            {tree.length ? (
              <Tree
                showLine
                defaultExpandAll
                treeData={tree}
                onSelect={(keys) => {
                  const item = objects.find((candidate) => JSON.stringify([candidate.schema, candidate.name]) === keys[0]);
                  if (item) void selectObject(item);
                }}
              />
            ) : (
              <Empty description={objectsLoading ? '正在读取' : '点击右上角读取对象'} />
            )}
          </div>
          <div className="database-query">
            <div className="section-heading">
              <Space>
                <DatabaseOutlined style={{ color: '#166e60' }} />
                <Typography.Title level={5} style={{ margin: 0 }}>SQL 交互编辑器</Typography.Title>
                <Tag color="cyan">端口: {selectedPort || environment.port}</Tag>
              </Space>
              <Button type="primary" icon={<PlayCircleOutlined />} loading={loading} onClick={() => void run()}>
                执行 (Run)
              </Button>
            </div>
            <CodeEditor value={sql} language="sql" onChange={setSql} height={260} />
            {selected && (
              <div className="database-columns">
                <Typography.Text strong>{selected.schema}.{selected.name}</Typography.Text>
                <Typography.Text type="secondary">{columns.map((item) => `${item.name} ${item.type}`).join(' · ') || '无可见列'}</Typography.Text>
              </div>
            )}
          </div>
        </section>

        <section className="work-section">
          <div className="section-heading">
            <Typography.Title level={5}>查询结果</Typography.Title>
            {result && (
              <Space>
                <Tag color={result.row_count > 0 ? 'success' : 'default'}>{result.command_tag || 'OK'}</Tag>
                <Typography.Text type="secondary">{result.elapsed_ms} ms · {result.row_count} 行结果</Typography.Text>
              </Space>
            )}
          </div>
          {!result ? (
            <Empty description="执行 SQL 后显示结果" />
          ) : result.columns.length > 0 ? <>
            {result.truncated && <Alert type="info" showIcon message="仅展示前 200 行" style={{ marginBottom: 10 }} />}
            <Table
              rowKey="key"
              size="small"
              scroll={{ x: 'max-content' }}
              pagination={{ pageSize: 20 }}
              dataSource={rows}
              columns={result.columns.map((name, index) => ({
                title: name,
                dataIndex: String(index),
                key: String(index),
                render: (value: string | null) => value === null ? <Typography.Text type="secondary">NULL</Typography.Text> : value,
              }))}
            />
          </> : (
            <Alert type="success" showIcon message={result.command_tag || '执行完成'} />
          )}
        </section>

        <section className="work-section">
          <div className="section-heading">
            <Space>
              <Typography.Title level={5}>实例运行状态</Typography.Title>
              <Tag color="cyan">端口: {selectedPort || environment.port}</Tag>
            </Space>
            <Space>
              {adminTab === 'settings' && (
                <Input.Search size="small" placeholder="参数名" style={{ width: 200 }} allowClear
                  value={settingsSearch} onChange={(event) => setSettingsSearch(event.target.value)}
                  onSearch={(value) => void refreshAdmin('settings', value)} />
              )}
              <Button icon={<ReloadOutlined />} loading={adminLoading} onClick={() => void refreshAdmin()} aria-label="刷新实例状态" />
            </Space>
          </div>
          <Tabs
            size="small"
            activeKey={adminTab}
            onChange={(key) => { setAdminTab(key); void refreshAdmin(key); }}
            items={[
              {
                key: 'sessions', label: `会话 (${sessions.length})`,
                children: sessions.length ? (
                  <Table rowKey="pid" size="small" scroll={{ x: 'max-content' }} pagination={{ pageSize: 20 }}
                    dataSource={sessions}
                    columns={[
                      ...rowColumns(sessions, ['pid', 'usename', 'datname', 'application_name', 'client_addr', 'state', 'wait_event_type', 'wait_event', 'query_start', 'backend_type', 'query'], { query: 360 }),
                      {
                        title: '操作', key: 'ops', fixed: 'right' as const, render: (_, row) => (
                          <Space size={4}>
                            <Popconfirm title={`取消 PID ${row.pid} 的当前查询？`} onConfirm={() => void cancelBackend(Number(row.pid), false)}>
                              <Button size="small">取消查询</Button>
                            </Popconfirm>
                            <Popconfirm title={`终止会话 PID ${row.pid}？连接将被断开`} onConfirm={() => void cancelBackend(Number(row.pid), true)}>
                              <Button size="small" danger icon={<StopOutlined />}>终止会话</Button>
                            </Popconfirm>
                          </Space>
                        ),
                      },
                    ]} />
                ) : <Empty description="点击刷新读取会话" />,
              },
              {
                key: 'locks', label: `锁 (${locks.length})`,
                children: locks.length ? (
                  <Table rowKey={(row) => `${row.pid}-${row.locktype}-${row.mode}`} size="small" scroll={{ x: 'max-content' }} pagination={{ pageSize: 20 }}
                    dataSource={locks}
                    columns={rowColumns(locks, ['pid', 'locktype', 'mode', 'granted', 'relation', 'blocked_by', 'usename', 'state', 'query'], { query: 320 })} />
                ) : <Empty description="点击刷新读取锁" />,
              },
              {
                key: 'replication', label: '复制',
                children: <>
                  <Typography.Text strong>发送端 (pg_stat_replication)</Typography.Text>
                  <Table rowKey="pid" size="small" scroll={{ x: 'max-content' }} pagination={false} style={{ marginBottom: 16 }}
                    dataSource={replication.senders}
                    columns={rowColumns(replication.senders, ['pid', 'application_name', 'client_addr', 'state', 'sync_state', 'sent_lsn', 'replay_lsn', 'replay_lag'])}
                    locale={{ emptyText: '本节点无发送端连接（备库或无订阅者）' }} />
                  <Typography.Text strong>接收端 (pg_stat_wal_receiver)</Typography.Text>
                  <Table rowKey="pid" size="small" scroll={{ x: 'max-content' }} pagination={false} style={{ marginBottom: 16 }}
                    dataSource={replication.receivers}
                    columns={rowColumns(replication.receivers, ['pid', 'status', 'sender_host', 'sender_port', 'written_lsn', 'flushed_lsn'])}
                    locale={{ emptyText: '本节点不是备库' }} />
                  <Typography.Text strong>复制槽 (pg_replication_slots)</Typography.Text>
                  <Table rowKey="slot_name" size="small" scroll={{ x: 'max-content' }} pagination={false}
                    dataSource={replication.slots}
                    columns={rowColumns(replication.slots, ['slot_name', 'slot_type', 'datname', 'active', 'wal_status', 'restart_lsn', 'confirmed_flush_lsn', 'safe_wal_size'])} />
                </>,
              },
              {
                key: 'settings', label: `参数 (${settings.length})`,
                children: settings.length ? (
                  <Table rowKey="name" size="small" scroll={{ x: 'max-content' }} pagination={{ pageSize: 20 }}
                    dataSource={settings}
                    columns={rowColumns(settings, ['name', 'setting', 'unit', 'source', 'pending_restart', 'category', 'vartype', 'min_val', 'max_val'])} />
                ) : <Empty description="点击刷新读取参数（默认显示非默认值与常用项）" />,
              },
            ]} />
        </section>

        <section className="work-section">
          <div className="section-heading">
            <div>
              <Typography.Title level={5} style={{ margin: 0 }}>数据管理器</Typography.Title>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Prisma Studio——浏览表数据与结构；经由平台 BFF 连接所选节点
              </Typography.Text>
            </div>
          </div>
          <div className={studioFullscreen ? 'database-studio database-studio-fullscreen' : 'database-studio'}>
            <Button
              className="database-studio-toggle"
              size="small"
              icon={studioFullscreen ? <CompressOutlined /> : <ExpandOutlined />}
              onClick={() => setStudioFullscreen((v) => !v)}
            >
              {studioFullscreen ? '退出全屏' : '全屏'}
            </Button>
            <PlatformErrorBoundary>
              <Suspense fallback={<Empty description="正在加载数据管理器…" />}>
                <StudioPanel
                  key={`${environment.id}:${selectedPort || environment.port}`}
                  environmentId={environment.id}
                  port={selectedPort || environment.port}
                />
              </Suspense>
            </PlatformErrorBoundary>
          </div>
        </section>
      </>}
    </>
  );
}
