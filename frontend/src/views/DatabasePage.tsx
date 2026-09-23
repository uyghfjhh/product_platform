import { useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Empty, Space, Table, Tag, Tree, Typography } from 'antd';
import { PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons';

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

function quoteIdentifier(value: string) {
  return `"${value.replaceAll('"', '""')}"`;
}

export default function DatabasePage({ environment }: Props) {
  const { message } = App.useApp();
  const [sql, setSql] = useState('SELECT version();');
  const [result, setResult] = useState<QueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [objects, setObjects] = useState<DatabaseObject[]>([]);
  const [columns, setColumns] = useState<Column[]>([]);
  const [selected, setSelected] = useState<DatabaseObject | null>(null);
  const [objectsLoading, setObjectsLoading] = useState(false);
  const [objectsError, setObjectsError] = useState('');

  useEffect(() => {
    setObjects([]);
    setSelected(null);
    setColumns([]);
    setResult(null);
    setObjectsError('');
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

  async function run() {
    if (!environment || !sql.trim()) return;
    setLoading(true);
    try {
      setResult(await post<QueryResult>(`/environments/${encodeURIComponent(environment.id)}/query`, { sql, max_rows: 200 }));
    } catch (error) {
      setResult(null);
      message.error((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  const rows = result?.rows.map((row, index) => ({ key: index, ...Object.fromEntries(row.map((value, column) => [String(column), value])) })) || [];
  const tree = useMemo(() => Array.from(new Set(objects.map((item) => item.schema))).map((schema) => ({
    title: schema, key: `schema:${schema}`,
    children: objects.filter((item) => item.schema === schema).map((item) => ({
      title: `${item.name} ${item.kind === 'v' || item.kind === 'm' ? '(视图)' : ''}`,
      key: JSON.stringify([item.schema, item.name]), isLeaf: true,
    })),
  })), [objects]);
  return (
    <>
      <div className="page-heading">
        <div><Typography.Title level={3}>数据库管理</Typography.Title><Typography.Text type="secondary">SQL 工作台</Typography.Text></div>
        {environment && <Typography.Text code>{environment.host}:{environment.port} / {environment.database_name}</Typography.Text>}
      </div>
      {!environment ? <Empty description="先选择数据库环境" /> : <>
        <section className="work-section database-workspace">
          <div className="database-objects">
            <div className="section-heading"><Typography.Title level={5}>数据库对象</Typography.Title><Button icon={<ReloadOutlined />} loading={objectsLoading} onClick={() => void refreshObjects()} aria-label="读取数据库对象" /></div>
            {objectsError && <Alert type="warning" showIcon message="无法读取对象" description={objectsError} />}
            {tree.length ? <Tree showLine defaultExpandAll treeData={tree} onSelect={(keys) => {
              const item = objects.find((candidate) => JSON.stringify([candidate.schema, candidate.name]) === keys[0]);
              if (item) void selectObject(item);
            }} /> : <Empty description={objectsLoading ? '正在读取' : '点击右上角读取对象'} />}
          </div>
          <div className="database-query">
            <div className="section-heading"><Typography.Title level={5}>SQL</Typography.Title><Button type="primary" icon={<PlayCircleOutlined />} loading={loading} onClick={() => void run()}>执行</Button></div>
            <CodeEditor value={sql} language="sql" onChange={setSql} height={260} />
            {selected && <div className="database-columns"><Typography.Text strong>{selected.schema}.{selected.name}</Typography.Text><Typography.Text type="secondary">{columns.map((item) => `${item.name} ${item.type}`).join(' · ') || '无可见列'}</Typography.Text></div>}
          </div>
        </section>
        <section className="work-section">
          <div className="section-heading"><Typography.Title level={5}>结果</Typography.Title>{result && <Space><Tag>{result.command_tag}</Tag><Typography.Text type="secondary">{result.elapsed_ms} ms · {result.row_count} 行</Typography.Text></Space>}</div>
          {!result ? <Empty description="执行 SQL 后显示结果" /> : result.columns.length > 0 ? <>
            {result.truncated && <Alert type="info" showIcon message="仅展示前 200 行" />}
            <Table rowKey="key" size="small" scroll={{ x: 'max-content' }} pagination={{ pageSize: 20 }}
              dataSource={rows}
              columns={result.columns.map((name, index) => ({ title: name, dataIndex: String(index), key: String(index), render: (value: string | null) => value === null ? <Typography.Text type="secondary">NULL</Typography.Text> : value }))}
            />
          </> : <Alert type="success" showIcon message={result.command_tag || '执行完成'} />}
        </section>
      </>}
    </>
  );
}
