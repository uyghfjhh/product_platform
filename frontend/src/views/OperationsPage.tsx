import { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, Checkbox, Collapse, Form, Input, InputNumber, Modal, Select, Space, Table, Tabs, Typography } from 'antd';
import { api, type Action, type Environment, type Product } from '../platform/api';

type Row = { id: string; title?: string; [key: string]: unknown };
type Step = { action: string; target?: string; deployment_plan_id?: string; parameters: Record<string, unknown> };
type Version = { product_id: string; title: string; version: string; source_revision: string | null };
type Evidence = { id: string; path: string; line_start: number; line_end: number; text: string; revision: string | null };
export default function OperationsPage({ products, environments, openTask }: { products: Product[]; environments: Environment[]; openTask: (id: string) => void }) {
  const { modal, message } = App.useApp();
  const [data, setData] = useState<Record<string, Row[]>>({});
  const [versions, setVersions] = useState<Version[]>([]);
  const [kind, setKind] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();
  const [actions, setActions] = useState<Action[]>([]);
  const [plans, setPlans] = useState<Array<{ id: string; action: string; target: string; ready: boolean }>>([]);
  const [steps, setSteps] = useState<Step[]>([{ action: '', parameters: {} }]);
  const [product, setProduct] = useState('');
  const [version, setVersion] = useState('');
  const [query, setQuery] = useState('');
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [answer, setAnswer] = useState('');
  const reload = useCallback(async () => {
    const paths = ['pipelines', 'pipeline-runs', 'schedules', 'notifications', 'notification-webhooks', 'failure-clusters'];
    try {
      const rows = await Promise.all(paths.map((path) => api<Row[]>('/' + path)));
      const releases = await api<{ versions: Version[]; artifacts: Row[] }>('/product-versions');
      setData(Object.fromEntries([...paths.map((path, i) => [path, rows[i]]), ['artifacts', releases.artifacts]])); setVersions(releases.versions);
    } catch (cause) { setError((cause as Error).message); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => {
    if (!(data['pipeline-runs'] || []).some((row) => ['RUNNING', 'CANCELLING'].includes(String(row.status)))) return;
    const timer = setInterval(() => void reload(), 3000); return () => clearInterval(timer);
  }, [data, reload]);
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(''); try { await action(); } catch (cause) { setError((cause as Error).message); } finally { setBusy(false); }
  }
  function confirm(title: string, content: string, action: () => Promise<void>) { modal.confirm({ title, content, onOk: () => perform(action) }); }
  function create(type: string) { setEditing(null); form.resetFields(); setKind(type); setSteps([{ action: '', parameters: {} }]); setActions([]); }
  async function editPipeline(row: Row) {
    setKind('pipelines'); setEditing(row.id); form.setFieldsValue(row);
    setSteps(row.steps as Step[]); setActions(await api<Action[]>(`/environments/${row.environment_id}/actions`)); setPlans(await api<typeof plans>(`/deployment/drafts/${row.environment_id}/plans`).catch(() => []));
  }
  async function save() {
    const values = await form.validateFields();
    if (kind === 'pipelines') values.steps = steps;
    if (kind === 'schedules') values.acknowledge_change = Boolean(values.enabled);
    if (kind === 'notification-webhooks') values.acknowledge_send = Boolean(values.enabled);
    await api('/' + kind + (editing ? '/' + editing : ''), { method: editing ? 'PUT' : 'POST', body: JSON.stringify(values) }); setKind(''); await reload();
  }
  async function knowledge(action: string) {
    if (!product || !version) throw new Error('请选择产品和源码版本');
    const result = await api<{ files?: number; truncated?: boolean; evidence?: Evidence[]; analysis?: { answer: string; unknowns: string[] } }>(`/products/${product}/knowledge/${action}`, {
      method: 'POST', body: JSON.stringify(action === 'index' ? { version } : { query, version, limit: 8 }),
    });
    if (action === 'index') message.info(`已索引 ${result.files} 个文件${result.truncated ? '（部分覆盖）' : ''}`);
    else { setEvidence(result.evidence || []); setAnswer(result.analysis ? result.analysis.answer + '\n' + result.analysis.unknowns.join('\n') : ''); }
  }
  const table = (type: string, fields: string[], extra: (row: Row) => React.ReactNode) => <Table rowKey="id" size="small" scroll={{ x: 'max-content' }} dataSource={data[type] || []}
    columns={[...fields.map((name) => ({ title: ({ title: '名称', environment_id: '环境', action: '动作', target: '目标', status: '状态', enabled: '启用', read: '已读', product_id: '产品', version: '版本', sha256: '内容摘要', id: '执行编号', index: '步骤', reason: '原因', url: '接收地址', interval_seconds: '间隔（秒）', next_run_at: '下次执行', reason_pattern: '错误模式', results: '当前结果' } as Record<string, string>)[name] || name, dataIndex: name, render: (value: unknown) => name === 'environment_id' ? environments.find((e) => e.id === value)?.title || String(value) : name === 'product_id' ? products.find((p) => p.id === value)?.title || String(value) : name === 'pipeline_id' ? (data.pipelines || []).find((p) => p.id === value)?.title || String(value) : name === 'id' ? <Typography.Text copyable={{ text: String(value) }}>{String(value).slice(0, 12)}</Typography.Text> : name === 'status' ? ({ RUNNING: '执行中', SUCCEEDED: '成功', FAILED: '失败', CANCELLING: '取消中', CANCELLED: '已取消', RECOVERY_REQUIRED: '需要恢复' } as Record<string, string>)[String(value)] || String(value) : name === 'enabled' || name === 'read' ? value ? '是' : '否' : name === 'next_run_at' ? new Date(String(value)).toLocaleString() : typeof value === 'object' ? JSON.stringify(value) : String(value ?? '—') })), { title: '操作', render: (_: unknown, row: Row) => extra(row) }]} />;
  return <>
    <Typography.Title level={3}>平台运营</Typography.Title><Button onClick={() => void reload()}>刷新</Button>
    {error && <Alert type="error" message={error} style={{ margin: '12px 0' }} />}
    <Tabs items={[
      { key: 'pipelines', label: '流水线', children: <><Button onClick={() => create('pipelines')}>新建流水线</Button>
        {table('pipelines', ['title', 'environment_id'], (row) => <Space><Button onClick={() => confirm('执行流水线', '将按顺序执行全部步骤，失败时停止。', async () => { await api(`/pipelines/${row.id}/run`, { method: 'POST', body: JSON.stringify({ acknowledge_change: true }) }); await reload(); })}>执行</Button><Button onClick={() => void perform(() => editPipeline(row))}>编辑</Button><Button danger onClick={() => confirm('删除流水线', '运行中或已启用定时执行的流水线不能删除。', async () => { await api(`/pipelines/${row.id}`, { method: 'DELETE' }); await reload(); })}>删除</Button></Space>)}
        <Typography.Title level={5}>执行状态</Typography.Title>{table('pipeline-runs', ['id', 'status', 'index', 'reason'], (row) => <Space wrap>{((row.tasks as string[]) || []).map((id) => <Button key={id} onClick={() => openTask(id)}>{id.slice(0, 8)}</Button>)}{['RUNNING', 'CANCELLING'].includes(String(row.status)) && <Button onClick={() => void perform(async () => { await api(`/pipeline-runs/${row.id}/cancel`, { method: 'POST' }); await reload(); })}>取消</Button>}</Space>)}</> },
      { key: 'schedules', label: '定时执行', children: <><Button onClick={() => create('schedules')}>新建定时任务</Button>{table('schedules', ['pipeline_id', 'enabled', 'interval_seconds', 'next_run_at'], (row) => <Space><Button onClick={() => confirm(row.enabled ? '停用定时执行' : '启用定时执行', '启用后自动执行全部步骤，重启后仍有效。', async () => { await api(`/schedules/${row.id}`, { method: 'PUT', body: JSON.stringify({ ...row, enabled: !row.enabled, acknowledge_change: true }) }); await reload(); })}>{row.enabled ? '停用' : '启用'}</Button><Button danger onClick={() => void perform(async () => { await api(`/schedules/${row.id}`, { method: 'DELETE' }); await reload(); })}>删除</Button></Space>)}</> },
      { key: 'notifications', label: '通知', children: <>{table('notifications', ['action', 'target', 'status', 'read'], (row) => <Space><Button onClick={() => openTask(String(row.task_id))}>查看任务</Button><Button onClick={() => void perform(async () => { await api(`/notifications/${row.id}/read`, { method: 'PUT' }); await reload(); })}>标为已读</Button></Space>)}<Button onClick={() => create('notification-webhooks')}>添加 Webhook 目标</Button>{table('notification-webhooks', ['title', 'url', 'enabled'], (row) => <Space><Button onClick={() => confirm(row.enabled ? '停用外部通知' : '启用外部通知', '将向该 URL 发送任务完成状态，不发送 SQL、日志或凭据。', async () => { await api(`/notification-webhooks/${row.id}`, { method: 'PUT', body: JSON.stringify({ ...row, enabled: !row.enabled, acknowledge_send: true }) }); await reload(); })}>{row.enabled ? '停用' : '启用'}</Button><Button danger onClick={() => void perform(async () => { await api(`/notification-webhooks/${row.id}`, { method: 'DELETE' }); await reload(); })}>删除</Button></Space>)}</> },
      { key: 'versions', label: '版本与产物', children: <><Table rowKey={(row) => row.product_id + ':' + row.version} dataSource={versions} columns={[{ title: '产品', dataIndex: 'title' }, { title: '声明版本', dataIndex: 'version' }, { title: '源码提交', dataIndex: 'source_revision' }, { title: '产物', render: (_, row) => <Button loading={busy} onClick={() => void perform(async () => { await api('/product-artifacts', { method: 'POST', body: JSON.stringify({ product_id: row.product_id, version: row.version }) }); await reload(); })}>生成产品包</Button> }]} />{table('artifacts', ['product_id', 'version', 'sha256'], (row) => <Space><Button href={`/api/v1/product-artifacts/${row.id}/download`}>下载产物</Button><Button loading={busy} onClick={() => void perform(async () => { const plan = await api<{ product_id: string; version: string; sha256: string }>(`/product-artifacts/${row.id}/installation-plan`); confirm('安装或切换产品包', `${plan.product_id} · ${plan.version} · SHA256 ${plan.sha256}。将替换 Provider、用例、模板和前端扩展，重建前端；活动任务会阻止安装。`, async () => { await api(`/product-artifacts/${row.id}/activate`, { method: 'POST', body: JSON.stringify({ acknowledge_change: true }) }); window.location.reload(); }); })}>审阅并安装</Button></Space>)}</> },
      { key: 'knowledge', label: '知识与源码', children: <><Alert type="info" message="引用绑定源码提交、文件摘要和行号；源码版本不等于运行中的二进制版本。" /><Space wrap style={{ margin: '12px 0' }}><Select aria-label="知识产品" style={{ width: 200 }} value={product || undefined} options={products.map((p) => ({ value: p.id, label: p.title }))} onChange={(id) => { setProduct(id); setVersion(''); setEvidence([]); }} /><Select aria-label="知识版本" style={{ width: 120 }} value={version || undefined} options={versions.filter((v) => v.product_id === product).map((v) => ({ value: v.version, label: v.version }))} onChange={setVersion} /><Button loading={busy} onClick={() => void perform(() => knowledge('index'))}>建立或更新索引</Button></Space><Input.TextArea aria-label="检索问题" value={query} onChange={(event) => setQuery(event.target.value)} rows={3} placeholder="代码符号、功能名称或问题" /><Space style={{ margin: '12px 0' }}><Button loading={busy} onClick={() => void perform(() => knowledge('search'))}>检索证据</Button><Button loading={busy} onClick={() => void perform(() => knowledge('answer'))}>基于证据回答</Button></Space>{answer && <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{answer}</Typography.Paragraph>}<Collapse items={evidence.map((e) => ({ key: e.id, label: `${e.path}:${e.line_start}–${e.line_end} · ${e.revision || '未关联提交'}`, children: <pre style={{ whiteSpace: 'pre-wrap' }}>{e.text}</pre> }))} /></> },
      { key: 'failures', label: '失败聚类', children: <><Alert type="info" message="按产品和错误文本聚合当前失败，相似文本不等于共同根因，不改变原判定。" />{table('failure-clusters', ['product_id', 'reason_pattern', 'results'], () => null)}</> },
    ]} />
    <Modal title={kind === 'pipelines' ? '新建流水线' : kind === 'schedules' ? '新建定时任务' : '通知目标'} open={Boolean(kind)} onCancel={() => setKind('')} onOk={() => void perform(save)} confirmLoading={busy} width={850}>
      {error && <Alert type="error" message={error} />}
      <Form form={form} layout="vertical" initialValues={{ interval_seconds: 3600, enabled: false }}>
        {kind !== 'schedules' && <Form.Item name="title" label="名称" rules={[{ required: true }]}><Input /></Form.Item>}
        {kind === 'pipelines' && <><Form.Item name="environment_id" label="执行环境" rules={[{ required: true }]}><Select options={environments.map((e) => ({ value: e.id, label: e.title }))} onChange={(id) => void perform(async () => { setActions(await api<Action[]>(`/environments/${id}/actions`)); setPlans(await api<typeof plans>(`/deployment/drafts/${id}/plans`).catch(() => [])); setSteps([{ action: '', parameters: {} }]); })} /></Form.Item>
          {steps.map((step, position) => <section key={position} style={{ padding: 10 }}><Space wrap><Typography.Text>步骤 {position + 1}</Typography.Text><Select style={{ width: 240 }} value={step.action || undefined} placeholder="选择动作" options={actions.map((a) => ({ value: a.id, label: a.title }))} onChange={(action) => setSteps((rows) => rows.map((row, i) => i === position ? { action, target: ['tests', 'stability'].includes(actions.find((entry) => entry.id === action)?.capability || '') ? 'all' : undefined, parameters: {} } : row))} /><Input style={{ width: 220 }} value={step.target} placeholder="目标（留空使用环境默认）" onChange={(event) => setSteps((rows) => rows.map((row, i) => i === position ? { ...row, target: event.target.value || undefined } : row))} /><Button danger disabled={steps.length === 1} onClick={() => setSteps((rows) => rows.filter((_, i) => i !== position))}>移除</Button></Space>
          {step.action === 'deployment.change' && <Select aria-label="已审阅部署计划" style={{ width: '100%', marginTop: 8 }} placeholder="先在部署管理生成并审阅变更计划" value={step.deployment_plan_id} options={plans.filter((plan) => plan.action === step.action && plan.ready).map((plan) => ({ value: plan.id, label: `${plan.id.slice(0, 12)} · ${plan.target}` }))} onChange={(id) => setSteps((rows) => rows.map((row, i) => i === position ? { ...row, deployment_plan_id: id, target: plans.find((plan) => plan.id === id)?.target } : row))} />}
          {Object.entries((actions.find((a) => a.id === step.action)?.parameter_schema?.properties || {}) as Record<string, { enum?: string[] }>).map(([name, schema]) => <Space key={name} style={{ margin: 8 }}><Typography.Text>{name}</Typography.Text>{schema.enum ? <Select style={{ width: 180 }} options={schema.enum.map((v) => ({ value: v, label: v }))} onChange={(value) => setSteps((rows) => rows.map((row, i) => i === position ? { ...row, parameters: { ...row.parameters, [name]: value } } : row))} /> : <Input onChange={(event) => setSteps((rows) => rows.map((row, i) => i === position ? { ...row, parameters: { ...row.parameters, [name]: event.target.value } } : row))} />}</Space>)}</section>)}<Button onClick={() => setSteps((rows) => [...rows, { action: '', parameters: {} }])}>添加步骤</Button></>}
        {kind === 'schedules' && <><Form.Item name="pipeline_id" label="流水线" rules={[{ required: true }]}><Select options={(data.pipelines || []).map((p) => ({ value: p.id, label: p.title }))} /></Form.Item><Form.Item name="interval_seconds" label="执行间隔（秒）" rules={[{ required: true }]}><InputNumber min={60} max={31536000} /></Form.Item></>}
        {kind === 'notification-webhooks' && <><Form.Item name="url" label="接收 URL" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="credential_env" label="令牌环境变量名（可选）" extra="只保存变量名，令牌由服务环境提供。"><Input /></Form.Item></>}
        {kind !== 'pipelines' && <Form.Item name="enabled" valuePropName="checked"><Checkbox>确认启用{kind === 'schedules' ? '全部流水线步骤的自动执行' : '向上述 URL 发送任务状态'}</Checkbox></Form.Item>}
      </Form>
    </Modal>
  </>;
}
