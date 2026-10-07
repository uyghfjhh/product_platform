import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Collapse, Empty, Input, InputNumber, Modal, Select, Space, Spin, Switch, Table, Tabs, Tag, Typography } from 'antd';
import { ExperimentOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, StopOutlined } from '@ant-design/icons';

import { api, generateUUID, post, statusColor, type Environment, type Task } from '../platform/api';
import CodeEditor from './CodeEditor';
import WorkloadDashboard from './WorkloadDashboard';
import './workloadWorkbench.css';

type Parameters = {
  clients: number; jobs: number | null; duration_seconds: number; preset: string;
  script: string; target_tps: number; connect_per_transaction: boolean;
  statement_timeout_seconds: number; minimum_tps: number; max_average_latency_ms: number; jdbc_jar: string;
  connection_mode: 'direct' | 'proxy' | 'compare';
};
type Entry = {
  id: string; title: string; description: string; driver: string; category: string;
  impact: string; available: boolean; reason: string | null; defaults: Parameters;
  default_selected: boolean; editable_script: boolean; source: string;
};
type Draft = Entry & { key: string; selected: boolean; parameters: Parameters };
type Catalog = { environment: Environment; workloads: Entry[]; limitations: string[];
  connection_modes?: ('direct' | 'proxy' | 'compare')[];
  profiles?: { id: string; title: string; duration_seconds: number; clients: number }[];
};
type Plan = {
  id: string; ready: boolean; issues: string[]; smoke: boolean; expires_at: string;
  environment: Environment; duration_seconds: number; peak_clients: number;
  steps: { target: string; parameters: Parameters }[];
  workloads: { id: string; title: string; driver: string; script_modified: boolean; script_sha256: string;
    tested_build?: { binary: string; sha256: string; version: string } | null }[];
};
type Run = { id: string; status: string; reason?: string; tasks: string[]; task_details: Task[]; definition: Plan };
type Sample = { elapsed: number; tps: number; latency_ms: number; observed_at?: string; phase?: string };
type Comparison = { valid: boolean; reason: string; direct_tps?: number; proxy_tps?: number;
  tps_loss_percent?: number; direct_latency_ms?: number; proxy_latency_ms?: number;
  latency_increase_percent?: number | null; latency_increase_ms?: number };
type Metrics = {
  status: string; available: boolean; samples: Sample[]; sample_count?: number;
  summary: { tps?: number; latency_ms?: number; errors?: number };
  phase?: string;
  connection_evidence?: { proxy_pid?: number; proxy_started?: boolean; proxy_stopped?: boolean;
    tested_build?: { binary: string; sha256: string; version: string };
    backend?: { host: string; port: number }; proxy?: { host: string; port: number } };
  proxy_monitor?: { pid: number; samples: { rss_mib: number; cpu_percent: number | null }[] };
  result?: { reason: string; performance_verdict: string; correctness_verdict: string; started_at?: number;
    comparison?: Comparison; phases?: Record<string, { summary: Metrics['summary'] }>;
    checks: { name: string; expected: unknown; actual: unknown; passed: boolean }[] };
};
const TERMINAL = ['SUCCEEDED', 'FAILED', 'CANCELLED', 'RECOVERY_REQUIRED'];
const LABELS: Record<string, string> = { QUEUED: '排队中', RUNNING: '运行中', CANCELLING: '停止中', SUCCEEDED: '执行成功', FAILED: '执行失败', CANCELLED: '已取消', RECOVERY_REQUIRED: '需要恢复', PENDING: '尚未执行' };
const verdict = (value?: string) => value === 'NOT_CONFIGURED' || !value ? '未配置' : value;
const text = (value: unknown) => value == null ? '未采集' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const connectionLabel = (mode?: string) => ({ direct: '数据库直连', proxy: 'fbasecman 代理', compare: '直连 / fbasecman 对比' }[mode || 'direct'] || mode);

function Curve({ samples, field, title }: { samples: Sample[]; field: 'tps' | 'latency_ms'; title: string }) {
  const maximum = Math.max(1, ...samples.map((sample) => sample[field]));
  const start = samples[0]?.elapsed || 0;
  const end = samples.at(-1)?.elapsed || 0;
  const points = samples.map((sample) => `${28 + (sample.elapsed - start) * 276 / Math.max(1, end - start)},${100 - sample[field] / maximum * 72}`).join(' ');
  return <section className="wb-curve"><strong>{title}</strong>{samples.length ? <svg role="img" aria-label={title} viewBox="0 0 320 126"><path d="M28 20V100H308" /><polyline points={points} /><text x="28" y="120">执行时间（秒）</text><text x="30" y="18">{maximum.toFixed(1)}</text></svg> : <div className="wb-no-samples">等待真实采样</div>}</section>;
}

export default function WorkloadWorkbench({ environment, openTask }: { environment: Environment; openTask: (identity: string) => void }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [active, setActive] = useState('');
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('全部');
  const [openGroups, setOpenGroups] = useState<string[]>(['SQL 负载']);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [compare, setCompare] = useState(false);
  const [workspace, setWorkspace] = useState('configure');
  const storageKey = 'workload-run:' + environment.id;
  const [runId, setRunId] = useState(() => sessionStorage.getItem(storageKey) || '');
  const [run, setRun] = useState<Run | null>(null);
  const [metrics, setMetrics] = useState<Record<string, Metrics>>({});
  const cache = useRef<Record<string, Metrics>>({});
  const [inspectedTask, setInspectedTask] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    void api<Catalog>(`/environments/${encodeURIComponent(environment.id)}/workloads`, { signal: controller.signal })
      .then((data) => {
        setCatalog(data);
        setDrafts(data.workloads.map((entry) => ({ ...entry, key: entry.id, selected: entry.default_selected && entry.available, parameters: { ...entry.defaults } })));
        setActive(data.workloads.find((entry) => entry.default_selected)?.id || data.workloads[0]?.id || '');
      }).catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); })
      .finally(() => { if (!controller.signal.aborted) setCatalogLoading(false); });
    return () => controller.abort();
  }, [environment.id]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let timer: number;
    async function refresh() {
      try {
        const row = await api<Run>(`/workload-runs/${encodeURIComponent(runId)}`);
        if (cancelled) return;
        setRun(row);
        await Promise.all(row.tasks.map(async (identity) => {
          if (cache.current[identity] && TERMINAL.includes(cache.current[identity].status)) return;
          try { cache.current[identity] = await api<Metrics>(`/operations/${identity}/workload`); } catch { return; }
        }));
        if (cancelled) return;
        setMetrics({ ...cache.current });
        if (!TERMINAL.includes(row.status)) timer = window.setTimeout(() => void refresh(), 1500);
      } catch (cause) {
        if (!cancelled) {
          const message = (cause as Error).message;
          setError(message);
          if (message === '负载执行不存在') {
            sessionStorage.removeItem(storageKey); setRunId(''); setRun(null);
          } else timer = window.setTimeout(() => void refresh(), 3000);
        }
      }
    }
    void refresh();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [runId]);

  const current = drafts.find((entry) => entry.key === active);
  const selected = drafts.filter((entry) => entry.selected);
  const visible = drafts.filter((entry) => (category === '全部' || entry.category === category)
    && (entry.title + entry.description + entry.id).toLowerCase().includes(query.toLowerCase()));
  const running = Boolean(runId && (!run || !TERMINAL.includes(run.status)));
  function update(values: Partial<Parameters>) {
    setDrafts((entries) => entries.map((entry) => entry.key === active ? { ...entry, parameters: { ...entry.parameters, ...values } } : entry));
  }
  function select(key: string, checked: boolean) {
    setDrafts((entries) => entries.map((entry) => entry.key === key ? { ...entry, selected: checked } : entry));
  }
  function addCustom() {
    const template = drafts.find((entry) => entry.id === 'pgbench.connectivity');
    if (!template?.available) return;
    const key = 'custom-' + generateUUID();
    setDrafts((entries) => [...entries, { ...template, key, title: '自定义 SQL 负载', description: '本次自定义查询方案', parameters: { ...template.defaults }, selected: true }]);
    setActive(key);
  }
  async function review(smoke = false) {
    setBusy(true); setError('');
    try {
      const value = await post<Plan>('/workload-plans', { environment_id: environment.id, mode: 'sequential', smoke,
        workloads: selected.map((entry) => ({ id: entry.id, instance_id: entry.key, title: entry.title, parameters: entry.parameters })) });
      setPlan(value);
    } catch (cause) { setError((cause as Error).message); } finally { setBusy(false); }
  }
  async function start() {
    if (!plan) return;
    setBusy(true); setError('');
    try {
      const row = await post<Run>(`/workload-plans/${plan.id}/run`, { acknowledge_change: true });
      cache.current = {}; setMetrics({}); setInspectedTask('');
      setRun(row); setRunId(row.id); sessionStorage.setItem(storageKey, row.id);
      setPlan(null); setWorkspace('dashboard');
    } catch (cause) { setError((cause as Error).message); } finally { setBusy(false); }
  }
  async function stop() {
    if (!run) return;
    setBusy(true);
    try { setRun(await post<Run>(`/workload-runs/${run.id}/cancel`, {})); }
    catch (cause) { setError((cause as Error).message); } finally { setBusy(false); }
  }
  function numberField(key: 'clients' | 'jobs' | 'duration_seconds' | 'target_tps' | 'statement_timeout_seconds' | 'minimum_tps' | 'max_average_latency_ms', label: string, min: number, max: number) {
    if (!current) return null;
    return <div className="wb-field"><label>{label}</label><InputNumber aria-label={label} min={min} max={max} value={current.parameters[key]} disabled={busy} precision={key.endsWith('_tps') || key.endsWith('_ms') ? undefined : 0}
      onChange={(value) => {
        const numeric = value == null ? min : Number(value);
        const patch = { [key]: key === 'jobs' && value == null ? null : numeric };
        if (key === 'clients' && current.parameters.jobs && numeric < current.parameters.jobs) patch.jobs = numeric;
        update(patch);
      }} /></div>;
  }
  const taskId = inspectedTask || run?.tasks.at(-1) || '';
  const observed = metrics[taskId];
  const dashboardView = <WorkloadDashboard environmentId={environment.id} runId={runId || undefined} openTask={openTask}
    clients={run ? run.definition.workloads.flatMap((entry, index) => {
      const fact = metrics[run.tasks[index]];
      const phases = run.definition.steps[index].parameters.connection_mode === 'compare' ? ['direct', 'proxy'] : [undefined];
      return phases.map((phase) => ({ id: (run.tasks[index] || entry.id)+(phase ? ':'+phase : ''), title: entry.title+(phase ? ' · '+connectionLabel(phase) : ''),
      status: phase && run.task_details?.[index]?.status === 'RUNNING'
        ? fact?.phase === phase ? 'RUNNING' : fact?.phase === 'proxy' && phase === 'direct' ? 'SUCCEEDED' : 'PENDING'
        : run.task_details?.[index]?.status || 'PENDING', started_at: run.task_details?.[index]?.started_at,
      proxy: fact?.connection_evidence?.proxy_started ? {
        pid: fact.connection_evidence.proxy_pid, stopped: fact.connection_evidence.proxy_stopped,
        endpoint: `${fact.connection_evidence.proxy?.host}:${fact.connection_evidence.proxy?.port}`,
        ...fact.proxy_monitor?.samples.at(-1),
      } : undefined,
      samples: (fact?.samples || []).filter((sample) => !phase || sample.phase === phase),
      summary: phase ? fact?.result?.phases?.[phase]?.summary : fact?.summary,
      result_started_at: metrics[run.tasks[index]]?.result?.started_at,
    })); }) : []} />;
  const configure = <>
    <div className="wb-grid">
      <section className="wb-library">
        <div className="wb-panel-heading"><div><span className="wb-eyebrow">WORKLOAD LIBRARY</span><h4>工作负载库</h4></div><Tag>{selected.length} 项已选</Tag></div>
        <div className="wb-library-filters"><Input aria-label="搜索负载" prefix={<SearchOutlined />} placeholder="搜索名称、场景或引擎" value={query} onChange={(event) => setQuery(event.target.value)} />
          <Select aria-label="负载分类" value={category} onChange={setCategory} options={['全部', ...new Set(drafts.map((entry) => entry.category))].map((value) => ({ value, label: value }))} /></div>
        <div className="wb-selection-actions"><Button type="text" size="small" disabled={busy} onClick={() => setDrafts((entries) => entries.map((entry) => ({ ...entry, selected: visible.some((item) => item.key === entry.key && item.available) || entry.selected })))}>选择可用项</Button><Button type="text" size="small" disabled={busy} onClick={() => setDrafts((entries) => entries.map((entry) => ({ ...entry, selected: false })))}>清空选择</Button></div>
        <div className="wb-library-list">{visible.length ? <Collapse ghost size="small"
          activeKey={query || category !== '全部' ? [...new Set(visible.map((entry) => entry.category))] : openGroups}
          onChange={(keys) => setOpenGroups(Array.isArray(keys) ? keys : [keys])}
          items={[...new Set(visible.map((entry) => entry.category))].map((group) => ({ key: group,
            label: <span className="wb-group-label">{group}<Tag>{visible.filter((entry) => entry.category === group).length} 项</Tag>{!visible.some((entry) => entry.category === group && entry.available) && <small>尚未接入</small>}</span>,
            children: visible.filter((entry) => entry.category === group).map((entry) => <div key={entry.key} className={`wb-library-row ${active === entry.key ? 'active' : ''} ${!entry.available ? 'unavailable' : ''}`}>
              <Checkbox aria-label={'选择 ' + entry.title} checked={entry.selected} disabled={busy || !entry.available} onChange={(event) => select(entry.key, event.target.checked)} />
              <button type="button" className="wb-library-open" onClick={() => setActive(entry.key)}><div className="wb-compact-title"><strong>{entry.title}</strong><Tag>{entry.driver}</Tag></div>{active === entry.key && <span>{entry.description}</span>}</button>
            </div>),
          }))} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的负载" />}</div>
        <Button className="wb-custom-button" icon={<PlusOutlined />} disabled={busy || !drafts.some((entry) => entry.id === 'pgbench.connectivity' && entry.available)} onClick={addCustom}>添加自定义 SQL 负载</Button>
      </section>
      <section className="wb-config">
        {current ? <>
          <div className="wb-panel-heading"><div><span className="wb-eyebrow">SCENARIO CONFIGURATION</span><h4>{current.title}</h4><p>{current.description}</p></div><Tag>{current.driver}</Tag></div>
          <div className="wb-config-source"><span>参数来源：{current.source}</span>{current.available && JSON.stringify(current.parameters) !== JSON.stringify(current.defaults) && <Tag color="processing">本次已覆盖</Tag>}</div>
          {!current.available ? <Alert type="warning" showIcon message="该场景暂不可执行" description={current.reason} /> : <>
            {current.key.startsWith('custom-') && <div className="wb-custom-name"><Input aria-label="自定义负载名称" value={current.title} maxLength={120} onChange={(event) => setDrafts((entries) => entries.map((entry) => entry.key === active ? { ...entry, title: event.target.value } : entry))} /><Button danger disabled={busy} onClick={() => { setDrafts((entries) => entries.filter((entry) => entry.key !== active)); setActive(drafts[0]?.key || ''); }}>移除</Button></div>}
            <Tabs items={[
              { key: 'parameters', label: '负载参数', children: <>
                <div className="wb-default-summary"><strong>常用参数和 SQL 已填好，通常无需修改</strong><span>需要调整负载强度时，可选一个常用预设。</span></div>
                <div className="wb-field"><label>连接方式</label><Select aria-label="连接方式" disabled={busy}
                  value={current.parameters.connection_mode || 'direct'}
                  options={(catalog?.connection_modes || ['direct']).map((value) => ({ value, label: connectionLabel(value) }))}
                  onChange={(connection_mode) => update({ connection_mode })} /></div>
                {current.parameters.connection_mode !== 'direct' && <div className="wb-quiet-note">自动启动本轮独立 fbasecman，检查控制台和实际后端，结束后停止。代理固定访问登记的同一后端；对比模式先直连后代理，每阶段分别预热 3 秒。</div>}
                <Space wrap className="wb-profile-presets">{catalog?.profiles?.map((profile) => <Button key={profile.id} disabled={busy}
                  type={current.parameters.duration_seconds === profile.duration_seconds && current.parameters.clients === profile.clients ? 'primary' : 'default'}
                  onClick={() => update({ duration_seconds: profile.duration_seconds, clients: profile.clients, jobs: null })}>{profile.title}</Button>)}</Space>
                <div className="wb-fields">{numberField('duration_seconds', '时长（秒）', 1, 86400)}{numberField('clients', '并发客户端', 1, 128)}</div>
                <div className="wb-default-facts"><Tag>{current.driver === 'jdbc' ? '线程与客户端一致' : current.parameters.jobs == null ? '线程自动匹配' : `线程 ${current.parameters.jobs}`}</Tag><Tag>查询超时 {current.parameters.statement_timeout_seconds} 秒</Tag>
                  {current.driver === 'pgbench' && <Tag>{current.parameters.connect_per_transaction ? '每事务重连' : '复用连接'}</Tag>}
                  {current.driver === 'jdbc' && <Tag>{current.parameters.jdbc_jar ? '驱动路径已填入' : '需要 JDBC 驱动'}</Tag>}</div>
                {current.driver === 'jdbc' && !current.parameters.jdbc_jar && <Alert type="warning" showIcon message="未发现本机 JDBC 驱动，请在高级配置中补充路径" />}
                <Collapse key={current.key} ghost className="wb-advanced" defaultActiveKey={current.driver === 'jdbc' && !current.parameters.jdbc_jar ? ['advanced'] : []}
                  items={[{ key: 'advanced', label: current.driver === 'jdbc' && !current.parameters.jdbc_jar ? '高级配置（需补充驱动）' : '高级配置（已提供默认值）', children: <>
                    <div className="wb-fields">{current.driver === 'pgbench' && <>{numberField('jobs', '工作线程（空值自动）', 1, current.parameters.clients)}{numberField('target_tps', '目标 TPS（0 表示不限速）', 0, 1000000)}</>}
                      {numberField('statement_timeout_seconds', '单条查询超时（秒）', 1, 60)}</div>
                    {current.driver === 'pgbench' ? <div className="wb-toggle-field"><div><strong>每事务重新连接</strong><span>已按负载模板选择，通常无需修改。</span></div><Switch aria-label="每事务重新连接" checked={current.parameters.connect_per_transaction} disabled={busy} onChange={(value) => update({ connect_per_transaction: value })} /></div>
                      : <div className="wb-field"><label>控制机 JDBC 驱动 jar 绝对路径</label><Input aria-label="JDBC 驱动路径" value={current.parameters.jdbc_jar} disabled={busy} onChange={(event) => update({ jdbc_jar: event.target.value })} placeholder="自动识别本机驱动，也可手动覆盖" /></div>}
                  </> }]} />
                <div className="wb-quiet-note">预设只调整时长、并发和自动线程，不覆盖 SQL 或验收规则。查询使用只读事务，建议使用专用只读账号。</div>
              </> },
              { key: 'sql', label: 'SQL 脚本', children: <>
                <div className="wb-script-toolbar"><Tag color={current.parameters.script === current.defaults.script ? 'default' : 'processing'}>{current.parameters.script === current.defaults.script ? '默认脚本' : '本次自定义'}</Tag><Space><Button size="small" disabled={busy} onClick={() => update({ script: current.defaults.script })}>恢复默认 SQL</Button><Button size="small" onClick={() => setCompare(true)}>对照默认</Button></Space></div>
                <CodeEditor value={current.parameters.script} language="sql" height={290} readOnly={busy} onChange={(script) => update({ script })} theme={['dark', 'cman'].includes(document.documentElement.dataset.theme || '') ? 'vs-dark' : 'vs'} />
                <div className="wb-quiet-note">仅支持 SELECT；JDBC 当前支持单条查询。事务控制、写入和 pgbench 元命令会在审阅时拒绝。</div>
              </> },
              { key: 'acceptance', label: '验收规则', children: <>
                <div className="wb-fields">{numberField('minimum_tps', '最低 TPS（0 不设阈值）', 0, 1000000)}{numberField('max_average_latency_ms', '最高平均延迟 ms（0 不设阈值）', 0, 3600000)}</div>
                <Alert type="info" showIcon message="执行成功不等于性能达标" description="未设置阈值时报告明确标注性能验收未配置；本阶段没有业务结果断言或 P95 / P99 采集。" />
              </> },
            ]} />
            <Button type="text" icon={<ReloadOutlined />} disabled={busy} onClick={() => update({ ...current.defaults })}>恢复此负载全部默认参数</Button>
          </>}
        </> : <Empty description="选择一项负载查看配置" />}
      </section>
    </div>
    <footer className="wb-execution-bar"><div><strong>本次执行 · {selected.length} 项负载</strong><span>顺序执行 · 失败停止后续项 · 后端 {catalog?.environment.host}:{catalog?.environment.port}/{catalog?.environment.database_name}</span><small>{[...new Set(selected.map((entry) => connectionLabel(entry.parameters.connection_mode)))].join('、')} · 代理端口在执行时独立分配</small></div><Space wrap><Button icon={<ExperimentOutlined />} loading={busy} disabled={!selected.length || running} onClick={() => void review(true)}>小规模试跑</Button><Button type="primary" size="large" icon={<PlayCircleOutlined />} loading={busy} disabled={!selected.length || running} onClick={() => void review()}>审阅并启动 {selected.length} 项</Button></Space></footer>
  </>;
  const observe = run ? <section className="wb-run-panel">
    <div className="wb-panel-heading"><div><span className="wb-eyebrow">RUN OBSERVATION</span><h4>负载执行</h4><p>{run.definition.environment.host}:{run.definition.environment.port}/{run.definition.environment.database_name} · 顺序执行</p></div><Space><Tag color={statusColor(run.status)}>{LABELS[run.status] || run.status}</Tag>{running && <Button danger icon={<StopOutlined />} loading={busy} onClick={() => Modal.confirm({ title: '停止整组工作负载？', content: '取消当前任务，并禁止提交后续负载。', onOk: stop })}>停止整组</Button>}</Space></div>
    {run.reason && <Alert type="warning" message={run.reason} />}
    <Table pagination={false} rowKey="id" size="small" scroll={{ x: 700 }} dataSource={run.definition.workloads.map((entry, index) => ({ ...entry, parameters: run.definition.steps[index].parameters, task: run.task_details?.[index], identity: run.tasks[index] }))} columns={[
      { title: '负载', dataIndex: 'title' },
      { title: '连接方式', render: (_, row) => connectionLabel(row.parameters.connection_mode) },
      { title: '状态', render: (_, row) => <Tag color={statusColor(row.task?.status || 'PENDING')}>{LABELS[row.task?.status || 'PENDING'] || row.task?.status}</Tag> },
      { title: 'TPS', render: (_, row) => metrics[row.identity]?.summary.tps?.toFixed(2) ?? metrics[row.identity]?.samples.at(-1)?.tps.toFixed(2) ?? '—' },
      { title: '平均延迟 ms', render: (_, row) => metrics[row.identity]?.summary.latency_ms?.toFixed(3) ?? metrics[row.identity]?.samples.at(-1)?.latency_ms.toFixed(3) ?? '—' },
      { title: '性能验收', render: (_, row) => verdict(metrics[row.identity]?.result?.performance_verdict) },
      { title: '证据', render: (_, row) => <Space><Button size="small" disabled={!row.identity} onClick={() => setInspectedTask(row.identity)}>采样与判点</Button><Button size="small" disabled={!row.identity} onClick={() => openTask(row.identity)}>任务与原始输出</Button></Space> },
    ]} />
    {taskId && <div className="wb-observation">
      {observed?.connection_evidence?.tested_build && <Typography.Paragraph>
        被测构建：{observed.connection_evidence.tested_build.version} · {observed.connection_evidence.tested_build.binary}<br />
        SHA256：{observed.connection_evidence.tested_build.sha256}
      </Typography.Paragraph>}
      {observed?.connection_evidence?.proxy_started && <Alert type="info" showIcon
        message={`fbasecman PID ${observed.connection_evidence.proxy_pid} · ${observed.connection_evidence.proxy_stopped ? '本轮已停止' : '本轮已启动'}`}
        description={`代理 ${observed.connection_evidence.proxy?.host}:${observed.connection_evidence.proxy?.port} → 后端 ${observed.connection_evidence.backend?.host}:${observed.connection_evidence.backend?.port}。独立配置、控制台输出与后端身份验证保存在本轮证据。`} />}
      {observed?.proxy_monitor?.samples.length ? <div className="wb-quiet-note">代理末次采样：RSS {observed.proxy_monitor.samples.at(-1)?.rss_mib.toFixed(1)} MiB · CPU {observed.proxy_monitor.samples.at(-1)?.cpu_percent?.toFixed(1) ?? '等待基线'} %（100% = 一个 CPU 核）</div> : null}
      {observed?.result?.comparison && <><Typography.Title level={5}>直连与 fbasecman 性能对比</Typography.Title>
        <Typography.Paragraph>{observed.result.comparison.reason}</Typography.Paragraph>
        {observed.result.comparison.valid && <Table pagination={false} size="small" rowKey="metric" dataSource={[
          { metric: 'TPS', direct: observed.result.comparison.direct_tps?.toFixed(2), proxy: observed.result.comparison.proxy_tps?.toFixed(2), change: `吞吐损失 ${observed.result.comparison.tps_loss_percent?.toFixed(2)}%` },
          { metric: '平均延迟 ms', direct: observed.result.comparison.direct_latency_ms?.toFixed(3), proxy: observed.result.comparison.proxy_latency_ms?.toFixed(3), change: observed.result.comparison.latency_increase_percent == null ? '直连延迟为 0，百分比不可计算' : `延迟增幅 ${observed.result.comparison.latency_increase_percent.toFixed(2)}%` },
        ]} columns={[{ title: '指标', dataIndex: 'metric' }, { title: '数据库直连', dataIndex: 'direct' }, { title: 'fbasecman', dataIndex: 'proxy' }, { title: '相对变化', dataIndex: 'change' }]} />}
      </>}
      {(observed?.samples.some((sample) => sample.phase === 'direct') ? ['direct', 'proxy'] : [undefined]).map((phase) => <div className="wb-curves" key={phase || 'single'}><Curve samples={(observed?.samples || []).filter((sample) => !phase || sample.phase === phase)} field="tps" title={`${phase ? connectionLabel(phase)+' · ' : ''}TPS 实际采样`} /><Curve samples={(observed?.samples || []).filter((sample) => !phase || sample.phase === phase)} field="latency_ms" title={`${phase ? connectionLabel(phase)+' · ' : ''}平均延迟 ms 实际采样`} /></div>)}
      {observed && (observed.sample_count ?? observed.samples.length) > observed.samples.length && <div className="wb-quiet-note">曲线显示最近 {observed.samples.length} / {observed.sample_count} 个采样；完整历史见「任务与原始输出」。</div>}
      {observed?.result ? <><Typography.Paragraph>{observed.result.reason}</Typography.Paragraph><Table size="small" pagination={false} rowKey="name" dataSource={observed.result.checks} columns={[{ title: '判点', dataIndex: 'name' }, { title: '期望', dataIndex: 'expected', render: text }, { title: '实际', dataIndex: 'actual', render: text }, { title: '判定', dataIndex: 'passed', render: (passed: boolean) => <Tag color={passed ? 'success' : 'error'}>{passed ? 'PASS' : 'FAIL'}</Tag> }]} /><div className="wb-quiet-note">业务正确性断言：{verdict(observed.result.correctness_verdict)}。曲线为客户端采样，不包含未采集的分位延迟。</div></> : <div className="wb-quiet-note">尚未产生最终判点；可打开任务查看实时输出。</div>}
    </div>}
  </section> : <div className="workload-empty"><Empty description="尚未提交负载方案，启动后在这里观察每项任务" /></div>;

  return <div className="workload-workbench">
    {error && <Alert closable type="error" showIcon message={error} onClose={() => setError('')} />}
    <div className="wb-target-summary"><span className="wb-target-dot" /><strong>{catalog?.environment.title || environment.title}</strong><code>{catalog?.environment.host || environment.host}:{catalog?.environment.port || environment.port}</code><span>数据库：{catalog?.environment.database_name || environment.database_name}</span><Tag>登记的后端数据库</Tag></div>
    {!catalog ? catalogLoading ? <div className="wb-loading"><Spin /> 正在读取负载目录</div> : <div className="workload-empty"><Empty description="负载目录读取失败，请确认后端已加载新接口" /><Button onClick={() => window.location.reload()}>重新读取</Button></div> : <Tabs activeKey={workspace} onChange={setWorkspace} items={[{ key: 'configure', label: '配置方案', children: configure }, { key: 'dashboard', label: '综合监控大屏', children: dashboardView }, { key: 'observe', label: <>运行观察 {run && <Tag>{LABELS[run.status] || run.status}</Tag>}</>, children: observe }]} />}
    <Modal title={plan?.smoke ? '审阅小规模试跑' : '审阅执行计划'} open={Boolean(plan)} width={840} onCancel={() => !busy && setPlan(null)} onOk={() => void start()} confirmLoading={busy} okText="确认启动" okButtonProps={{ disabled: !plan?.ready || running }}>
      {plan && <><div className="wb-review-summary"><strong>{plan.environment.title}</strong><code>{plan.environment.host}:{plan.environment.port}/{plan.environment.database_name}</code><span>用户：{plan.environment.database_user} · 顺序执行 · 峰值 {plan.peak_clients} 个客户端 · 总运行配置时长 {plan.duration_seconds} 秒（不含准备及连接开销）</span></div>
        {plan.issues.map((issue) => <Alert key={issue} type="error" showIcon message={issue} />)}
        {plan.workloads.find((entry) => entry.tested_build)?.tested_build && <Typography.Paragraph>
          被测 fbasecman：{plan.workloads.find((entry) => entry.tested_build)?.tested_build?.version}<br />
          {plan.workloads.find((entry) => entry.tested_build)?.tested_build?.binary}<br />
          SHA256：{plan.workloads.find((entry) => entry.tested_build)?.tested_build?.sha256}
        </Typography.Paragraph>}
        <Table size="small" pagination={false} rowKey="id" scroll={{ x: 600 }} dataSource={plan.workloads.map((entry, index) => ({ ...entry, parameters: plan.steps[index].parameters }))} columns={[{ title: '负载', dataIndex: 'title' }, { title: '连接方式', render: (_, row) => connectionLabel(row.parameters.connection_mode) }, { title: '引擎', dataIndex: 'driver' }, { title: '客户端', render: (_, row) => row.parameters.clients }, { title: '每阶段时长 s', render: (_, row) => row.parameters.duration_seconds }, { title: 'SQL', render: (_, row) => <Tag color={row.script_modified ? 'processing' : 'default'}>{row.script_modified ? '本次覆盖' : '默认脚本'}</Tag> }]} />
        <div className="wb-quiet-note">计划保存了实际参数与 SQL 摘要。环境或部署配置变化后必须重新审阅。有效期至 {new Date(plan.expires_at).toLocaleTimeString()}。</div></>}
    </Modal>
    <Modal title="默认脚本与本次脚本对照" open={compare} onCancel={() => setCompare(false)} footer={null} width={900}><div className="wb-script-comparison"><div><strong>默认脚本</strong><pre>{current?.defaults.script}</pre></div><div><strong>本次脚本</strong><pre>{current?.parameters.script}</pre></div></div></Modal>
  </div>;
}
