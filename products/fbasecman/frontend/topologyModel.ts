export type RegressionNode = {
  name: string;
  label?: string;
  host: string;
  port: string;
  role: string;
  state: string;
  cluster: string;
  is_write?: boolean;
};

export type RegressionCluster = {
  name: string;
  label?: string;
  state: string;
  nodes: RegressionNode[];
};

export type RegressionSnapshot = {
  title: string;
  step_num: number;
  clusters: RegressionCluster[];
  event_desc?: string;
  action?: string;
  event_type?: string;
  proxy_state?: {
    status?: string;
    badge?: string;
    monitor_status?: string;
    topology_status?: string;
    routing_decision?: string;
    details?: string;
  };
  links?: Record<string, boolean>;
};

export type RegressionTopology = {
  clusters: RegressionCluster[];
  step_snapshots: RegressionSnapshot[];
  proxy?: { write_port?: number; read_port?: number };
};
