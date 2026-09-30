import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Form, Input, InputNumber, Radio, Select, Space, Tag } from 'antd';
import {
  ReactFlow, Background, Controls, MiniMap,
  applyNodeChanges, type Edge, type Node as RFNode, type NodeChange, type NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

export type FreeNode = { name: string; host: string; port: number; data_dir: string; role: 'primary' | 'standby' };
export type FreeHost = { name: string; address: string };

type FlowData = { name: string; role: string; port: number; host: string; label: string };

function toFlow(nodes: FreeNode[]): RFNode<FlowData>[] {
  const standbys = nodes.filter((node) => node.role !== 'primary');
  return nodes.map((node) => {
    const index = standbys.indexOf(node);
    const angle = (2 * Math.PI * index) / Math.max(1, standbys.length);
    return {
      id: node.name,
      position: node.role === 'primary'
        ? { x: 320, y: 240 }
        : { x: 320 + 260 * Math.cos(angle), y: 240 + 180 * Math.sin(angle) },
      data: {
        name: node.name, role: node.role, port: node.port, host: node.host,
        label: `${node.name} · ${node.role === 'primary' ? '主' : '备'} · :${node.port}`,
      },
      style: {
        border: `2px solid ${node.role === 'primary' ? '#d4380d' : '#1677ff'}`,
        borderRadius: 8, padding: '6px 10px', background: '#fff', width: 170, fontSize: 12,
      },
    };
  });
}

function deriveEdges(nodes: FreeNode[]): Edge[] {
  const primary = nodes.find((node) => node.role === 'primary');
  if (!primary) return [];
  return nodes
    .filter((node) => node.role === 'standby')
    .map((node) => ({
      id: `${primary.name}->${node.name}`,
      source: primary.name,
      target: node.name,
      animated: true,
      label: '流复制',
      style: { stroke: '#1677ff' },
    }));
}

export default function FreeTopologyEditor({ nodes, hosts, basePort, dataRoot, onChange }: {
  nodes: FreeNode[]; hosts: FreeHost[]; basePort: number; dataRoot: string;
  onChange: (nodes: FreeNode[]) => void;
}) {
  const [flowNodes, setFlowNodes] = useState<RFNode<FlowData>[]>(() => toFlow(nodes));
  const [selected, setSelected] = useState<string | null>(null);

  const sync = useCallback((next: FreeNode[]) => {
    onChange(next);
    setFlowNodes((prev) => {
      const positioned = new Map(prev.map((node) => [node.id, node.position]));
      return toFlow(next).map((node) => ({ ...node, position: positioned.get(node.id) || node.position }));
    });
  }, [onChange]);

  useEffect(() => {
    setFlowNodes((prev) => {
      const positioned = new Map(prev.map((node) => [node.id, node.position]));
      return toFlow(nodes).map((node) => ({ ...node, position: positioned.get(node.id) || node.position }));
    });
    setSelected((prev) => (prev && nodes.some((node) => node.name === prev) ? prev : null));
  }, [nodes]);

  const onNodesChange = useCallback((changes: NodeChange<RFNode<FlowData>>[]) => {
    setFlowNodes((prev) => applyNodeChanges(changes, prev));
    const removed = changes.filter((c) => c.type === 'remove').map((c) => c.id);
    if (removed.length) {
      onChange(nodes.filter((node) => !removed.includes(node.name)));
      setSelected((prev) => (prev && removed.includes(prev) ? null : prev));
    }
  }, [nodes, onChange]);

  const edges = useMemo(() => deriveEdges(nodes), [nodes]);
  const current = nodes.find((node) => node.name === selected) || null;

  const addNode = (role: 'primary' | 'standby') => {
    let index = nodes.length + 1;
    while (nodes.some((node) => node.name === `node${index}`)) index += 1;
    if (role === 'primary' && nodes.some((node) => node.role === 'primary')) role = 'standby';
    sync([...nodes, {
      name: `node${index}`, host: '', port: basePort + index - 1,
      data_dir: dataRoot ? `${dataRoot.replace(/\/$/, '')}/node${index}` : '', role,
    }]);
  };

  const patch = (name: string, fields: Partial<FreeNode>) => {
    let next = nodes.map((node) => (node.name === name ? { ...node, ...fields } : node));
    if (fields.role === 'primary') {
      next = next.map((node) => (node.name !== name && node.role === 'primary' ? { ...node, role: 'standby' } : node));
    }
    if (fields.name && fields.name !== name) setSelected(fields.name);
    sync(next);
  };

  const onNodeClick: NodeMouseHandler = (_event, node) => setSelected(node.id);

  return <>
    <Alert type="info" style={{ marginBottom: 8 }}
      message="自由拓扑：一个流复制集群——恰好一个主节点，其余为备库。拖动摆放节点，点选后在右侧编辑端口/目录/主机。" />
    <div style={{ display: 'flex', gap: 12 }}>
      <div style={{ flex: 1, height: 420, border: '1px solid #d9d9d9', borderRadius: 8, overflow: 'hidden' }}>
        <ReactFlow
          nodes={flowNodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onNodeClick={onNodeClick}
          onPaneClick={() => setSelected(null)}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background />
          <Controls />
          <MiniMap pannable />
        </ReactFlow>
      </div>
      <div style={{ width: 300 }}>
        <Space wrap style={{ marginBottom: 12 }}>
          <Button size="small" onClick={() => addNode('primary')}>添加主节点</Button>
          <Button size="small" onClick={() => addNode('standby')}>添加备库</Button>
        </Space>
        {current ? (
          <Form layout="vertical" size="small">
            <Form.Item label="节点名"><Input value={current.name} onChange={(e) => patch(current.name, { name: e.target.value })} /></Form.Item>
            <Form.Item label="角色"><Radio.Group value={current.role} onChange={(e) => patch(current.name, { role: e.target.value })}
              options={[{ value: 'primary', label: '主节点' }, { value: 'standby', label: '备库' }]} /></Form.Item>
            <Form.Item label="端口"><InputNumber style={{ width: '100%' }} value={current.port} min={1024} max={65535}
              onChange={(port) => patch(current.name, { port: port || 1024 })} /></Form.Item>
            <Form.Item label="数据目录"><Input value={current.data_dir} placeholder="/data/node1" onChange={(e) => patch(current.name, { data_dir: e.target.value })} /></Form.Item>
            {hosts.length > 0 && <Form.Item label="主机"><Select value={current.host || undefined} allowClear placeholder="默认主机"
              options={hosts.map((host) => ({ value: host.name, label: `${host.name} · ${host.address}` }))}
              onChange={(host) => patch(current.name, { host: host || '' })} /></Form.Item>}
            <Button danger size="small" onClick={() => { sync(nodes.filter((node) => node.name !== current.name)); setSelected(null); }}>删除节点</Button>
          </Form>
        ) : (
          <div style={{ color: '#999' }}>点选节点编辑；连线展示复制关系（由角色决定）。当前 {nodes.length} 个节点：
            {nodes.map((node) => <Tag key={node.name} color={node.role === 'primary' ? 'volcano' : 'blue'}>{node.name}</Tag>)}
          </div>
        )}
      </div>
    </div>
  </>;
}
