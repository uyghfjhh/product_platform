import { useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Empty, Select, Space, Table, Tag, Tree, Typography } from 'antd';
import { PlayCircleOutlined, ReloadOutlined, DatabaseOutlined, ThunderboltOutlined } from '@ant-design/icons';

import { api, post, type Environment, type Product } from '../api';
import CodeEditor from '../components/LazyCodeEditor';

type Props = {
  product: Product | undefined;
  environment: Environment | undefined;
  openTask: (taskId: string) => void;
  reload: () => Promise<void>;
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

export default function DatabasePage({ environment }: Props) {
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
  const [selectedPort, setSelectedPort] = useState<number | null>(null);

  useEffect(() => {
    setObjects([]);
    setSelected(null);
    setColumns([]);
    setResult(null);
    setObjectsError('');
    setNodes([]);

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

    const topoOptions = nodes.map((n) => ({
      label: `[${n.role.toUpperCase()}] ${n.label} (${n.host}:${n.port})`,
      value: n.port,
    }));
    return [baseOption, ...topoOptions];
  }, [environment, nodes]);

  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>数据库管理</Typography.Title>
          <Typography.Text type="secondary">Web-PSQL 全节点交互工作台与内核状态实时查询</Typography.Text>
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
      </>}
    </>
  );
}
