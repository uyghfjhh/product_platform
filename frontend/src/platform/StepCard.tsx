import React, { useState } from 'react';
import { Button, Tag, message } from 'antd';
import {
  CopyOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  DownOutlined,
  UpOutlined,
  CodeOutlined,
  TableOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import type { NormalizedStep } from './stepNormalizer';

interface StepCardProps {
  step: NormalizedStep;
  defaultExpanded?: boolean;
  evidenceBasePath?: string;
}

export const StepCard: React.FC<StepCardProps> = ({ step, defaultExpanded = true, evidenceBasePath }) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [showRawTable, setShowRawTable] = useState(false);
  const [logExpanded, setLogExpanded] = useState(true);
  const [techOpen, setTechOpen] = useState(false);

  const copyToClipboard = (text: string, label = '内容') => {
    void navigator.clipboard.writeText(text);
    message.success(`${label}已复制到剪贴板`);
  };

  const isPass = step.status === 'PASS';
  const isFail = step.status === 'FAIL';
  const summaryIsVerdict = /[✅❌]/.test(step.actualSummary || '');
  // text 载荷与判定明细同源且为判点文本时，载荷框让位给断言判定块
  const verdictReplacesPayload =
    step.actualPayload?.type === 'text' &&
    step.actualPayload.raw === step.actualSummary &&
    summaryIsVerdict;

  return (
    <div className={`step-card-root ${isPass ? 'pass' : isFail ? 'fail' : 'pending'}`}>
      {/* 1. 顶部标题栏 */}
      <div className="step-header">
        <div className="step-header-left">
          <span className="step-order-badge">#{step.order}</span>
          <span className={`step-kind-badge ${step.kind}`}>
            {step.kind === 'action' ? '执行动作' : step.kind === 'verify' ? '状态验证' : '配置比对'}
          </span>
          <span className="step-title-text">{step.cleanTitle || step.title}</span>
        </div>

        <div className="step-header-right">
          {step.telemetry?.elapsed && (
            <span className="step-telemetry-pill">
              ⏱ {step.telemetry.elapsed}
              {step.telemetry.attempts ? ` (${step.telemetry.attempts}次)` : ''}
            </span>
          )}
          <Tag
            color={isPass ? 'success' : isFail ? 'error' : 'default'}
            icon={isPass ? <CheckCircleOutlined /> : isFail ? <CloseCircleOutlined /> : undefined}
            style={{ margin: 0, fontWeight: 700 }}
          >
            {step.status}
          </Tag>
          <Button
            type="text"
            size="small"
            icon={expanded ? <UpOutlined /> : <DownOutlined />}
            onClick={() => setExpanded(!expanded)}
            style={{ color: '#94a3b8' }}
          />
        </div>
      </div>

      {/* 2. 折叠主体内容 */}
      {expanded && (
        <div className="step-body">
          {/* 上下文环境标签 (端口 / 配置文件 / 执行节点) */}
          {step.context && Object.keys(step.context).length > 0 && (
            <div className="step-context-bar">
              {Object.entries(step.context).map(([k, v]) => (
                <span className="step-context-pill" key={k}>
                  <b>{k}:</b> {v}
                </span>
              ))}
            </div>
          )}

          {/* 执行动作说明 */}
          {step.action && (
            <div style={{ color: '#cbd5e1', fontSize: '13px', lineHeight: 1.5 }}>
              <span style={{ color: '#94a3b8', marginRight: 6 }}>动作:</span>
              {step.action}
            </div>
          )}

          {/* 执行命令 (Command Box) */}
          {step.command && (
            <div className="step-command-box">
              <div className="step-command-header">
                <span>⚡ 执行命令</span>
                <Button
                  type="text"
                  size="small"
                  icon={<CopyOutlined />}
                  style={{ color: '#94a3b8', fontSize: 11 }}
                  onClick={() => copyToClipboard(step.command!, '命令')}
                >
                  复制
                </Button>
              </div>
              <pre>{step.command}</pre>
            </div>
          )}

          {/* 验证步骤专用：预期准则 vs 实际输出工作台 (Verification Workbench) */}
          {step.kind === 'verify' && (
            <div className="step-verification-workbench">
              {/* 关键期望 (Expected) */}
              {step.expected && (
                <div className="step-expected-card">
                  <div className="step-section-label expected">
                    <span>🎯 关键期望 (Expected Criteria)</span>
                  </div>
                  <div className="step-expected-content">{step.expected}</div>
                </div>
              )}

              {/* 实际输出载荷 (Actual Output / State Table) */}
              {step.actualPayload && !verdictReplacesPayload && (
                <div className="step-actual-card">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div className="step-section-label actual">
                      <span>📊 实际输出 (Actual Payload)</span>
                    </div>
                    {step.actualPayload.parsedTable && (
                      <Button
                        type="text"
                        size="small"
                        icon={showRawTable ? <TableOutlined /> : <CodeOutlined />}
                        style={{ color: '#38bdf8', fontSize: 11 }}
                        onClick={() => setShowRawTable(!showRawTable)}
                      >
                        {showRawTable ? '表格视图' : '原始控制台'}
                      </Button>
                    )}
                  </div>

                  {/* 结构化表格渲染 */}
                  {step.actualPayload.parsedTable && !showRawTable ? (
                    <div className="step-table-container">
                      <table className="step-ascii-table">
                        <thead>
                          <tr>
                            {step.actualPayload.parsedTable.headers.map((h, i) => (
                              <th key={i}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {step.actualPayload.parsedTable.rows.map((row, ri) => (
                            <tr key={ri}>
                              {row.map((col, ci) => (
                                <td key={ci}>{col}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {step.actualPayload.parsedTable.footer && (
                        <div className="step-table-footer">{step.actualPayload.parsedTable.footer}</div>
                      )}
                    </div>
                  ) : (
                    /* 原始控制台文本 */
                    <div className="step-command-box" style={{ marginTop: 4 }}>
                      <pre style={{ color: '#e2e8f0', fontSize: 11 }}>
                        {step.actualPayload.raw}
                      </pre>
                    </div>
                  )}
                </div>
              )}

              {/* 断言判定明细：逐判点 ✅/❌ 核对结果。表格/diff 载荷下并列渲染；
                  判点文本载荷则由本块替代渲染（verdictReplacesPayload）。 */}
              {step.actualSummary &&
                !step.actualSummary.startsWith('returncode=') &&
                (verdictReplacesPayload ||
                  (step.actualPayload?.type !== 'text' &&
                    step.actualSummary !== step.actualPayload?.raw)) && (
                <div className="step-verdict-card">
                  <div className="step-section-label verdict">
                    <span>� 断言判定 (Assertion Verdict)</span>
                  </div>
                  <div className="step-verdict-lines">
                    {step.actualSummary.split('\n').map((line, i) => {
                      const trimmed = line.trim();
                      const cls = trimmed.startsWith('✅') ? 'ok'
                        : trimmed.startsWith('❌') ? 'bad'
                        : trimmed ? 'plain' : 'blank';
                      return <div key={i} className={`verdict-line ${cls}`}>{line || ' '}</div>;
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

          {step.analysis && (
            <div className="step-analysis-card single">
              <div>
                <div className="step-section-label verdict">结果分析</div>
                <div className={isPass ? 'analysis-pass' : 'analysis-fail'}>{step.analysis}</div>
              </div>
            </div>
          )}

          {/* 配置比对步骤专用：Diff 视图 (Diff Workbench) */}
          {step.kind === 'diff' && step.actualPayload && (
            <div className="step-diff-workbench">
              {/* 关键期望 */}
              {step.expected && (
                <div className="step-expected-card">
                  <div className="step-section-label expected">
                    <span>🎯 比对期望 (Expected Criteria)</span>
                  </div>
                  <div className="step-expected-content">{step.expected}</div>
                </div>
              )}

              {/* 语义变化标签 (Semantic Changes) */}
              {step.actualPayload.semanticChanges && step.actualPayload.semanticChanges.length > 0 && (
                <div>
                  <div className="step-section-label" style={{ color: '#fbbf24' }}>
                    <span>⚡ 识别到的语义变更 (Semantic Changes)</span>
                  </div>
                  <div className="step-semantic-tags">
                    {step.actualPayload.semanticChanges.map((sc, i) => (
                      <span className="semantic-tag" key={i}>
                        <span className="field">{sc.field}:</span>
                        <span className="from">{sc.from}</span>
                        <span className="arrow">➔</span>
                        <span className="to">{sc.to}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* 语法高亮 Diff 视窗 */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                  <div className="step-section-label" style={{ color: '#94a3b8' }}>
                    <span>📄 Unified Diff</span>
                  </div>
                  <Button
                    type="text"
                    size="small"
                    icon={<CopyOutlined />}
                    style={{ color: '#94a3b8', fontSize: 11 }}
                    onClick={() => copyToClipboard(step.actualPayload!.raw, 'Diff')}
                  >
                    复制 Diff
                  </Button>
                </div>
                <div className="step-diff-box">
                  {step.actualPayload.raw.split('\n').map((line, idx) => {
                    const isAdd = line.startsWith('+') && !line.startsWith('+++');
                    const isDel = line.startsWith('-') && !line.startsWith('---');
                    const isInfo = line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++');
                    const cls = isAdd ? 'add' : isDel ? 'del' : isInfo ? 'info' : '';
                    return (
                      <div key={idx} className={`diff-line ${cls}`}>
                        {line || ' '}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}

          {/* 纯操作步骤如果只有即时反馈 */}
          {step.kind === 'action' && step.actualSummary && !step.actualSummary.startsWith('returncode=') && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#94a3b8' }}>
              <span>即时状态:</span>
              <Tag color="cyan" style={{ margin: 0 }}>{step.actualSummary}</Tag>
            </div>
          )}

          {/* 3. 技术附件：原始工件与日志证据，默认折叠 */}
          {step.evidenceItems && step.evidenceItems.length > 0 && (
            <div className="step-tech-attachment">
              <div
                className="step-tech-attachment-header"
                onClick={() => setTechOpen(!techOpen)}
              >
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <FileTextOutlined />
                  <span>技术附件</span>
                  <span style={{ opacity: 0.6, fontSize: 11 }}>原始工件 / 日志证据</span>
                </span>
                <Button
                  type="text"
                  size="small"
                  icon={techOpen ? <UpOutlined /> : <DownOutlined />}
                  style={{ color: '#94a3b8' }}
                />
              </div>
              {techOpen && (
                <div className="step-tech-attachment-body">
              {/* 工件引用药丸 */}
              {step.evidenceItems.filter((e) => e.type === 'artifact').length > 0 && (
                <div className="step-evidence-pills">
                  {step.evidenceItems
                    .filter((e) => e.type === 'artifact')
                    .map((item, idx) => {
                      const content = <><FileTextOutlined /><span>{item.value}</span></>;
                      if (!evidenceBasePath) return <span className="evidence-artifact-pill" key={idx} title="测试工件路径">{content}</span>;
                      const reference = item.value.split('/').map(encodeURIComponent).join('/');
                      return <a className="evidence-artifact-pill" key={idx}
                        href={`/api/v1${evidenceBasePath}/${reference}`} target="_blank" rel="noreferrer"
                        title="打开原始证据">{content}</a>;
                    })}
                </div>
              )}

              {/* 日志证据视窗 */}
              {step.evidenceItems.filter((e) => e.type === 'log' || e.type === 'text').length > 0 && (
                <div className="step-log-evidence-box">
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '4px 10px',
                      background: '#0d1527',
                      borderBottom: '1px solid #1a263d',
                      fontSize: 11,
                      color: '#fbbf24',
                      cursor: 'pointer',
                    }}
                    onClick={() => setLogExpanded(!logExpanded)}
                  >
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span>📜 日志证据 (Log Evidence)</span>
                    </span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <Button
                        type="text"
                        size="small"
                        icon={<CopyOutlined />}
                        style={{ color: '#fbbf24', fontSize: 11 }}
                        onClick={(e) => {
                          e.stopPropagation();
                          const allLog = step.evidenceItems
                            .filter((ev) => ev.type === 'log' || ev.type === 'text')
                            .map((ev) => ev.value)
                            .join('\n\n');
                          copyToClipboard(allLog, '日志证据');
                        }}
                      >
                        复制日志
                      </Button>
                      <Button
                        type="text"
                        size="small"
                        icon={logExpanded ? <UpOutlined /> : <DownOutlined />}
                        style={{ color: '#fbbf24' }}
                      />
                    </div>
                  </div>
                  {logExpanded && (
                    <pre>
                      {step.evidenceItems
                        .filter((e) => e.type === 'log' || e.type === 'text')
                        .map((e) => e.value)
                        .join('\n\n')}
                    </pre>
                  )}
                </div>
              )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
