import { useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Empty, Modal, Segmented, Select, Space, Tabs, Tag, Typography } from 'antd';
import { PauseOutlined, PlayCircleOutlined, StepBackwardOutlined, StepForwardOutlined } from '@ant-design/icons';

import { api, statusColor } from '../../api';
import CodeEditor from '../../components/LazyCodeEditor';
import LogViewer from '../../components/LogViewer';
import type { TopologyData, TopologyNode } from '../../components/ThreeTopologyView';
import Topology2D from './Topology2D';
import ReferenceThreeTopology from './ReferenceThreeTopology';
import type { RegressionTopology } from './topologyModel';

type ReportStep = {
  title: string;
  status: string;
  action?: string;
  command?: string;
  expected?: string;
  actual?: string;
  evidence?: string;
  state_table?: string;
};
type ParsedReport = {
  status: string;
  reason: string;
  start_time: string;
  end_time: string;
  steps: ReportStep[];
  assertions: ReportStep[];
  key_config: string;
  purpose?: string;
  test_contents?: string[];
  backtrace?: string;
  topology: RegressionTopology | null;
};
type Artifact = {
  available: boolean;
  target: string;
  report: string | null;
  summary: { status?: string; reason?: string } | null;
  steps?: any[];
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
  const [topologyMode, setTopologyMode] = useState<'3d' | '2d' | 'cards'>('3d');
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    if (!target) return;
    let cancelled = false;
    setArtifact(null);
    setSelectedStep(0);
    setPlaying(false);
    setSelectedLog('');
    setLogLines([]);
    setTopologyMode('3d');
    setPlaybackSpeed(1);
    setSelectedNode(null);
    setDetailsOpen(false);
    setFullscreen(false);
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
    const timer = window.setTimeout(() => setSelectedStep((value) => value + 1), 2800 / playbackSpeed);
    return () => window.clearTimeout(timer);
  }, [playing, selectedStep, snapshots.length, playbackSpeed]);

  const currentClusters = snapshots[selectedStep]?.clusters || artifact?.parsed?.topology?.clusters || [];

  const threeTopology = useMemo<TopologyData | null>(() => {
    const clusters = snapshots[selectedStep]?.clusters || artifact?.parsed?.topology?.clusters || [];
    if (!clusters.length) return null;
    const nodes = clusters.flatMap((cluster) => cluster.nodes.map((node) => ({
      id: node.name, label: node.label || node.name, host: node.host,
      port: Number(node.port) || 0,
      role: node.role.toLowerCase() === 'primary' ? 'primary' : 'standby',
      group: cluster.name, data_dir: '',
    })));
    const edges = clusters.flatMap((cluster) => cluster.nodes.slice(1).map((node) => ({
      id: `${cluster.name}:${node.name}`, source: cluster.nodes[0].name,
      target: node.name, kind: 'streaming',
    })));
    return { target: target || '', kind: 'report', nodes, edges };
  }, [artifact, selectedStep, snapshots, target]);

  const observed = useMemo(() => {
    const clusters = snapshots[selectedStep]?.clusters || artifact?.parsed?.topology?.clusters || [];
    return Object.fromEntries(clusters.flatMap((cluster) => cluster.nodes.map((node) => [
      node.name,
      { running: node.state.toUpperCase() === 'ACTIVE', message: node.state },
    ])));
  }, [artifact, selectedStep, snapshots]);

  const parsed = artifact?.parsed;
  const status = artifact?.summary?.status || parsed?.status || 'UNKNOWN';

  const displaySteps = useMemo(() => {
    const list = parsed?.steps ? [...parsed.steps] : [];
    if (status === 'FAIL' && !list.some((s) => s.status === 'FAIL')) {
      const journalSteps = artifact?.steps || [];
      const failed = journalSteps.filter((js: any) => js.result === 'FAIL' || js.status === 'FAIL');
      if (failed.length > 0) {
        failed.forEach((js: any, idx: number) => {
          let cmd = '';
          let stateTable = '';
          if (Array.isArray(js.execution)) {
            for (const ex of js.execution) {
              if (ex?.text) {
                const text = String(ex.text);
                if (text.includes('\n\n')) {
                  const [c, ...rest] = text.split('\n\n');
                  cmd = c;
                  stateTable = rest.join('\n\n');
                } else if (text.startsWith('$')) {
                  cmd = text;
                } else {
                  stateTable = text;
                }
              }
            }
          }
          list.push({
            title: js.title || `步骤 ${list.length + idx + 1}: 断言失败`,
            status: 'FAIL',
            action: '',
            command: cmd,
            expected: js.expected || '',
            actual: js.actual || parsed?.reason || '执行未达到预期',
            evidence: '',
            state_table: stateTable,
          });
        });
      } else if (parsed?.reason) {
        list.push({
          title: `步骤 ${list.length + 1}: 断言失败中断`,
          status: 'FAIL',
          action: '',
          command: '',
          expected: '全部步骤执行通过',
          actual: parsed.reason,
          evidence: '',
          state_table: '',
        });
      }
    }
    return list;
  }, [parsed, status, artifact]);

  const displayAssertions = useMemo(() => {
    const list = parsed?.assertions ? [...parsed.assertions] : [];
    if (status === 'FAIL' && !list.some((a) => a.status === 'FAIL')) {
      list.push({
        title: '用例终态断言',
        status: 'FAIL',
        expected: '用例全部断言通过',
        actual: parsed?.reason || '测试未通过',
      });
    }
    return list;
  }, [parsed, status]);

  return <Modal open={!!target} title={<Space><span>{target || '测试报告'}</span><Tag color={statusColor(status)}>{status}</Tag><Button size="small" onClick={() => setDetailsOpen(!detailsOpen)}>ⓘ 详情</Button><Button size="small" onClick={() => setFullscreen(!fullscreen)}>{fullscreen ? '退出全屏' : '全屏'}</Button></Space>}
    onCancel={onClose} footer={null} width={fullscreen ? '100vw' : 'min(1380px, 96vw)'}
    className={`regress-report-modal${fullscreen ? ' report-fullscreen' : ''}`} destroyOnHidden>
    {!artifact ? <Empty description="正在读取测试报告" /> : !artifact.available ? <Empty description="该用例尚无报告" /> : <>
      {detailsOpen && <div className="report-header"><Typography.Text>{parsed?.purpose || target}</Typography.Text><Typography.Text type="secondary">开始：{parsed?.start_time || '-'} · 结束：{parsed?.end_time || '-'}</Typography.Text>{parsed?.reason && <Typography.Text>{parsed.reason}</Typography.Text>}</div>}
      <Tabs items={[
        { key: 'steps', label: `交互步骤详情 (${displaySteps.length})`, children: displaySteps.length ? <div className="report-steps">{displaySteps.map((step, index) => <section className={`report-step ${step.status === 'FAIL' ? 'fail' : 'pass'}`} key={index}>
          <div className="report-step-header"><strong>{step.title}</strong><Tag color={statusColor(step.status)}>{step.status}</Tag></div>
          {step.action && <div className="report-step-field"><span>动作</span><p>{step.action}</p></div>}
          {step.command && <div className="report-step-field"><span>执行命令</span><pre>{step.command}</pre></div>}
          {step.expected && <div className="report-step-field"><span>预期结果</span><p>{step.expected}</p></div>}
          {step.actual && <div className="report-step-field"><span>实际结果</span><p>{step.actual}</p></div>}
          {step.evidence && <div className="report-step-field"><span>证据</span><p>{step.evidence}</p></div>}
          {step.state_table && <div className="report-step-field"><span>中间状态</span><pre className="state-table-block">{step.state_table}</pre></div>}
        </section>)}</div> : <Empty description="报告中没有结构化步骤" /> },
        { key: 'checks', label: `检测项断言 (${displayAssertions.length})`, children: displayAssertions.length ? <div className="report-steps">{displayAssertions.map((check, index) => <section className={`report-step ${check.status === 'FAIL' ? 'fail' : 'pass'}`} key={index}>
          <Space><Typography.Text strong>{check.title}</Typography.Text><Tag color={statusColor(check.status)}>{check.status}</Tag></Space>
          <p><b>预期：</b>{check.expected || '-'}</p><p><b>实际：</b>{check.actual || '-'}</p>
        </section>)}</div> : <Empty description="报告中没有独立检测项" /> },
        ...(currentClusters.length ? [{ key: 'topology', label: '🪐 架构拓扑看板', children: <>
          <Alert type="info" showIcon className="report-source-note" message="拓扑快照部分由报告步骤推演；实测状态以原始命令和日志为准。" />
          <div className="report-topology-toolbar"><Segmented value={topologyMode} onChange={(value) => setTopologyMode(value as typeof topologyMode)} options={[{ label: '3D 全息拓扑', value: '3d' }, { label: '2D 架构图', value: '2d' }, { label: '详细节点卡片', value: 'cards' }]} /></div>
          {topologyMode === '3d' && parsed?.topology && <ReferenceThreeTopology topology={parsed.topology}
            onSelect={(name) => setSelectedNode(threeTopology?.nodes.find((node) => node.id === name) || null)} />}
          {topologyMode === '2d' && parsed?.topology && <Topology2D topology={parsed.topology}
            position={selectedStep} playing={playing} speed={playbackSpeed}
            onSelectStep={(index) => { setPlaying(false); setSelectedStep(index); }}
            onTogglePlay={() => { if (selectedStep === snapshots.length - 1) setSelectedStep(0); setPlaying(!playing); }}
            onSpeed={setPlaybackSpeed}
            selected={selectedNode?.id || null} onSelect={(name) => setSelectedNode(threeTopology?.nodes.find((node) => node.id === name) || null)} />}
          {topologyMode === 'cards' && <div className="report-node-grid">{threeTopology?.nodes.map((node) => <div className="report-node-card" key={node.id}><strong>{node.label}</strong><span>{node.group} · {node.role}</span><span>{node.host}:{node.port}</span><Tag color={observed[node.id]?.running ? 'success' : 'warning'}>{observed[node.id]?.message || '未知'}</Tag></div>)}</div>}
          {topologyMode === '3d' && selectedNode && <div className="report-node-inspector"><strong>{selectedNode.label}</strong><span>{selectedNode.group} · {selectedNode.role} · {selectedNode.host}:{selectedNode.port}</span><Tag color={observed[selectedNode.id]?.running ? 'success' : 'warning'}>{observed[selectedNode.id]?.message || '未知'}</Tag></div>}
          {topologyMode !== '2d' && snapshots.length > 0 && <div className="replay-controls">
            <Button icon={<StepBackwardOutlined />} onClick={() => { setPlaying(false); setSelectedStep((value) => Math.max(0, value - 1)); }} disabled={selectedStep === 0} aria-label="上一步" />
            <Button type="primary" icon={playing ? <PauseOutlined /> : <PlayCircleOutlined />} onClick={() => { if (selectedStep === snapshots.length - 1) setSelectedStep(0); setPlaying(!playing); }}>{playing ? '暂停' : '播放'}</Button>
            <Button icon={<StepForwardOutlined />} onClick={() => { setPlaying(false); setSelectedStep((value) => Math.min(snapshots.length - 1, value + 1)); }} disabled={selectedStep >= snapshots.length - 1} aria-label="下一步" />
            <Typography.Text type="secondary">{selectedStep + 1} / {snapshots.length} · {snapshots[selectedStep]?.title}</Typography.Text>
          </div>}
        </> }] : []),
        ...(parsed?.backtrace ? [{ key: 'backtrace', label: '🚨 GDB 崩溃调用栈', children: <pre className="raw-report">{parsed.backtrace}</pre> }] : []),
        { key: 'overview', label: '用例设计与配置', children: <div className="report-steps"><section className="report-step"><strong>验证目的</strong><p>{parsed?.purpose || '未提供'}</p></section><section className="report-step"><strong>测试内容</strong>{parsed?.test_contents?.length ? parsed.test_contents.map((item, index) => <p key={index}>{item}</p>) : <p>未提供</p>}</section><section className="report-step"><strong>关键配置</strong>{parsed?.key_config ? <CodeEditor value={parsed.key_config} language="ini" readOnly height={360} /> : <p>未提供</p>}</section></div> },
        { key: 'raw', label: '原始报告 (report.txt)', children: <pre className="raw-report">{artifact.report}</pre> },
        { key: 'logs', label: '运行时日志', children: <><Select value={selectedLog || undefined} onChange={setSelectedLog} style={{ minWidth: 280, marginBottom: 14 }} placeholder="选择原始日志" options={artifact.logs.map((item) => ({ label: item.name, value: item.name }))} /><LogViewer lines={logLines} filename={selectedLog} /></> },
      ].sort((a, b) => {
        const order = ['steps', 'topology', 'backtrace', 'checks', 'overview', 'raw', 'logs'];
        return order.indexOf(a.key) - order.indexOf(b.key);
      })} />
    </>}
  </Modal>;
}
