import type { DeploymentProductAdapter } from './deploymentRegistry';
import type { TestMode, TestProductAdapter } from './testRegistry';
import type { Environment } from '../platform/api';
import type { ComponentType } from 'react';
import type { TopologyData, TopologyNode } from '../platform/topology';

type ReportProps = {
  target: string | null;
  environmentId?: string;
  onClose: () => void;
  caseInfo?: { title?: string | null; summary?: string | null; name?: string | null; core_id?: string | null };
};
type TerminalProps = { taskId: string | null; onInspect: (taskId: string) => void; onFinished: () => void; onClose?: () => void };
type CanvasProps = {
  topology: TopologyData;
  observed: Record<string, { running: boolean | null; message?: string; pid?: number | null }> | null;
  onSelectNode: (node: TopologyNode) => void;
  onOpenSql: (node: TopologyNode) => void;
  onDeploy: () => void;
};

export type ProductFrontend = {
  TestSettings?: ComponentType<{ environment: Environment; onChanged: () => Promise<void> | void }>;
  deployment?: () => DeploymentProductAdapter;
  test?: (mode?: TestMode) => TestProductAdapter;
  testMode?: (environment?: Environment) => TestMode;
  ReportViewer?: ComponentType<ReportProps>;
  RegressionTerminal?: ComponentType<TerminalProps>;
  DeploymentCanvas?: ComponentType<CanvasProps>;
  sceneDetails?: { kindPrefix: string; component: ComponentType<{ details: Record<string, unknown> }> };
};
