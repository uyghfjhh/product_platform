import type { DeploymentProductAdapter } from './deploymentRegistry';
import type { TestMode, TestProductAdapter } from './testRegistry';
import type { Environment } from '../platform/api';
import type { ComponentType } from 'react';
import type { TopologyData, TopologyNode } from '../components/ThreeTopologyView';

type ReportProps = { target: string | null; environmentId?: string; onClose: () => void };
type TerminalProps = { taskId: string | null; onInspect: (taskId: string) => void; onFinished: () => void };
type CanvasProps = {
  topology: TopologyData;
  observed: Record<string, { running: boolean | null; message: string }> | null;
  onSelectNode: (node: TopologyNode) => void;
  onOpenSql: (node: TopologyNode) => void;
  onDeploy: () => void;
};

export type ProductFrontend = {
  deployment?: () => DeploymentProductAdapter;
  test?: (mode?: TestMode) => TestProductAdapter;
  testMode?: (environment?: Environment) => TestMode;
  ReportViewer?: ComponentType<ReportProps>;
  RegressionTerminal?: ComponentType<TerminalProps>;
  DeploymentCanvas?: ComponentType<CanvasProps>;
  sceneDetails?: { kindPrefix: string; component: ComponentType<{ details: Record<string, unknown> }> };
};
