import { useState } from 'react';
import { Alert, Button, Drawer, Input, Space, Table, Tag, Typography } from 'antd';
import { PlayCircleOutlined } from '@ant-design/icons';

import { post, type Environment } from '../api';
import type { TopologyNode } from './ThreeTopologyView';

type QueryResult = {
  columns: string[];
  rows: (string | null)[][];
  truncated: boolean;
  command_tag?: string;
  elapsed_ms?: number;
};

/** 即席 SQL 探测抽屉：拓扑节点一键拉起，按节点端口执行只读探测。 */
export default function SqlWorkbenchDrawer({ environment, node, onClose }: {
  environment: Environment | undefined;
  node: TopologyNode | null;
  onClose: () => void;
}) {
  const [sql, setSql] = useState('SELECT version(), pg_is_in_recovery() AS is_standby;');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState('');

  async function execute() {
    if (!environment || !node) return;
    setRunning(true);
    setError('');
    try {
      const data = await post<QueryResult>(
        `/environments/${encodeURIComponent(environment.id)}/query`,
        { sql, port: node.port },
      );
      setResult(data);
    } catch (cause) {
      setResult(null);
      setError((cause as Error).message);
    } finally {
      setRunning(false);
    }
  }

  return (
    <Drawer
      title={node ? `SQL 工作台 · ${node.label || node.id}` : 'SQL 工作台'}
      open={!!node}
      onClose={onClose}
      width={640}
    >
      {node && (
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Space wrap>
            <Tag color="blue">{node.host}:{node.port}</Tag>
            {environment && <Tag>{environment.database_name}@{environment.database_user}</Tag>}
          </Space>
          <Input.TextArea
            value={sql}
            onChange={(event) => setSql(event.target.value)}
            autoSize={{ minRows: 4, maxRows: 10 }}
            className="sql-workbench-input"
            placeholder="输入 SQL，按节点端口直连执行"
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') void execute();
            }}
          />
          <Space>
            <Button type="primary" icon={<PlayCircleOutlined />} loading={running}
              onClick={() => void execute()}>
              执行 (Ctrl+Enter)
            </Button>
            {result?.command_tag && <Tag color="success">{result.command_tag}</Tag>}
            {result?.truncated && <Tag color="warning">结果已截断</Tag>}
          </Space>
          {error && <Alert type="error" showIcon message="查询失败" description={error} />}
          {result && result.columns.length > 0 && (
            <Table
              size="small"
              rowKey={(_row, index) => String(index)}
              dataSource={result.rows.map((row) => Object.fromEntries(
                result.columns.map((column, i) => [column, row[i]]),
              ))}
              columns={result.columns.map((column) => ({
                title: column, dataIndex: column,
                render: (value: string | null) => (
                  <Typography.Text code={value !== null} style={{ fontSize: 12 }}>
                    {value === null ? 'NULL' : value}
                  </Typography.Text>
                ),
              }))}
              pagination={result.rows.length > 20 ? { pageSize: 20 } : false}
              scroll={{ x: true }}
            />
          )}
          {result && result.columns.length === 0 && (
            <Alert type="success" showIcon message={`执行成功: ${result.command_tag || 'OK'}`} />
          )}
        </Space>
      )}
    </Drawer>
  );
}
