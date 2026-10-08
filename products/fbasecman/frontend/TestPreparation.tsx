import { useEffect, useState } from 'react';
import { Alert, App, Button, Space, Tag } from 'antd';
import { ExperimentOutlined, ReloadOutlined } from '@ant-design/icons';
import { api, operationRequest, type Environment, type Task } from '../../../frontend/src/platform/api';

type Profile = { generated: boolean; context_ready: boolean };
type Props = { environment: Environment; profileId?: string; tasks: Task[];
  onTask: (taskId: string) => void; onChanged: () => Promise<void> | void };

export default function TestPreparation({ environment, profileId, tasks, onTask, onChanged }: Props) {
  const { message, modal } = App.useApp();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const related = tasks.filter(task => task.environment_id === environment.id);
  const taskStates = related.map(task => `${task.id}:${task.status}`).join(',');
  const active = related.some(task => ['QUEUED', 'RUNNING', 'CANCELLING'].includes(task.status));

  useEffect(() => {
    let disposed = false;
    setProfile(null);
    setError('');
    api<Profile>(`/environments/${encodeURIComponent(environment.id)}/fbasecman-profile`)
      .then(value => { if (!disposed) setProfile(value); })
      .catch(cause => { if (!disposed) setError((cause as Error).message); });
    return () => { disposed = true; };
  }, [environment.id, taskStates, revision]);

  if (profileId !== 'cman') return null;

  function prepare() {
    modal.confirm({
      title: `准备公共测试资源 · ${environment.title}`,
      content: <><p>将创建缺失的测试账号、测试表和 test_db，更新公共 MMR 视图、同步 u3 密码，并准备 test_db 的 MMR 关系及适用的 Citus 扩展。</p>
        <p>还会创建备库物理复制槽，修改备库的 primary_conninfo、primary_slot_name 并重载配置，最后采集测试上下文。已有同名对象会被复用，公共资源保留供后续用例使用。</p>
        <p>此操作用于专用回归环境，不表示所有用例前置条件均满足；每个用例仍需执行自己的检查、准备与清理。</p></>,
      okText: '确认修改并准备', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        setBusy(true);
        try {
          const task = await operationRequest(environment.id, 'tests.prepare_fbasecman', 'all', { cluster: 'cman' }, true);
          onTask(task.id);
          await onChanged();
          setRevision(value => value + 1);
        } catch (cause) {
          message.error((cause as Error).message);
        } finally { setBusy(false); }
      },
    });
  }

  return <Alert style={{ marginBottom: 18 }} type="info" showIcon
    message={<Space wrap><span>fbasecman 公共测试准备</span>
      <Tag>{error ? '状态获取失败' : !profile ? '读取状态中' : profile.context_ready ? '已记录测试上下文' : '未记录测试上下文'}</Tag></Space>}
    description={<><p>部分用例依赖公共测试账号、测试库及上下文。各用例的专属配置、数据和恢复仍由用例管理；上下文存在不代表集群或全部用例已就绪。</p>
      {error && <p>{error}</p>}
      {profile && !profile.generated && <p>请先在数据库部署页配置本回归环境的部署方案。</p>}
      <Space wrap><Button icon={<ExperimentOutlined />} loading={busy}
        disabled={!profile?.generated || active || busy} onClick={prepare}>准备公共测试资源</Button>
        <Button icon={<ReloadOutlined />} onClick={() => setRevision(value => value + 1)}>刷新准备状态</Button>
        {active && <span>环境有未结束的任务，暂不能准备。</span>}</Space></>}
  />;
}
