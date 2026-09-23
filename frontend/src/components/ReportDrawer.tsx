import { useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Drawer, Empty, Select, Space, Tabs, Tag, Typography } from 'antd';
import { PauseOutlined, PlayCircleOutlined, StepBackwardOutlined, StepForwardOutlined } from '@ant-design/icons';
import { ReactFlow, Background, Controls, type Edge, type Node } from '@xyflow/react';

import { api, statusColor } from '../api';
import CodeEditor from './LazyCodeEditor';
import LogViewer from './LogViewer';

type ReportStep = { title: string; status: string; command?: string; expected?: string; actual?: string; evidence?: string };
type ClusterNode = { name: string; label?: string; host: string; port: string; role: string; state: string; cluster: string };
type Cluster = { name: string; state: string; nodes: ClusterNode[] };
type Snapshot = { title: string; step_num: number; clusters: Cluster[]; event_desc?: string };
type ParsedReport = {
  status: string;
  reason: string;
  start_time: string;
  end_time: string;
  steps: ReportStep[];
  assertions: ReportStep[];
  key_config: string;
  topology: { clusters: Cluster[]; step_snapshots: Snapshot[] } | null;
};
type Artifact = {
  available: boolean;
  target: string;
  report: string | null;
  summary: { status?: string; reason?: string } | null;
  parsed: ParsedReport | null;
  logs: { name: string; size: number }[];
};

export default function ReportDrawer({ target, environmentId, onClose }: { target: string | null; environmentId?: string; onClose: () => void }) {
  const { message } = App.useApp();
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [selectedStep, setSelectedStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selectedLog, setSelectedLog] = useState('');
  const [logLines, setLogLines] = useState<string[]>([]);

  useEffect(() => {
    if (!target) return;
    let cancelled = false;
    setArtifact(null);
    setSelectedStep(0);
    setPlaying(false);
    setSelectedLog('');
    setLogLines([]);
    void api<Artifact>(`/fbasecman/cases/${encodeURIComponent(target)}/artifacts${environmentId ? `?environment_id=${encodeURIComponent(environmentId)}` : ''}`).then((value) => {
      if (!cancelled) { setArtifact(value); setSelectedLog(value.logs[0]?.name || ''); }
    }).catch((error) => { if (!cancelled) message.error(error.message); });
    return () => { cancelled = true; };
  }, [target, environmentId, message]);

  useEffect(() => {
    if (!target || !selectedLog) return;
    let cancelled = false;
    void api<{ lines: string[] }>(`/fbasecman/cases/${encodeURIComponent(target)}/logs?filename=${encodeURIComponent(selectedLog)}&last_lines=1000${environmentId ? `&environment_id=${encodeURIComponent(environmentId)}` : ''}`)
      .then((value) => { if (!cancelled) setLogLines(value.lines); })
      .catch((error) => { if (!cancelled) message.error(error.message); });
    return () => { cancelled = true; };
  }, [target, environmentId, selectedLog, message]);

  const snapshots = artifact?.parsed?.topology?.step_snapshots || [];
  useEffect(() => {
    if (!playing || !snapshots.length) return;
    if (selectedStep >= snapshots.length - 1) { setPlaying(false); return; }
    const timer = window.setTimeout(() => setSelectedStep((value) => value + 1), 1150);
    return () => window.clearTimeout(timer);
  }, [playing, selectedStep, snapshots.length]);

  const topology = useMemo(() => {
    const clusters = snapshots[selectedStep]?.clusters || artifact?.parsed?.topology?.clusters || [];
    const nodes: Node[] = [];
    const edges: Edge[] = [];
    clusters.forEach((cluster, column) => {
      cluster.nodes.forEach((item, row) => {
        nodes.push({
          id: item.name,
          position: { x: column * 300, y: row * 98 },
          data: { label: <div className="topo-node-content"><strong>{item.name}</strong><small>{item.host}:{item.port}</small><span>{item.role} · {item.state}</span></div> },
          style: { width: 220, borderRadius: 5, background: '#fff', color: '#263832', border: item.state === 'ACTIVE' ? '1px solid #4b9a7b' : '1px solid #c88956' },
        });
        if (row > 0) edges.push({ id: `${cluster.name}:${item.name}`, source: cluster.nodes[0].name, target: item.name, style: { stroke: '#9bb5aa' } });
      });
    });
    return { nodes, edges };
  }, [artifact, selectedStep, snapshots]);

  const parsed = artifact?.parsed;
  const status = artifact?.summary?.status || parsed?.status || 'UNKNOWN';
  return <Drawer open={!!target} title={target || '测试报告'} onClose={onClose} width="min(1120px, 96vw)" destroyOnHidden>
    {!artifact ? <Empty description="正在读取测试报告" /> : !artifact.available ? <Empty description="该用例尚无报告" /> : <>
      <div className="report-header">
        <Space><Tag color={statusColor(status)}>{status}</Tag><Typography.Text type="secondary">{parsed?.start_time || ''} {parsed?.end_time ? `→ ${parsed.end_time}` : ''}</Typography.Text></Space>
        {parsed?.reason && <Typography.Text>{parsed.reason}</Typography.Text>}
      </div>
      <Alert type="info" showIcon className="report-source-note" message="来源：当前环境最近一次用例报告" description="拓扑快照由报告解析重建，用于回看；实时执行状态请以任务事件与原始日志为准。" />
      <Tabs items={[
        { key: 'steps', label: `步骤 ${parsed?.steps.length || 0}`, children: parsed?.steps.length ? <div className="report-steps">{parsed.steps.map((step, index) => <section className="report-step" key={index}>
          <Space><Typography.Text strong>{step.title}</Typography.Text><Tag color={statusColor(step.status)}>{step.status}</Tag></Space>
          {step.command && <pre>{step.command}</pre>}
          {step.expected && <p><b>预期：</b>{step.expected}</p>}
          {step.actual && <p><b>实际：</b>{step.actual}</p>}
          {step.evidence && <p><b>证据：</b>{step.evidence}</p>}
        </section>)}</div> : <Empty description="报告中没有结构化步骤" /> },
        { key: 'checks', label: `检测项 ${parsed?.assertions.length || 0}`, children: parsed?.assertions.length ? <div className="report-steps">{parsed.assertions.map((check, index) => <section className="report-step" key={index}>
          <Space><Typography.Text strong>{check.title}</Typography.Text><Tag color={statusColor(check.status)}>{check.status}</Tag></Space>
          <p><b>预期：</b>{check.expected || '-'}</p><p><b>实际：</b>{check.actual || '-'}</p>
        </section>)}</div> : <Empty description="报告中没有独立检测项" /> },
        { key: 'topology', label: '拓扑回看', children: topology.nodes.length ? <>
          <div className="deployment-flow report-flow"><ReactFlow nodes={topology.nodes} edges={topology.edges} fitView nodesDraggable={false} nodesConnectable={false} proOptions={{ hideAttribution: true }}><Controls showInteractive={false} /><Background gap={24} color="#e6edeb" /></ReactFlow></div>
          {snapshots.length > 0 && <div className="replay-controls">
            <Button icon={<StepBackwardOutlined />} onClick={() => { setPlaying(false); setSelectedStep((value) => Math.max(0, value - 1)); }} disabled={selectedStep === 0} aria-label="上一步" />
            <Button type="primary" icon={playing ? <PauseOutlined /> : <PlayCircleOutlined />} onClick={() => { if (selectedStep === snapshots.length - 1) setSelectedStep(0); setPlaying(!playing); }}>{playing ? '暂停' : '播放'}</Button>
            <Button icon={<StepForwardOutlined />} onClick={() => { setPlaying(false); setSelectedStep((value) => Math.min(snapshots.length - 1, value + 1)); }} disabled={selectedStep >= snapshots.length - 1} aria-label="下一步" />
            <Typography.Text type="secondary">{selectedStep + 1} / {snapshots.length} · {snapshots[selectedStep]?.title}</Typography.Text>
          </div>}
        </> : <Empty description="本用例没有拓扑快照" /> },
        { key: 'logs', label: `日志 ${artifact.logs.length}`, children: <><Select value={selectedLog || undefined} onChange={setSelectedLog} style={{ minWidth: 280, marginBottom: 14 }} placeholder="选择原始日志" options={artifact.logs.map((item) => ({ label: item.name, value: item.name }))} /><LogViewer lines={logLines} filename={selectedLog} /></> },
        { key: 'config', label: '配置', children: parsed?.key_config ? <CodeEditor value={parsed.key_config} language="ini" readOnly height={420} /> : <Empty description="报告没有配置快照" /> },
        { key: 'raw', label: '原始报告', children: <pre className="raw-report">{artifact.report}</pre> },
      ]} />
    </>}
  </Drawer>;
}
