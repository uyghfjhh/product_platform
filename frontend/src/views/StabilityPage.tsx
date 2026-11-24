import { useEffect, useState } from 'react';
import { Alert, Button, Card, Checkbox, Empty, InputNumber, Modal, Select, Space, Spin, Typography } from 'antd';
import { PlayCircleOutlined, ThunderboltOutlined } from '@ant-design/icons';

import { api, operationRequest, type Environment, type Product, type RegressionBinding } from '../platform/api';
import ExecutionTerminal from '../components/ExecutionTerminal';
import TestBindingBar from '../components/TestBindingBar';
import '../components/workloadWorkbench.css';

type Catalog = {
  target: string;
  duration_seconds: number;
  issue: string | null;
  memory_issue: string | null;
  workloads: { id: string; title: string }[];
};
type Selection = {
  workloads: string[];
  duration: number | null;
  memoryProfile: boolean;
  profileInterval: number;
};
type MemoryReport = {
  available: boolean;
  samples: number;
  snapshots: number;
  rss_change_mib: number | null;
  baseline_to_later_seconds?: number | null;
  issue: string | null;
  files: { name: string; title: string }[];
};

function defaults(catalog: Catalog): Selection {
  return {
    workloads: catalog.workloads.map(({ id }) => id),
    duration: catalog.duration_seconds,
    memoryProfile: false,
    profileInterval: 30,
  };
}

function StabilityRun({ environmentId, openTask }: { environmentId: string; openTask: (id: string) => void }) {
  const storageKey = 'fbasecman-stability-task:' + environmentId;
  const [taskId, setTaskId] = useState(() => sessionStorage.getItem(storageKey));
  const [running, setRunning] = useState(Boolean(taskId));
  const [catalog, setCatalog] = useState<Catalog>();
  const [selection, setSelection] = useState<Selection>();
  const [draft, setDraft] = useState<Selection>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [memoryReport, setMemoryReport] = useState<MemoryReport>();
  const memoryPath = `/environments/${encodeURIComponent(environmentId)}/fbasecman-longrun/${taskId}/memory`;

  useEffect(() => {
    if (!taskId || running) return;
    const controller = new AbortController();
    void api<MemoryReport>(memoryPath, { signal: controller.signal })
      .then(setMemoryReport)
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [taskId, running, memoryPath]);


  useEffect(() => {
    const controller = new AbortController();
    void api<Catalog>(`/environments/${encodeURIComponent(environmentId)}/fbasecman-longrun`,
      { signal: controller.signal })
      .then((result) => {
        setCatalog(result);
        setSelection(defaults(result));
      })
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [environmentId, revision]);

  async function start() {
    if (!catalog || !selection?.duration || !selection.workloads.length || catalog.issue || busy || running) return;
    setBusy(true);
    setMemoryReport(undefined);
    setError('');
    try {
      const task = await operationRequest(environmentId, 'stability.fbasecman', catalog.target, {
        duration_seconds: selection.duration,
        enabled_workloads: selection.workloads,
        ...(selection.memoryProfile ? { memory_profile: true, profile_interval: selection.profileInterval } : {}),
      }, true);
      sessionStorage.setItem(storageKey, task.id);
      setTaskId(task.id);
      setRunning(true);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <>
    <Card title="标准常稳全套" style={{ maxWidth: 760 }}>
      <Space direction="vertical" size="middle">
        <Typography.Text type="secondary">
          在独立的六节点 MMR 环境运行 fbasecman 的 7×24 小时测试场景。
        </Typography.Text>
        {error && <Alert type="error" showIcon message={error} />}
        {catalog?.issue && <Alert type="warning" showIcon message={catalog.issue} />}
        {loading ? <Space><Spin size="small" /> 正在读取常稳配置</Space> : catalog && selection ? <>
          <Typography.Text strong>
            已选 {selection.workloads.length} / {catalog.workloads.length} 项 · {selection.duration === 600 ? '10 分钟' : `${selection.duration} 秒`}{selection.memoryProfile && ' · jemalloc 内存排查'}
          </Typography.Text>
          <Space wrap>
            <Button type="primary" icon={<PlayCircleOutlined />} loading={busy}
              disabled={Boolean(catalog.issue) || running}
              onClick={() => void start()}>
              {selection.workloads.length === catalog.workloads.length ? '启动全套' : '启动已选'}
            </Button>
            <Button disabled={busy} onClick={() => setDraft(selection)}>修改负载与时长</Button>
            {taskId && <Button type="link" onClick={() => openTask(taskId)}>查看本次任务</Button>}
          </Space>
        </> : <Button onClick={() => {
          setError('');
          setLoading(true);
          setRevision((value) => value + 1);
        }}>重新读取</Button>}
      </Space>
    </Card>
    {memoryReport?.available && <Card title="本次内存诊断" style={{ marginTop: 16, maxWidth: 960 }}>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Typography.Text>
          RSS 采样 {memoryReport.samples} 次 · 保留同进程 heap 快照 {memoryReport.snapshots} 份
          {memoryReport.baseline_to_later_seconds != null && ` · 差分跨度 ${Math.round(memoryReport.baseline_to_later_seconds)} 秒`}
          {memoryReport.rss_change_mib !== null && ` · RSS 变化 ${memoryReport.rss_change_mib} MiB`}
        </Typography.Text>
        {memoryReport.issue && <Alert type="warning" showIcon message={memoryReport.issue} />}
        {memoryReport.files.map(({ name, title }) => {
          const url = `/api/v1${memoryPath}/${name}`;
          return name.endsWith('.svg') || name.endsWith('.png')
            ? <a key={name} href={url} target="_blank" rel="noreferrer">
                <img src={url} alt={title} style={{ display: 'block', maxWidth: '100%' }} />
              </a>
            : <a key={name} href={url} download={name}>{title}</a>;
        })}
      </Space>
    </Card>}
    <ExecutionTerminal taskId={taskId} onInspect={openTask} initiallyExpanded
      onFinished={() => setRunning(false)} />
    <Modal title="修改负载与时长" open={Boolean(draft)} okText="应用" cancelText="取消"
      okButtonProps={{ disabled: !draft?.duration || !draft.workloads.length }}
      onCancel={() => setDraft(undefined)}
      onOk={() => { setSelection(draft); setDraft(undefined); }}>
      {catalog && draft && <Space direction="vertical" size="middle">
        <label>运行时长（秒）{' '}
          <InputNumber min={1} max={2592000} precision={0} value={draft.duration}
            onChange={(duration) => setDraft({ ...draft, duration })} />
        </label>
        <Button size="small" onClick={() => setDraft(defaults(catalog))}>恢复默认</Button>
        <Checkbox checked={draft.memoryProfile} disabled={Boolean(catalog.memory_issue)}
          onChange={(event) => setDraft({ ...draft, memoryProfile: event.target.checked })}>
          jemalloc 内存排查
        </Checkbox>
        {catalog.memory_issue && <Typography.Text type="secondary">{catalog.memory_issue}</Typography.Text>}
        {draft.memoryProfile && <Space wrap>
          <Typography.Text>按累计分配量导出 heap</Typography.Text>
          <Select aria-label="heap 导出间隔" value={draft.profileInterval}
            options={[{ label: '64 MiB · 短时', value: 26 }, { label: '1 GiB · 默认', value: 30 },
              { label: '4 GiB · 长时', value: 32 }]}
            onChange={(profileInterval) => setDraft({ ...draft, profileInterval })} />
        </Space>}
        <Checkbox.Group value={draft.workloads}
          onChange={(workloads) => setDraft({ ...draft, workloads })}>
          <Space direction="vertical">
            {catalog.workloads.map(({ id, title }) => <Checkbox key={id} value={id}>{title}</Checkbox>)}
          </Space>
        </Checkbox.Group>
      </Space>}
    </Modal>
  </>;
}

type Props = {
  product: Product | undefined;
  environment: Environment | undefined;
  openTask: (taskId: string) => void;
  reload: () => Promise<void>;
  environments?: Environment[];
  bindings?: RegressionBinding[];
};

export default function StabilityPage({ product, environment, openTask, reload,
                                        environments = [], bindings = [] }: Props) {
  const profileId = product?.test_profiles?.[0]?.id;
  const binding = bindings.find((item) => item.product_id === product?.id && item.profile_id === profileId);
  const executionEnvironment = profileId
    ? environments.find((item) => item.id === binding?.environment_id && item.product_id === product?.id)
    : environment;

  return (
    <div className="workload-page">
      <header className="workload-page-heading">
        <div className="workload-page-heading-left">
          <span className="workload-page-icon"><ThunderboltOutlined /></span>
          <Typography.Title level={3} style={{ margin: 0 }}>fbasecman 稳定性测试</Typography.Title>
        </div>
      </header>
      <TestBindingBar product={product} profileId={profileId}
        environments={environments} bindings={bindings} onChanged={reload} />
      {executionEnvironment
        ? <StabilityRun key={executionEnvironment.id} environmentId={executionEnvironment.id} openTask={openTask} />
        : <div className="workload-empty"><Empty description="先选择并绑定执行环境" /></div>}
    </div>
  );
}
