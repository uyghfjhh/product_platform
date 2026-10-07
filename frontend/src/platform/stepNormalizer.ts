/**
 * 平台通用回归测试步骤领域规范化引擎 (Platform Universal Step Normalizer)
 *
 * 统一对等保 (security)、多活 (MMR/fbase-database)、高可用 (fbasecman) 等所有产品的步骤数据
 * 进行智能解析、清洗、分类和结构化：
 * 1. 自动分类为 ActionStep (纯操作)、VerifyStep (状态断言)、DiffStep (配置比对)
 * 2. 剥离命令中夹杂的 SQL 表格输出，将真正的业务载荷放入 actualPayload
 * 3. 抽取关键期望并与实际状态对照，将 returncode/elapsed 沉淀为轻量遥测 (telemetry)
 * 4. 规范化日志证据与工件文件引用，支持独立控制台视窗与预览
 */

export type StepKind = 'action' | 'verify' | 'diff';

export interface StepEvidenceItem {
  type: 'artifact' | 'log' | 'text';
  label: string;
  value: string;
}

export interface ParsedTable {
  headers: string[];
  rows: string[][];
  footer?: string;
}

export interface SemanticChange {
  field: string;
  from: string;
  to: string;
}

export interface NormalizedStep {
  key: string;
  order: number;
  title: string;
  cleanTitle: string;
  status: 'PASS' | 'FAIL' | 'BLOCKED' | 'CANCELLED' | 'SKIP' | 'UNKNOWN';
  kind: StepKind;

  // 命令与动作
  command?: string;
  exampleCode?: string;
  action?: string;
  context?: Record<string, string>;

  // 验证与比对
  expected?: string;
  assertion?: string;
  analysis?: string;
  actualSummary?: string;
  actualPayload?: {
    type: 'table' | 'diff' | 'text' | 'json';
    raw: string;
    parsedTable?: ParsedTable;
    semanticChanges?: SemanticChange[];
  };

  // 证据 (日志证据 / 工件引用)
  evidenceItems: StepEvidenceItem[];

  // 底层遥测指标 (如 returncode, attempts, elapsed)
  telemetry?: {
    returncode?: number;
    attempts?: number;
    elapsed?: string;
  };
}

/** 辅助函数：解析 ASCII PSQL 表格 */
export function parsePsqlAsciiTable(text: string): ParsedTable | null {
  if (!text || (!text.includes('|') && !text.includes('---'))) return null;
  const lines = text.split('\n').map((l) => l.trimEnd()).filter(Boolean);
  if (lines.length < 2) return null;

  // 寻找分隔行，如 "---+---" 或 "-----------+---------"
  const sepIndex = lines.findIndex((l) => /^[-+\s]{3,}$/.test(l.trim()) && l.includes('+'));
  if (sepIndex <= 0) return null;

  const headerLine = lines[sepIndex - 1];
  let rawHeaders = headerLine.split('|').map((h) => h.trim());
  if (rawHeaders.length > 2 && rawHeaders[0] === '' && rawHeaders[rawHeaders.length - 1] === '') {
    rawHeaders = rawHeaders.slice(1, -1);
  }
  const headers = rawHeaders;
  if (headers.length < 2) return null;

  const rows: string[][] = [];
  let footer = '';

  for (let i = sepIndex + 1; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith('(') && line.includes('row')) {
      footer = line.trim();
      break;
    }
    if (line.includes('|')) {
      let cols = line.split('|').map((c) => c.trim());
      if (cols.length === headers.length + 1 && cols[cols.length - 1] === '') {
        cols = cols.slice(0, -1);
      } else if (cols.length === headers.length + 1 && cols[0] === '') {
        cols = cols.slice(1);
      } else if (cols.length === headers.length + 2 && cols[0] === '' && cols[cols.length - 1] === '') {
        cols = cols.slice(1, -1);
      }
      if (cols.length === headers.length) {
        rows.push(cols);
      }
    }
  }

  return { headers, rows, footer };
}

/** 辅助函数：从文本中提取语义变化 */
export function parseSemanticChanges(text: string): SemanticChange[] {
  const changes: SemanticChange[] = [];
  const lines = text.split('\n');
  for (const line of lines) {
    const m = line.trim().match(/^([a-zA-Z0-9_.[\]]+):\s*(.*?)\s*->\s*(.*)$/);
    if (m) {
      changes.push({
        field: m[1].trim(),
        from: m[2].trim(),
        to: m[3].trim(),
      });
    }
  }
  return changes;
}

/** 核心步骤规范化函数 */
export function normalizeStep(rawStep: any, fallbackIndex: number): NormalizedStep {
  const rawTitle = String(rawStep?.title || rawStep?.name || `步骤 ${fallbackIndex + 1}`);

  // 提取步骤序号与干净标题
  let order = Number(rawStep?.order);
  let cleanTitle = rawTitle;
  const titleMatch = rawTitle.match(/^(?:步骤|检测项)\s*(\d+)[:：]\s*(.*)$/);
  if (titleMatch) {
    if (isNaN(order) || !order) order = parseInt(titleMatch[1], 10);
    cleanTitle = titleMatch[2].trim();
  }
  if (!order || isNaN(order)) order = fallbackIndex + 1;

  // result 是业务结论；finished/completed 只表示生命周期结束。
  const rawStatus = String(rawStep?.result || rawStep?.status || 'UNKNOWN').toUpperCase();
  const status: NormalizedStep['status'] =
    ['PASS', 'FAIL', 'BLOCKED', 'CANCELLED', 'SKIP'].includes(rawStatus)
      ? rawStatus as NormalizedStep['status']
      : rawStatus === 'ERROR' ? 'FAIL' : 'UNKNOWN';
  const valueText = (value: unknown): string => {
    if (value === undefined || value === null) return '';
    if (typeof value === 'string') return value.trim();
    if (Array.isArray(value) && value.length === 0) return '0 行（空结果集）';
    return JSON.stringify(value, null, 2);
  };

  // 提取原始字段
  let action = typeof rawStep?.action === 'string' ? rawStep.action.trim() : '';
  let rawCommand = '';
  if (typeof rawStep?.command === 'string') {
    rawCommand = rawStep.command.trim();
  } else if (Array.isArray(rawStep?.execution)) {
    const firstEx = rawStep.execution[0];
    if (typeof firstEx === 'string') rawCommand = firstEx.trim();
    else if (firstEx && typeof firstEx === 'object' && typeof firstEx.text === 'string') {
      rawCommand = firstEx.text.trim();
    }
  }

  let rawExpected = valueText(rawStep?.expected);
  const rawAssertion = typeof rawStep?.assertion === 'string'
    ? rawStep.assertion.trim()
    : rawStep?.assertion ? JSON.stringify(rawStep.assertion, null, 2) : '';
  const rawAnalysis = typeof rawStep?.analysis === 'string' ? rawStep.analysis.trim() : '';
  let rawActual = valueText(rawStep?.actual);
  const rawOutput = typeof rawStep?.output === 'string' ? rawStep.output.trim() : '';
  let stateTable = typeof rawStep?.state_table === 'string' ? rawStep.state_table.trim() : '';
  const context: Record<string, string> = {};

  if (rawStep?.port) context['端口'] = String(rawStep.port);
  if (rawStep?.config_file) context['配置文件'] = String(rawStep.config_file);

  // 1. 清洗命令行：拆出元数据与可能附带在命令后的 SQL 表格
  let cleanCommand = rawCommand;
  if (rawCommand) {
    const lines = rawCommand.split('\n');
    const filteredLines: string[] = [];
    for (const l of lines) {
      const s = l.trim();
      if (s.startsWith('监听端口:')) {
        context['监听端口'] = s.slice(s.indexOf(':') + 1).trim();
      } else if (s.startsWith('配置文件:')) {
        context['配置文件'] = s.slice(s.indexOf(':') + 1).trim();
      } else if (s.startsWith('关键期望:') || s.startsWith('预期:')) {
        if (!rawExpected) {
          rawExpected = s.replace(/^(?:关键期望|预期):\s*/, '').trim();
        }
      } else {
        filteredLines.push(l);
      }
    }
    const combined = filteredLines.join('\n').trim();

    // 检查是否有 "$ psql ... \n\n <table content>"
    if (combined.startsWith('$ ')) {
      const parts = combined.split(/\n\s*\n/);
      if (parts.length > 1) {
        cleanCommand = parts[0].trim();
        const outputAfter = parts.slice(1).join('\n\n').trim();
        if (!stateTable && outputAfter) {
          stateTable = outputAfter;
        }
      } else {
        cleanCommand = combined;
      }
    } else {
      cleanCommand = combined;
    }
  }

  // 2. 遥测信息提取 (returncode, attempts, elapsed)
  const telemetry: { returncode?: number; attempts?: number; elapsed?: string } = {};
  if (rawActual) {
    const rcMatch = rawActual.match(/returncode=(\d+)/);
    if (rcMatch) telemetry.returncode = parseInt(rcMatch[1], 10);
    const attMatch = rawActual.match(/attempts=(\d+)/);
    if (attMatch) telemetry.attempts = parseInt(attMatch[1], 10);
    const elapMatch = rawActual.match(/elapsed=([\d.]+s?)/);
    if (elapMatch) telemetry.elapsed = elapMatch[1].endsWith('s') ? elapMatch[1] : elapMatch[1] + 's';
  }

  // 3. 证据抽取 (evidence)
  const evidenceItems: StepEvidenceItem[] = [];
  const addEvidenceValue = (val: string, label = '证据') => {
    if (!val) return;
    const trimmed = val.trim();
    if (!trimmed) return;
    if (trimmed.startsWith('artifacts/') || /\.(json|log|conf|diff)$/.test(trimmed)) {
      evidenceItems.push({ type: 'artifact', label: label.includes('工件') ? label : '工件证据', value: trimmed });
    } else if (trimmed.includes('\n') || trimmed.includes('[INFO]') || trimmed.includes('[ERROR]') || trimmed.includes('LOG:')) {
      evidenceItems.push({ type: 'log', label: label.includes('日志') ? label : '日志证据', value: trimmed });
    } else {
      evidenceItems.push({ type: 'text', label, value: trimmed });
    }
  };

  if (typeof rawStep?.evidence === 'string') {
    addEvidenceValue(rawStep.evidence, '证据');
  } else if (Array.isArray(rawStep?.evidence)) {
    for (const ev of rawStep.evidence) {
      if (typeof ev === 'string') {
        addEvidenceValue(ev, '证据');
      } else if (ev && typeof ev === 'object') {
        addEvidenceValue(ev.text || ev.value || JSON.stringify(ev), ev.label || '证据');
      }
    }
  }

  // 4. 类型推断 (kind)
  let kind: StepKind = 'action';
  const hasDiffContent =
    rawCommand.includes('diff -u') ||
    rawActual.includes('--- ') ||
    rawActual.includes('@@ ') ||
    rawActual.includes('语义变化:') ||
    cleanTitle.includes('diff') ||
    cleanTitle.includes('配置文件实际 diff');

  const hasExplicitVerify =
    Boolean(rawExpected) ||
    Boolean(stateTable) ||
    cleanTitle.includes('查看') ||
    cleanTitle.includes('验证') ||
    cleanTitle.includes('检查') ||
    cleanTitle.includes('比对') ||
    cleanCommand.includes('SHOW ') ||
    cleanCommand.includes('SELECT ') ||
    (rawActual && !rawActual.startsWith('returncode=') && rawActual !== 'console ready');

  // 断言类型 + 命令内嵌校验决定 kind：command_succeeds 且无内嵌检查
  // 的才是纯执行动作（initdb/GRANT/UDF 调用等）；其余都是状态验证。
  const assertionType = rawStep?.assertion && typeof rawStep.assertion === 'object'
    && typeof rawStep.assertion.type === 'string'
    ? rawStep.assertion.type : '';
  const commandHasCheck =
    /\b(test|grep|awk|cmp|diff|wc)\b|\[\s|!\s*[\w./{]|for\s+\w+\s+in|while\b|until\b|case\b|exit\s+[1-9]|SELECT\b[^;]*?\bFROM\b|\bSHOW\b/i
      .test(rawCommand);

  if (hasDiffContent) {
    kind = 'diff';
  } else if (['prepare','action','cleanup'].includes(rawStep?.intent)) {
    kind='action';
  } else if (rawStep?.intent==='verify') {
    kind='verify';
  } else if (assertionType) {
    kind = assertionType === 'command_succeeds' && !commandHasCheck
      ? 'action'
      : 'verify';
  } else if (hasExplicitVerify) {
    kind = 'verify';
  } else {
    kind = 'action';
  }

  // 5. 组装 actualPayload：原始输出即实际结果，摘要文本仅用于判定展示
  let actualPayload: NormalizedStep['actualPayload'] = undefined;
  let actualSummary = rawActual;
  const actualText = rawOutput || rawActual;

  if (kind === 'diff') {
    const fullDiffText = [rawActual, stateTable].filter(Boolean).join('\n\n');
    const semanticChanges = parseSemanticChanges(fullDiffText);
    actualPayload = {
      type: 'diff',
      raw: fullDiffText,
      semanticChanges: semanticChanges.length ? semanticChanges : undefined,
    };
    if (semanticChanges.length) {
      actualSummary = `已识别 ${semanticChanges.length} 处配置语义变更`;
    }
  } else if (stateTable) {
    const parsed = parsePsqlAsciiTable(stateTable);
    actualPayload = {
      type: 'table',
      raw: stateTable,
      parsedTable: parsed || undefined,
    };
  } else if (actualText && !actualText.startsWith('returncode=')) {
    actualPayload = {
      type: 'text',
      raw: actualText,
    };
  }

  return {
    key: `step-${order}-${fallbackIndex}`,
    order,
    title: rawTitle,
    cleanTitle,
    status,
    kind,
    command: cleanCommand || undefined,
    exampleCode: typeof rawStep?.example_code === 'string' ? rawStep.example_code : undefined,
    action: action || undefined,
    context: Object.keys(context).length ? context : undefined,
    expected: rawExpected || undefined,
    assertion: rawAssertion || undefined,
    analysis: rawAnalysis || undefined,
    actualSummary: actualSummary || undefined,
    actualPayload,
    evidenceItems,
    telemetry: Object.keys(telemetry).length ? telemetry : undefined,
  };
}
