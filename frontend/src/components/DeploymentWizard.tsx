import { useEffect, useState } from 'react';
import { Alert, App, Button, Checkbox, Collapse, Form, Input, InputNumber, Modal, Radio, Select, Space, Steps, Table, Tag, Typography } from 'antd';
import { api, type Environment, type Task } from '../api';

type Template = { id: string; title: string; product_id: string; product_title: string; base_port: number; nodes: number };
type HostResource = { name: string; address: string; ssh_user: string; ssh_port?: number | null; ssh_identity_file: string; ssh_connect_timeout: number; home: string };
type Node = { name: string; host?: string; port: number; data_dir: string };
type Spec = {
  title: string; product_id: string; template_id: string; mode: 'new' | 'adopt' | 'import';
  host: string; hosts: HostResource[]; home: string; data_root: string; license_file: string; base_port: number;
  nodes: Node[]; parameters: Record<string, unknown>; source_yaml: string; target: string;
};
type Draft = { id: string; revision: number; spec: Spec };
type Check = { title: string; host: string; ok: boolean; detail: unknown };
type Plan = { id: string; ready: boolean; checks: Check[]; target: string; files: Record<string, string>;
  action: string | null; mode?: string; executable?: boolean;
  limitations: string[]; operations: Array<Node & { node: string; operation: string; kind?: string; executable?: boolean }> };
type Installation = { home: string; version: string; complete: boolean; sources: string[]; error?: string };
type FormValues = Omit<Spec, 'parameters' | 'nodes'> & { template_key: string; parameters_text: string; probe_data_dir?: string };

export default function DeploymentWizard({ open, environment, onClose, onSaved, openTask }: {
  open: boolean; environment?: Environment; onClose: () => void;
  onSaved: (id: string) => Promise<void>; openTask: (id: string) => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [step, setStep] = useState(0);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [hosts, setHosts] = useState<HostResource[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [installations, setInstallations] = useState<Installation[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [preview, setPreview] = useState('');
  const [importTargets, setImportTargets] = useState<Array<{ value: string; label: string }>>([]);
  const mode = Form.useWatch('mode', form);

  function loadDraft(value: Draft) {
    setDraft(value); setNodes(value.spec.nodes); setHosts(value.spec.hosts || []); setPlan(null); setStep(0); setConfirmed(false); setPreview(''); setImportTargets(value.spec.target ? [{ value: value.spec.target, label: value.spec.target }] : []);
    form.setFieldsValue({ ...value.spec, template_key: `${value.spec.product_id}:${value.spec.template_id}`,
      parameters_text: JSON.stringify(value.spec.parameters, null, 2) });
  }

  useEffect(() => {
    if (!open) return;
    let active = true;
    setStep(0); setDraft(null); setPlan(null); setNodes([]); setHosts([]); setError(''); setConfirmed(false); setPreview(''); setInstallations([]);
    setBusy(true);
    void Promise.all([api<Template[]>('/deployment/templates'), api<Draft[]>('/deployment/drafts')])
      .then(async ([options, saved]) => {
        if (!active) return;
        setTemplates(options); setDrafts(saved);
        if (environment) {
          const imported = await api<Draft>(`/environments/${encodeURIComponent(environment.id)}/deployment-draft`, { method: 'POST' });
          if (active) loadDraft(imported);
        } else {
          const first = options[0];
          form.resetFields();
          form.setFieldsValue({ title: '', mode: 'new', template_key: first ? `${first.product_id}:${first.id}` : '',
            host: '127.0.0.1', home: '', data_root: '', license_file: '',
            base_port: first?.base_port || 15432, parameters_text: '{}', source_yaml: '', target: '' });
        }
      }).catch((cause: Error) => { if (active) setError(cause.message); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [open, environment?.id, form]);

  async function perform(action: () => Promise<void>) {
    setBusy(true); setError('');
    try { await action(); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }

  async function save(): Promise<Draft> {
    const values = form.getFieldsValue(true);
    if (!values.title?.trim() || !values.template_key || !values.host) throw new Error('请填写方案名称、产品模板和主机');
    const [product_id, template_id] = values.template_key.split(':');
    const parameters = JSON.parse(values.parameters_text || '{}') as Record<string, unknown>;
    if (!parameters || typeof parameters !== 'object' || Array.isArray(parameters)) throw new Error('高级参数需要 JSON 对象');
    const spec: Spec = { title: values.title, product_id, template_id, mode: values.mode,
      host: values.host, hosts: values.mode === 'import' ? [] : hosts.filter((item) => item.name && item.address),
      home: values.home || '', data_root: values.data_root || '', license_file: values.license_file || '',
      base_port: values.base_port, parameters, nodes: nodes.map(({ name, host, port, data_dir }) => ({ name, host: hosts.find((item) => item.address === host)?.name || '', port, data_dir })),
      source_yaml: values.source_yaml || '', target: values.target || '' };
    const saved = await api<Draft>(draft ? `/deployment/drafts/${draft.id}` : '/deployment/drafts', {
      method: draft ? 'PUT' : 'POST', body: JSON.stringify({ spec, expected_revision: draft?.revision || 0 }),
    });
    setDraft(saved); setPlan(null); setConfirmed(false); return saved;
  }

  async function discover() {
    const values = form.getFieldsValue(true);
    const found = await api<{ installations: Installation[] }>('/deployment/discover', { method: 'POST',
      body: JSON.stringify({ host: values.host, home: values.home || '', data_dir: values.probe_data_dir || '' }) });
    setInstallations(found.installations);
    const complete = found.installations.filter((item) => item.complete);
    if (complete.length === 1) form.setFieldValue('home', complete[0].home);
    if (!found.installations.length) message.info('未发现安装，请填写目标主机的安装目录后重新探测');
  }

  async function next() {
    if (step === 0) { await save(); setStep(1); }
    else if (step === 1) {
      const saved = await save();
      const layout = await api<{ nodes: Node[]; target: string }>(`/deployment/drafts/${saved.id}/layout`, { method: 'POST' });
      setNodes(layout.nodes); setStep(2);
    } else if (step === 2) {
      const saved = await save();
      const generated = await api<Plan>(`/deployment/drafts/${saved.id}/plan`, { method: 'POST' });
      setPlan(generated); setStep(3);
    }
  }

  async function previewFiles() {
    if (!plan) return;
    const response = await fetch(`/api/v1/deployment/plans/${plan.id}/files/pgcluster.yaml`);
    if (!response.ok) throw new Error('读取生成文件失败');
    setPreview(await response.text());
  }

  async function apply(deploy: boolean) {
    if (!plan || !draft) return;
    if (deploy) {
      const task = await api<Task>(`/deployment/plans/${plan.id}/apply`, { method: 'POST', body: JSON.stringify({ acknowledge_change: confirmed }) });
      await onSaved(draft.id); onClose(); openTask(task.id);
    } else {
      await api(`/deployment/plans/${plan.id}/associate`, { method: 'POST' });
      await onSaved(draft.id); onClose(); message.success('部署文件已自动关联环境');
    }
  }

  return <Modal title="部署方案工作台" open={open} onCancel={onClose} width={1100} footer={null} maskClosable={!busy}>
    <Steps size="small" current={step} items={['方案与来源', '主机与安装', '实例配置', '检查与部署'].map((title) => ({ title }))} style={{ marginBottom: 24 }} />
    {error && <Alert type="error" message={error} style={{ marginBottom: 16 }} />}
    <Form form={form} layout="vertical" disabled={busy} onValuesChange={(changes) => {
      if ('data_root' in changes || 'base_port' in changes || 'template_key' in changes) setNodes([]);
    }}>
      <div style={{ display: step === 0 ? 'block' : 'none' }}>
        {!environment && <Form.Item label="继续已有草稿">
          <Select aria-label="继续已有草稿" placeholder="选择草稿，或直接填写新方案" allowClear value={draft?.id} options={drafts.map((item) => ({ value: item.id, label: item.spec.title }))}
            onChange={(id) => { const selected = drafts.find((item) => item.id === id); if (selected) loadDraft(selected); else { setDraft(null); setNodes([]); } }} />
        </Form.Item>}
        <Form.Item label="方案名称" name="title"><Input placeholder="例如：等保开发环境" /></Form.Item>
        <Form.Item label="部署模板" name="template_key"><Select disabled={Boolean(environment)} options={templates.map((item) => ({ value: `${item.product_id}:${item.id}`, label: `${item.product_title} · ${item.title}` }))}
          onChange={(key) => { const selected = templates.find((item) => `${item.product_id}:${item.id}` === key); form.setFieldValue('base_port', selected?.base_port); setNodes([]); }} /></Form.Item>
        <Form.Item label="配置来源" name="mode"><Radio.Group options={[{ value: 'new', label: '新建实例' }, { value: 'adopt', label: '接管已有实例' }, { value: 'import', label: '导入部署 YAML' }]} onChange={() => setNodes([])} /></Form.Item>
        <Alert type="info" message={draft ? `稳定环境 ID：${draft.id}` : '环境 ID 会自动生成；名称修改不会改变测试、任务和报告的关联。'} />
      </div>
      <div style={{ display: step === 1 ? 'block' : 'none' }}>
        <Form.Item label="目标主机" name="host" extra="本机使用 127.0.0.1；远程使用声明的 SSH 凭据或当前用户的 SSH 配置／agent，目标主机需有 Python 3。"><Input /></Form.Item>
        {mode !== 'import' && <Collapse style={{ marginBottom: 16 }} items={[{ key: 'hosts', label: '多主机与 SSH 凭据（可选）', children: <>
          <Alert type="info" style={{ marginBottom: 8 }}
            message="声明多台主机后可在实例步骤逐节点分配；主表单的主机地址必须出现在主机资源中。凭据只填密钥文件路径，不写口令。" />
          <Table rowKey={(_, index) => String(index)} size="small" pagination={false} dataSource={hosts} columns={[
            { title: '名称', render: (_, host, index) => <Input value={host.name} placeholder="host1" onChange={(e) => setHosts((prev) => prev.map((item, i) => i === index ? { ...item, name: e.target.value } : item))} /> },
            { title: '地址', render: (_, host, index) => <Input value={host.address} placeholder="192.168.x.x" onChange={(e) => setHosts((prev) => prev.map((item, i) => i === index ? { ...item, address: e.target.value } : item))} /> },
            { title: 'SSH 用户', render: (_, host, index) => <Input value={host.ssh_user} onChange={(e) => setHosts((prev) => prev.map((item, i) => i === index ? { ...item, ssh_user: e.target.value } : item))} /> },
            { title: 'SSH 端口', render: (_, host, index) => <InputNumber value={host.ssh_port} min={1} max={65535} onChange={(port) => setHosts((prev) => prev.map((item, i) => i === index ? { ...item, ssh_port: port } : item))} /> },
            { title: '密钥文件', render: (_, host, index) => <Input value={host.ssh_identity_file} placeholder="/home/postgres/.ssh/id_ed25519" onChange={(e) => setHosts((prev) => prev.map((item, i) => i === index ? { ...item, ssh_identity_file: e.target.value } : item))} /> },
            { title: '安装目录', render: (_, host, index) => <Input value={host.home} placeholder="留空用统一安装目录" onChange={(e) => setHosts((prev) => prev.map((item, i) => i === index ? { ...item, home: e.target.value } : item))} /> },
            { title: '', render: (_, _host, index) => <Button size="small" danger onClick={() => setHosts((prev) => prev.filter((_, i) => i !== index))}>删除</Button> },
          ]} />
          <Button size="small" onClick={() => setHosts((prev) => [...prev, { name: `host${prev.length + 1}`, address: '', ssh_user: '', ssh_identity_file: '', ssh_connect_timeout: 10, home: '' }])}>添加主机</Button>
        </> }]} />}
        {mode === 'import' ? <>
          <Form.Item label="部署文件内容" name="source_yaml"><Input.TextArea rows={12} /></Form.Item>
          <Space wrap style={{ marginBottom: 12 }}>
            <input type="file" accept=".yaml,.yml" aria-label="导入部署文件" onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void perform(async () => { form.setFieldValue('source_yaml', await file.text()); setImportTargets([]); });
            }} />
            <Button onClick={() => void perform(async () => {
              const targets = await api<Array<{ value: string; label: string }>>('/deployment/import-targets', {
                method: 'POST', body: JSON.stringify({ source_yaml: form.getFieldValue('source_yaml') }),
              });
              setImportTargets(targets);
              if (targets.length === 1) form.setFieldValue('target', targets[0].value);
            })}>解析部署目标</Button>
          </Space>
          <Form.Item label="集群目标" name="target"><Select options={importTargets} placeholder="解析文件后选择集群" /></Form.Item>
          <Alert type="info" message="导入只接管已有数据库，不会初始化或清理数据；首批支持同一主机。" />
        </> : <>
          <Form.Item label="已有实例数据目录（可选）" name="probe_data_dir" extra="用于读取历史启动参数，帮助发现已停止实例使用的安装。"><Input placeholder="例如 /home/postgres/pgdata/mac1" /></Form.Item>
          <Space wrap style={{ marginBottom: 12 }}><Button onClick={() => void perform(discover)}>探测数据库安装</Button>
            {installations.length > 0 && <Select style={{ minWidth: 420 }} placeholder="选择探测到的安装"
              options={installations.map((item) => ({ value: item.home, disabled: !item.complete, label: `${item.home} · ${item.version || '工具不完整'} · ${item.sources.join('／')}` }))}
              onChange={(home) => form.setFieldValue('home', home)} />}</Space>
          <Form.Item label="数据库安装目录（PGHOME）" name="home" extra="填写安装根目录，平台自动推导 bin、扩展和工具位置。"><Input placeholder="/usr/local/fbase15.15" /></Form.Item>
          <Form.Item label="数据根目录" name="data_root" extra="目标主机上的目录；节点目录会自动生成，也可下一步逐节点修改。"><Input placeholder="/home/postgres/pgdata/my-cluster" /></Form.Item>
          <Form.Item label="License 文件" name="license_file"><Input placeholder="目标主机上的 License 绝对路径" /></Form.Item>
          <Form.Item label="主节点起始端口" name="base_port"><InputNumber min={1024} max={65535} /></Form.Item>
        </>}
      </div>
      <div style={{ display: step === 2 ? 'block' : 'none' }}>
        <Alert type="info" message="固定回归模板保留节点名称与复制关系；可调整端口和数据目录。" style={{ marginBottom: 12 }} />
        <Table rowKey="name" pagination={false} size="small" scroll={{ x: 700 }} dataSource={nodes} columns={[
          { title: '节点', dataIndex: 'name' },
          { title: '主机', render: (_, node, index) => hosts.length
              ? <Select size="small" style={{ minWidth: 170 }} disabled={mode === 'import'} value={node.host}
                  options={hosts.map((item) => ({ value: item.address, label: `${item.name} · ${item.address}` }))}
                  onChange={(address) => setNodes((prev) => prev.map((item, i) => i === index ? { ...item, host: address } : item))} />
              : node.host },
          { title: '端口', render: (_, node, index) => <InputNumber disabled={mode === 'import'} value={node.port} min={1024} max={65535} onChange={(port) => setNodes((prev) => prev.map((item, i) => i === index ? { ...item, port: port || 1024 } : item))} /> },
          { title: '数据目录', render: (_, node, index) => <Input disabled={mode === 'import'} value={node.data_dir} onChange={(event) => setNodes((prev) => prev.map((item, i) => i === index ? { ...item, data_dir: event.target.value } : item))} /> },
        ]} />
        <Collapse style={{ marginTop: 16 }} items={[{ key: 'parameters', label: '高级数据库参数', children:
          <Form.Item name="parameters_text" extra={'JSON 对象，例如 {"max_connections": 200}；结构参数由拓扑管理。'}><Input.TextArea rows={5} disabled={mode === 'import'} /></Form.Item> }]} />
      </div>
    </Form>
    {step === 3 && plan && <>
      <Alert type={plan.ready ? 'success' : 'error'} message={plan.ready ? '检查通过，可关联方案或提交执行' : '检查未通过，请返回修改后重新生成'} style={{ marginBottom: 16 }} />
      <Table rowKey={(_, index) => String(index)} size="small" pagination={false} dataSource={plan.checks} scroll={{ y: 260 }} columns={[
        { title: '检查', dataIndex: 'title' }, { title: '主机', dataIndex: 'host' },
        { title: '结果', render: (_, check) => <Tag color={check.ok ? 'success' : 'error'}>{check.ok ? '通过' : '失败'}</Tag> },
        { title: '详情', render: (_, check) => <Typography.Text style={{ overflowWrap: 'anywhere' }}>{typeof check.detail === 'string' ? check.detail : JSON.stringify(check.detail)}</Typography.Text> },
      ]} />
      {plan.mode === 'diff' && (
        <Alert
          type={plan.executable ? 'warning' : 'error'}
          message={plan.executable ? '集群差异计划：将向既有集群新增节点' : '差异包含暂不支持自动执行的操作，请调整方案'}
          style={{ marginBottom: 16 }}
        />
      )}
      <Typography.Title level={5}>操作预览 · {plan.target}</Typography.Title>
      <Table rowKey="node" size="small" pagination={false} dataSource={plan.operations} scroll={{ x: 650 }} columns={[
        ...(plan.mode === 'diff' ? [{ title: '类型', render: (_: unknown, op: { kind?: string }) => <Tag>{({ add_standby: '扩容', add_node: '新增', remove_node: '缩容', change_node: '变更', parameters: '参数', topology: '拓扑' } as Record<string, string>)[op.kind || ''] || '操作'}</Tag> }] : []),
        { title: '节点', dataIndex: 'node' }, { title: '端口', dataIndex: 'port' }, { title: '数据目录', dataIndex: 'data_dir' }, { title: '将执行', dataIndex: 'operation' },
        ...(plan.mode === 'diff' ? [{ title: '可执行', render: (_: unknown, op: { executable?: boolean }) => <Tag color={op.executable ? 'success' : 'default'}>{op.executable ? '是' : '暂不支持'}</Tag> }] : []),
      ]} />
      <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>{plan.limitations.join('；')}</Typography.Paragraph>
      <Space wrap><Button onClick={() => void perform(previewFiles)}>查看生成 YAML</Button>
        {Object.keys(plan.files).map((name) => <Button key={name} href={`/api/v1/deployment/plans/${plan.id}/files/${encodeURIComponent(name)}`}>下载 {name}</Button>)}</Space>
      {preview && <pre className="raw-report" style={{ maxHeight: 300, overflow: 'auto' }}>{preview}</pre>}
      {plan.action === 'deployment.create' && <div style={{ marginTop: 16 }}><Checkbox checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)}>{plan.mode === 'diff' ? '确认按上述方案在既有集群上新增节点，并在完成后进行健康验收' : '确认按上述方案初始化新实例，并在完成后进行健康验收'}</Checkbox></div>}
    </>}
    <Space wrap style={{ marginTop: 24 }}>
      <Button disabled={busy || step === 0} onClick={() => { setStep((prev) => prev - 1); setPlan(null); setConfirmed(false); }}>上一步</Button>
      {step < 3 && <Button disabled={busy} onClick={() => void perform(async () => { await save(); message.success('草稿已保存'); })}>保存草稿</Button>}
      {step < 3 ? <Button type="primary" loading={busy} onClick={() => void perform(next)}>{step === 2 ? '检查并生成部署计划' : '下一步'}</Button> : <>
        <Button disabled={!plan?.ready || plan?.executable === false || busy} onClick={() => void perform(() => apply(false))}>仅关联环境</Button>
        <Button type="primary" loading={busy} disabled={!plan?.ready || plan?.executable === false || (plan?.action === 'deployment.create' && !confirmed)} onClick={() => void perform(() => apply(true))}>{plan?.action === 'deployment.create' ? '按计划部署并验收' : '接管并检查健康'}</Button>
      </>}
    </Space>
  </Modal>;
}
