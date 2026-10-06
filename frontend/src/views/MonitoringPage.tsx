import { Empty, Select, Space, Typography } from 'antd';
import DatabaseMonitoring from '../components/DatabaseMonitoring';
import type { Environment } from '../platform/api';
export default function MonitoringPage({ environment, environments, onSelectEnvironment, onOpenStudio, onOpenTask }: { environment?: Environment; environments: Environment[]; onSelectEnvironment: (id: string) => void; onOpenStudio: (nodeId: string) => void; onOpenTask: (taskId: string) => void }) {
  return <><Space wrap style={{ marginBottom: 16 }}><Typography.Title level={3} style={{ margin: 0 }}>数据库监控</Typography.Title><Select aria-label="监控环境" value={environment?.id} options={environments.map(e => ({ value: e.id, label: e.title }))} onChange={onSelectEnvironment} style={{ minWidth: 240 }} /></Space>{environment ? <DatabaseMonitoring key={environment.id} environmentId={environment.id} onOpenStudio={onOpenStudio} onOpenTask={onOpenTask} /> : <Empty description="选择数据库环境" />}</>;
}
