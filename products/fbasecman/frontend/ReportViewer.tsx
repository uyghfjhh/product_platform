import { useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Empty, Modal, Segmented, Select, Space, Tabs, Tag, Typography } from 'antd';
import { PauseOutlined, PlayCircleOutlined, StepBackwardOutlined, StepForwardOutlined } from '@ant-design/icons';

import { api, statusColor } from '../../../frontend/src/platform/api';
import CodeEditor from '../../../frontend/src/components/LazyCodeEditor';
import LogViewer from '../../../frontend/src/components/LogViewer';
import type { TopologyData, TopologyNode } from '../../../frontend/src/platform/topology';
import Topology2D from './Topology2D';
import ReferenceThreeTopology from './ReferenceThreeTopology';
import type { RegressionTopology } from './topologyModel';
import ReportEvidenceSteps from './ReportEvidenceSteps';

type ReportStep = {
  title: string;
  status: string;
  action?: string;
  command?: string;
  expected?: string;
  actual?: string;
  actualSummary?: string;
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
  purpose_source?: string;
  execution_scope?: {connection:string;transactions:{stage:string;operation:string;boundary:string}[];conclusion:string};
  execution_scope_source?: string;
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
    setFullscreen(false);
    void api<Artifact>(`/fbasecman/cases/${encodeURIComponent(target)}/artifacts${environmentId ? `?environment_id=${encodeURIComponent(environmentId)}` : ''}`).then((value) => {
      if (!cancelled) {
        setArtifact(value);
        const preferred = value.logs.find((item) => item.name === 'fbasecman.log') || value.logs[0];
        setSelectedLog(preferred?.name || '');
      }
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

  return <Modal open={!!target} title={<Space><span>{target || '测试报告'}</span><Tag color={statusColor(status)}>{status}</Tag><Button size="small" onClick={() => setFullscreen(!fullscreen)}>{fullscreen ? '退出全屏' : '全屏'}</Button></Space>}
    onCancel={onClose} footer={null} width={fullscreen ? '100vw' : 'min(1380px, 96vw)'}
    className={`regress-report-modal${fullscreen ? ' report-fullscreen' : ''}`} destroyOnHidden>
    {!artifact ? <Empty description="正在读取测试报告" /> : !artifact.available ? <Empty description="该用例尚无报告" /> : <>
      {<div className="report-header"><Typography.Text strong>测试目的</Typography.Text><Typography.Text>{parsed?.purpose || '本次未记录测试目的'}</Typography.Text>{parsed?.purpose_source === 'current_catalog' && <Typography.Text type="secondary">当前用例说明；本次执行结论以保存的断言和证据为准。</Typography.Text>}<Typography.Text type="secondary">开始：{parsed?.start_time || '-'} · 结束：{parsed?.end_time || '-'}</Typography.Text>{parsed?.reason && <Typography.Text>{parsed.reason}</Typography.Text>}</div>}
      {parsed?.execution_scope && <section className="cman-transaction-scope"><strong>连接与事务范围</strong><p>{parsed.execution_scope.connection}</p>{parsed.execution_scope_source === 'current_catalog' && <Typography.Text type="secondary">以下为当前用例设计说明；历史执行结论以当次证据为准。</Typography.Text>}<div style={{overflowX:'auto'}}><table className="case-description-table"><thead><tr><th>阶段</th><th>执行内容</th><th>事务结束方式</th></tr></thead><tbody>{parsed.execution_scope.transactions.map(row => <tr key={row.stage}><td>{row.stage}</td><td>{row.operation}</td><td>{row.boundary}</td></tr>)}</tbody></table></div><p>{parsed.execution_scope.conclusion}</p></section>}
      <Tabs items={[
        { key: 'steps', label: `验证结果与证据 (${displaySteps.length})`, children: <ReportEvidenceSteps evidenceBasePath={environmentId && target ? `/api/v1/environments/${encodeURIComponent(environmentId)}/results/${encodeURIComponent(target)}/evidence` : undefined} steps={[...displaySteps, ...displayAssertions.map(step => ({ ...step, intent: 'verify' }))]} /> },
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
        { key: 'overview', label: '用例设计与配置', children: <div className="report-steps"><section className="report-step"><strong>测试目的</strong><p>{parsed?.purpose || '未提供'}</p></section><section className="report-step"><strong>操作步骤与逐步预期</strong><div style={{overflowX:'auto'}}><table className="case-description-table"><thead><tr><th>类型</th><th>操作／检查</th><th>预期结果</th><th>实际结果摘要</th><th>状态</th></tr></thead><tbody>{displaySteps.map((s,i)=>{ const item = s as any; const type = item.intent === 'action' ? '执行动作' : item.intent === 'cleanup' ? '清理' : item.intent === 'verify' ? '业务验证' : item.title?.includes('配置') ? '配置比对' : '步骤'; return <tr key={i}><td>{type}</td><td>{s.title}</td><td>{s.expected || '当次未保存期望'}</td><td style={{whiteSpace:'pre-wrap',maxWidth:360}}>{s.actual || s.actualSummary || '当次未保存实际结果'}</td><td>{s.status}</td></tr>; })}</tbody></table></div></section><section className="report-step"><strong>测试内容</strong>{parsed?.test_contents?.length ? parsed.test_contents.map((item, index) => <p key={index}>{item}</p>) : <p>未提供</p>}</section><section className="report-step"><strong>关键配置</strong>{parsed?.key_config ? <details><summary>展开关键配置（完整内容）</summary><CodeEditor value={parsed.key_config} language="ini" readOnly height={360} /></details> : <p>未提供</p>}</section></div> },
        { key: 'raw', label: '原始报告（report.txt）', children: artifact.report !== null ? <pre className="raw-report">{artifact.report}</pre> : <Empty description="本次执行没有生成原始报告" /> },
        { key: 'logs', label: '诊断日志', children: <><Select value={selectedLog || undefined} onChange={setSelectedLog} style={{ minWidth: 280, marginBottom: 14 }} placeholder="选择原始日志" options={artifact.logs.map((item) => ({ label: item.name, value: item.name }))} /><LogViewer lines={logLines} filename={selectedLog} /></> },
      ].sort((a, b) => {
        const order = ['steps', 'topology', 'backtrace', 'checks', 'overview', 'raw', 'logs'];
        return order.indexOf(a.key) - order.indexOf(b.key);
      })} />
    </>}
  </Modal>;
}
