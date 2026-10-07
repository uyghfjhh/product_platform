import React, { useMemo, useState } from 'react';
import { Button, Empty, Input, Segmented, Space } from 'antd';
import {
  SearchOutlined,
  DownSquareOutlined,
  UpSquareOutlined,
} from '@ant-design/icons';
import { normalizeStep, type NormalizedStep } from './stepNormalizer';
import { StepCard } from './StepCard';
import './steps.css';

interface StepListProps {
  steps: any[];
  emptyDescription?: string;
  evidenceBasePath?: string;
}

export const StepList: React.FC<StepListProps> = ({
  steps,
  emptyDescription = '本次执行没有记录步骤，可查看执行结论及原始报告',
  evidenceBasePath,
}) => {
  const [filterType, setFilterType] = useState<string>('all');
  const [search, setSearch] = useState<string>('');
  const [allExpanded, setAllExpanded] = useState<boolean>(true);

  // 1. 规范化所有步骤
  const normalizedSteps: NormalizedStep[] = useMemo(() => {
    if (!Array.isArray(steps)) return [];
    return steps.map((raw, idx) => normalizeStep(raw, idx));
  }, [steps]);

  // 2. 过滤与搜索
  const filteredSteps = useMemo(() => {
    return normalizedSteps.filter((step) => {
      // 类型过滤
      if (filterType === 'fail' && step.status !== 'FAIL') return false;
      if (filterType === 'verify' && step.kind !== 'verify') return false;
      if (filterType === 'action' && step.kind !== 'action') return false;
      if (filterType === 'diff' && step.kind !== 'diff') return false;

      // 搜索文本过滤
      if (search.trim()) {
        const q = search.trim().toLowerCase();
        const inTitle = step.title.toLowerCase().includes(q);
        const inCmd = (step.command || '').toLowerCase().includes(q);
        const inExp = (step.expected || '').toLowerCase().includes(q);
        const inAct = (step.actualSummary || '').toLowerCase().includes(q);
        const inEv = step.evidenceItems.some((e) => e.value.toLowerCase().includes(q));
        if (!inTitle && !inCmd && !inExp && !inAct && !inEv) return false;
      }

      return true;
    });
  }, [normalizedSteps, filterType, search]);

  if (!normalizedSteps.length) {
    return <Empty description={emptyDescription} style={{ margin: '32px 0' }} />;
  }

  const failCount = normalizedSteps.filter((s) => s.status === 'FAIL').length;
  const verifyCount = normalizedSteps.filter((s) => s.kind === 'verify').length;
  const actionCount = normalizedSteps.filter((s) => s.kind === 'action').length;
  const diffCount = normalizedSteps.filter((s) => s.kind === 'diff').length;

  return (
    <div className="universal-steps-container">
      {/* 步骤工具栏：类型分段选择器 + 搜索 + 全部展开/折叠 */}
      <div className="steps-toolbar">
        <Space wrap>
          <Segmented
            value={filterType}
            onChange={(val) => setFilterType(val as string)}
            options={[
              { label: `全部 (${normalizedSteps.length})`, value: 'all' },
              ...(failCount > 0 ? [{ label: `🚨 失败 (${failCount})`, value: 'fail' }] : []),
              { label: `状态验证 (${verifyCount})`, value: 'verify' },
              { label: `执行动作 (${actionCount})`, value: 'action' },
              ...(diffCount > 0 ? [{ label: `配置比对 (${diffCount})`, value: 'diff' }] : []),
            ]}
          />
          <Input
            placeholder="搜索步骤命令、期望、证据..."
            prefix={<SearchOutlined style={{ color: '#64748b' }} />}
            allowClear
            size="small"
            style={{ width: 220 }}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </Space>

        <Space>
          <Button
            size="small"
            icon={<DownSquareOutlined />}
            onClick={() => setAllExpanded(true)}
            style={{ fontSize: 12 }}
          >
            全部展开
          </Button>
          <Button
            size="small"
            icon={<UpSquareOutlined />}
            onClick={() => setAllExpanded(false)}
            style={{ fontSize: 12 }}
          >
            全部收起
          </Button>
        </Space>
      </div>

      {/* 步骤卡片列表 */}
      {filteredSteps.length === 0 ? (
        <Empty description="没有匹配条件的步骤" style={{ margin: '24px 0' }} />
      ) : (
        filteredSteps.map((step) => (
          <StepCard key={`${step.key}-${allExpanded}`} step={step}
            defaultExpanded={allExpanded && step.kind !== 'action'}
            evidenceBasePath={evidenceBasePath} />
        ))
      )}
    </div>
  );
};
