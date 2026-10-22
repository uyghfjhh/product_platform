import { useEffect, useState } from 'react';
import { Alert, Button, Drawer, Input, Space, Tag, Tooltip, Typography } from 'antd';
import { CopyOutlined, DatabaseOutlined, PlayCircleOutlined } from '@ant-design/icons';

import { post, type Action, type Environment } from '../platform/api';
import type { TopologyNode } from '../platform/topology';

type ObservedMap = Record<string, { running: boolean | null; message: string; pid?: number | null }> | null;

const PROBE_PRESETS = [
  { label: '🔍 主备判定', sql: 'SELECT pg_is_in_recovery();', desc: '查询当前节点是否处于只读流复制恢复状态 (false 为主库，true 为备库)' },
  { label: '📊 复制状态', sql: 'SELECT client_addr, state, sync_state, replay_lsn FROM pg_stat_replication;', desc: '查询主库向从库发送的流复制连接与回放进度' },
  { label: '⏱️ 实例版本', sql: 'SELECT version();', desc: '查询当前实例 PostgreSQL/内核编译版本' },
  { label: '👥 活动连接', sql: 'SELECT count(*), state FROM pg_stat_activity GROUP BY state;', desc: '统计各连接状态的客户端连接数' },
];

type Props = {
  environment: Environment | undefined;
  selectedNode: TopologyNode | null;
  observed: ObservedMap;
  actions: Action[];
  loading: boolean;
  roleBusy: boolean;
  onClose: () => void;
  onOpenDatabase?: (node?: TopologyNode, view?: 'sql') => void;
  onRunAction: (action: Action, target?: string) => void | Promise<void>;
  onRunRole: (actionId: string) => void | Promise<void>;
  onCopyText: (text: string) => void;
};

export default function NodeInspectorDrawer({
  environment,
  selectedNode,
  observed,
  actions,
  loading,
  roleBusy,
  onClose,
  onOpenDatabase,
  onRunAction,
  onRunRole,
  onCopyText,
}: Props) {
  const [probeSql, setProbeSql] = useState('SELECT pg_is_in_recovery();');
  const [probeExecuting, setProbeExecuting] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [probeResult, setProbeResult] = useState<Array<Record<string, any>> | null>(null);
  const [probeDuration, setProbeDuration] = useState<number | null>(null);

  useEffect(() => {
    setProbeError(null);
    setProbeResult(null);
    setProbeDuration(null);
  }, [selectedNode?.id]);

  const runProbeSql = async (sqlToRun?: string) => {
    const sql = (sqlToRun ?? probeSql).trim();
    if (!sql || !environment || !selectedNode) return;
    setProbeExecuting(true);
    setProbeError(null);
    setProbeResult(null);
    const start = performance.now();
    try {
      const [err, res] = await post<[ { message?: string } | null, Record<string, any>[] | null ]>(
        `/environments/${encodeURIComponent(environment.id)}/studio/nodes/${encodeURIComponent(selectedNode.id)}`,
        { procedure: 'query', query: { sql } },
      );
      setProbeDuration(Math.round(performance.now() - start));
      if (err) {
        setProbeError(err.message || '查询失败');
      } else {
        setProbeResult(res || []);
      }
    } catch (cause) {
      setProbeDuration(Math.round(performance.now() - start));
      setProbeError((cause as Error).message);
    } finally {
      setProbeExecuting(false);
    }
  };

  return (
    <Drawer
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span>{selectedNode?.label || '节点详情'}</span>
          {selectedNode && (
            <Tag color={selectedNode.role === 'primary' ? 'gold' : 'blue'}>
              {selectedNode.role.toUpperCase()}
            </Tag>
          )}
          {selectedNode && (
            <Tag color={observed?.[selectedNode.id]?.running === false ? 'error'
              : observed?.[selectedNode.id]?.running === true ? 'success' : 'default'}>
              {observed?.[selectedNode.id]?.running === false ? '已停止'
                : observed?.[selectedNode.id]?.running === true ? '运行中' : '探测中'}
            </Tag>
          )}
        </div>
      }
      open={!!selectedNode}
      onClose={onClose}
      width={420}
    >
      {selectedNode && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {/* Quick PSQL Connection Snippet */}
          <div className="psql-snippet-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <span className="psql-snippet-label">PSQL 直连命令行</span>
              <Button
                size="small"
                type="text"
                className="psql-snippet-copy"
                icon={<CopyOutlined />}
                onClick={() => onCopyText(`psql -h ${selectedNode.host} -p ${selectedNode.port} -U ${environment?.database_user || 'postgres'}`)}
              >
                复制
              </Button>
            </div>
            <code className="psql-snippet">
              psql -h {selectedNode.host} -p {selectedNode.port} -U {environment?.database_user || 'postgres'}
            </code>
            {onOpenDatabase && (
              <Button
                type="primary"
                style={{ width: '100%', marginTop: 10 }}
                icon={<DatabaseOutlined />}
                onClick={() => onOpenDatabase(selectedNode)}
              >
                进入完整数据库 Studio
              </Button>
            )}
          </div>

          {/* In-Place Quick SQL Probe Console */}
          <div className="node-sql-probe-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontWeight: 600, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                <span>⚡</span> SQL 探针控制台
              </span>
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                支持 <kbd style={{ padding: '1px 4px', background: 'rgba(255,255,255,0.08)', borderRadius: 3 }}>Ctrl+Enter</kbd>
              </Typography.Text>
            </div>

            {/* Preset Diagnostic Pills */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
              {PROBE_PRESETS.map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  className="probe-preset-pill"
                  title={`${preset.desc}\n${preset.sql}`}
                  onClick={() => {
                    setProbeSql(preset.sql);
                    void runProbeSql(preset.sql);
                  }}
                >
                  {preset.label}
                </button>
              ))}
            </div>

            {/* SQL Text Area */}
            <Input.TextArea
              value={probeSql}
              onChange={(e) => setProbeSql(e.target.value)}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                  e.preventDefault();
                  void runProbeSql();
                }
              }}
              autoSize={{ minRows: 2, maxRows: 5 }}
              placeholder="输入 SQL 语句，例如 SELECT version();"
              style={{
                fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace',
                fontSize: 12,
                background: '#090f1c',
                color: '#e2e8f0',
                borderColor: '#2d4567',
              }}
            />

            {/* Action Bar */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }}>
              <Space size={6}>
                <Button
                  type="primary"
                  size="small"
                  icon={<PlayCircleOutlined />}
                  loading={probeExecuting}
                  onClick={() => void runProbeSql()}
                >
                  执行探针
                </Button>
                <Button
                  size="small"
                  onClick={() => {
                    setProbeSql('');
                    setProbeResult(null);
                    setProbeError(null);
                  }}
                >
                  清空
                </Button>
              </Space>
              {probeDuration !== null && (
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  耗时 {probeDuration}ms {probeResult ? `· ${probeResult.length} 行` : ''}
                </Typography.Text>
              )}
            </div>

            {/* Result Area */}
            {probeError && (
              <Alert
                type="error"
                showIcon
                message="执行失败"
                description={<pre style={{ margin: 0, fontSize: 11, whiteSpace: 'pre-wrap', maxHeight: 120, overflow: 'auto' }}>{probeError}</pre>}
                style={{ marginTop: 8 }}
              />
            )}

            {probeResult !== null && (
              <div style={{ marginTop: 8 }}>
                {probeResult.length === 0 ? (
                  <div style={{ padding: '6px 8px', background: 'rgba(255,255,255,0.03)', borderRadius: 4, fontSize: 12, color: '#94a3b8' }}>
                    ✓ 执行成功，返回 0 行记录
                  </div>
                ) : (
                  <div className="probe-result-table-wrapper">
                    <table className="probe-result-table">
                      <thead>
                        <tr>
                          {Object.keys(probeResult[0] || {}).map((col) => (
                            <th key={col}>{col}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {probeResult.slice(0, 50).map((row, idx) => (
                          <tr key={idx}>
                            {Object.keys(probeResult[0] || {}).map((col) => {
                              const val = row[col];
                              const str = val === null ? 'NULL' : typeof val === 'object' ? JSON.stringify(val) : String(val);
                              return (
                                <td key={col} className={val === null ? 'cell-null' : ''} title={str}>
                                  {str}
                                </td>
                              );
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {probeResult.length > 50 && (
                      <div style={{ fontSize: 11, color: '#94a3b8', padding: '4px 6px', textAlign: 'center' }}>
                        仅展示前 50 行，共 {probeResult.length} 行
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Key Metadata Table */}
          <div>
            <Typography.Title level={5} style={{ marginBottom: 10 }}>节点元数据</Typography.Title>
            <dl className="node-details">
              <dt>节点标识</dt><dd><code>{selectedNode.id}</code></dd>
              <dt>监听地址</dt><dd><code>{selectedNode.host}:{selectedNode.port}</code></dd>
              <dt>所属集群</dt><dd>{selectedNode.group || '未分组'}</dd>
              <dt>节点角色</dt><dd>{selectedNode.role === 'primary' ? '主写入库 (Primary)' : '流复制从库 (Standby)'}</dd>
              <dt>主守护进程 PID</dt>
              <dd>
                <Tooltip title="PostgreSQL 根守护进程 (postmaster/pgmaster) OS PID。当节点重启后，PID 会变更。">
                  <code>
                    {observed?.[selectedNode.id]?.pid != null
                      ? `pgmaster: ${observed[selectedNode.id].pid}`
                      : (observed?.[selectedNode.id]?.running === false ? '已停止 (未运行)' : '未探测 / --')}
                  </code>
                </Tooltip>
              </dd>
              <dt>运行状态</dt>
              <dd>
                {observed?.[selectedNode.id]?.running === true ? (
                  <Tag color="success">运行中</Tag>
                ) : observed?.[selectedNode.id]?.running === false ? (
                  <Tag color="error">已停止</Tag>
                ) : (
                  <Tag>未探测</Tag>
                )}
              </dd>
              <dt>数据库版本</dt><dd title={selectedNode.database_version}>{selectedNode.server_version || '未读取（实例不可达）'}</dd>
              <dt>MMR 插件版本</dt><dd>{selectedNode.extension_versions?.fdd_mmr || '未读取 / 未安装'}</dd>
              <dt>数据目录</dt><dd><code style={{ fontSize: 11 }}>{selectedNode.data_dir}</code></dd>
              {(() => {
                const configured = selectedNode.extensions || [];
                const installed = selectedNode.installed_extensions?.length
                  ? selectedNode.installed_extensions : configured;
                return installed.length > 0 && (
                  <>
                    <dt>已安装扩展</dt>
                    <dd>
                      <Space size={4} wrap>
                        {installed.map((ext) => (
                          <Tag key={ext} color="purple">{ext}</Tag>
                        ))}
                      </Space>
                      {!selectedNode.installed_extensions?.length ? (
                        <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 4 }}>
                          实例未连通，显示部署配置声明的扩展
                        </Typography.Text>
                      ) : null}
                    </dd>
                  </>
                );
              })()}
            </dl>
          </div>

          {/* Single Node Operations */}
          <div>
            <Typography.Title level={5} style={{ marginBottom: 10 }}>单节点运维动作</Typography.Title>
            <Space wrap>
              {(['start', 'stop', 'restart'] as const).map((name) => {
                const action = actions.find((item) => item.id === `deployment.${name}`);
                return action && (
                  <Button
                    key={name}
                    danger={name === 'stop'}
                    type={name === 'start' ? 'primary' : 'default'}
                    disabled={loading || roleBusy}
                    onClick={() => void onRunAction(action, selectedNode.id)}
                  >
                    {name === 'start' ? '启动节点' : name === 'stop' ? '停止节点' : '重启节点'}
                  </Button>
                );
              })}
              {selectedNode.group && selectedNode.role === 'standby' && actions.some(item => item.id === 'deployment.failover') && <>
                <Tooltip title={observed?.[selectedNode.id]?.running !== true ? '该节点未确认运行，请先启动并确认备库角色' : '停止当前主库，只提升选中的备库；旧主保持停止'}>
                  <Button disabled={loading || roleBusy || observed?.[selectedNode.id]?.running !== true}
                    onClick={() => void onRunRole('deployment.failover')}>备库升主</Button>
                </Tooltip>
                <Tooltip title={observed?.[selectedNode.id]?.running !== true ? '该节点未确认运行，请先启动并确认备库角色' : '停止当前主库，提升选中的备库，再重建旧主为备库'}>
                  <Button disabled={loading || roleBusy || observed?.[selectedNode.id]?.running !== true}
                    onClick={() => void onRunRole('deployment.switchover')}>主备切换</Button>
                </Tooltip>
              </>}
              {selectedNode.group && selectedNode.previous_primary === selectedNode.id && selectedNode.rejoin_required && actions.some(item => item.id === 'deployment.rejoin') && <Tooltip title="从当前主库重新备份，替换旧主的数据目录并以备库启动">
                <Button disabled={loading || roleBusy} onClick={() => void onRunRole('deployment.rejoin')}>重建旧主为备库</Button>
              </Tooltip>}
              {selectedNode.group && selectedNode.original_primary === selectedNode.id && selectedNode.role === 'standby' && actions.some(item => item.id === 'deployment.restore') && <Tooltip title={observed?.[selectedNode.id]?.running !== true ? '请先将旧主重建为备库并启动，再执行回切' : '将原主库重新提升为主库，并重建当前主库为备库'}>
                <Button disabled={loading || roleBusy || observed?.[selectedNode.id]?.running !== true}
                  onClick={() => void onRunRole('deployment.restore')}>切回原主库</Button>
              </Tooltip>}
            </Space>
          </div>
        </div>
      )}
    </Drawer>
  );
}
