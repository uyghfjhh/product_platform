import { useEffect, useState } from 'react';
import { Alert, Button, Drawer, List, Typography } from 'antd';
import { DownloadOutlined, FileTextOutlined } from '@ant-design/icons';

import { api } from './api';

type EvidenceIndex = { target: string; execution_id: string; verdict: string; evidence: string[] };

export default function EvidenceDrawer({ environmentId, target, onClose }: {
  environmentId?: string;
  target: string | null;
  onClose: () => void;
}) {
  const [index, setIndex] = useState<EvidenceIndex | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!environmentId || !target) { setIndex(null); return; }
    let active = true;
    setIndex(null);
    setError('');
    setLoading(true);
    void api<EvidenceIndex>(`/environments/${encodeURIComponent(environmentId)}/results/${encodeURIComponent(target)}/evidence`)
      .then((value) => { if (active) setIndex(value); })
      .catch((cause) => { if (active) setError((cause as Error).message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [environmentId, target]);

  const base = environmentId && target
    ? `/api/v1/environments/${encodeURIComponent(environmentId)}/results/${encodeURIComponent(target)}/evidence/`
    : '';

  const bundleHref = environmentId && target
    ? `/api/v1/environments/${encodeURIComponent(environmentId)}/results/${encodeURIComponent(target)}/bundle`
    : '';

  return <Drawer title="执行证据" open={Boolean(target)} onClose={onClose} width={560}
    extra={target && <Button size="small" icon={<DownloadOutlined />} href={bundleHref}
      title="打包环境记录、结果、证据与报告">故障分析包</Button>}>
    <Typography.Text type="secondary" style={{ overflowWrap: 'anywhere' }}>{target}</Typography.Text>
    {error && <Alert type="error" message={error} style={{ marginTop: 16 }} />}
    <List loading={loading} style={{ marginTop: 16 }} dataSource={index?.evidence || []}
      locale={{ emptyText: '暂无归档证据' }}
      renderItem={(reference) => <List.Item actions={[<a key="download"
        href={base + reference.split('/').map(encodeURIComponent).join('/')}
        title="下载原始证据"><DownloadOutlined /></a>]}>
        <List.Item.Meta avatar={<FileTextOutlined />} title={reference.split('/').at(-1)}
          description={<Typography.Text type="secondary" style={{ overflowWrap: 'anywhere' }}>{reference}</Typography.Text>} />
      </List.Item>} />
  </Drawer>;
}
