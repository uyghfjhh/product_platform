export type ReferenceDeployment = {
  nodes: Array<Record<string, unknown>>;
  edges: Array<Record<string, unknown>>;
  clusters: Array<Record<string, unknown>>;
  health: Record<string, unknown>;
};

export function renderDeploymentCanvas(env: ReferenceDeployment): string;
