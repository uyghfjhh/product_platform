import { Suspense, lazy, useEffect, useState } from 'react';
import { Badge, Button, Empty, Select, Space, Typography } from 'antd';
import { CompressOutlined, ExpandOutlined, ReloadOutlined } from '@ant-design/icons';

import { api, type Environment } from '../platform/api';
import type { TopologyNode } from '../platform/topology';
import PlatformErrorBoundary from '../components/PlatformErrorBoundary';

const StudioPanel = lazy(() => import('../components/StudioPanel'));
type Props = {
  environment: Environment | undefined;
  environments?: Environment[];
  onSelectEnvironment?: (id: string) => void;
  initialNodeId?: string;
};
type NodeStatus = Record<string, { running: boolean | null }>;

function resetStudioNavigation() {
  window.history.replaceState(null, '', window.location.pathname + window.location.search);
}

export default function DatabasePage({ environment, environments = [], onSelectEnvironment, initialNodeId }: Props) {
  const [nodes, setNodes] = useState<TopologyNode[]>([]);
  const [status, setStatus] = useState<NodeStatus>({});
  const [selection, setSelection] = useState({ environmentId: '', nodeId: '' });
  const [fullscreen, setFullscreen] = useState(false);
  const [revision, setRevision] = useState(0);
  const nodeId = selection.environmentId === environment?.id ? selection.nodeId : initialNodeId || '';

  useEffect(() => {
    const controller = new AbortController();
    setNodes([]); setStatus({});
    setSelection({ environmentId: environment?.id || '', nodeId: initialNodeId || '' });
    if (environment) {
      const path = `/environments/${encodeURIComponent(environment.id)}`;
      void api<{ nodes: TopologyNode[] }>(`${path}/topology`, { signal: controller.signal })
        .then((data) => { if (!controller.signal.aborted) setNodes(data.nodes); }).catch(() => undefined);
      void api<NodeStatus>(`${path}/topology/status`, { signal: controller.signal })
        .then((data) => { if (!controller.signal.aborted) setStatus(data); }).catch(() => undefined);
    }
    return () => controller.abort();
  }, [environment?.id, initialNodeId]);

  useEffect(() => {
    if (!fullscreen) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) setFullscreen(false);
    };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, [fullscreen]);

  const targetOptions = environment ? [
    { value: '', label: `环境连接 · ${environment.host}:${environment.port}` },
    ...nodes.map((node) => ({ value: node.id, label: <Space size={6}>
      <Badge status={status[node.id]?.running === true ? 'success' : status[node.id]?.running === false ? 'error' : 'default'} />
      <span>{node.label} · {node.host}:{node.port} · {node.role}</span>
    </Space> })),
  ] : [];
  const scope = `${environment?.id || ''}:${nodeId}:${revision}`;

  return (
    <div className="database-manager">
      <div className={`database-studio${fullscreen ? ' database-studio-fullscreen' : ''}`}>
        <header className="database-studio-toolbar">
          <div className="database-studio-heading">
            <Typography.Title level={3}>数据库管理</Typography.Title>
            {environment && <Typography.Text type="secondary">{environment.database_name} · {environment.database_user}</Typography.Text>}
          </div>
          <div className="database-studio-controls">
            <Select aria-label="选择数据库环境" placeholder="选择数据库环境" value={environment?.id}
              options={environments.map((item) => ({ value: item.id, label: item.title }))}
              onChange={(id) => { resetStudioNavigation(); onSelectEnvironment?.(id); }} />
            <Select aria-label="选择目标实例" placeholder="选择目标实例" value={environment ? nodeId : undefined}
              disabled={!environment} options={targetOptions}
              onChange={(id) => { resetStudioNavigation(); setSelection({ environmentId: environment!.id, nodeId: id }); }} />
            <Button aria-label="刷新数据库工作区" icon={<ReloadOutlined />} disabled={!environment}
              onClick={() => setRevision((value) => value + 1)} />
            <Button icon={fullscreen ? <CompressOutlined /> : <ExpandOutlined />}
              onClick={() => setFullscreen((value) => !value)}>{fullscreen ? '退出全屏' : '全屏'}</Button>
          </div>
        </header>
        <div className="database-studio-content">
          {!environment ? <Empty description="选择环境后开始管理数据库" /> : (
            <PlatformErrorBoundary key={scope}>
              <Suspense fallback={<Empty description="正在加载数据库工作区…" />}>
                <StudioPanel environmentId={environment.id} nodeId={nodeId || undefined} />
              </Suspense>
            </PlatformErrorBoundary>
          )}
        </div>
      </div>
    </div>
  );
}
