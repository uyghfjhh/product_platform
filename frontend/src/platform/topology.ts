/** Shared topology contracts for deployment and product views. */
export type TopologyNode = {
  id: string;
  label: string;
  host: string;
  port: number;
  role: string;
  group?: string;
  data_dir: string;
  extensions?: string[];
  installed_extensions?: string[];
};

export type TopologyData = {
  target: string;
  kind: string;
  nodes: TopologyNode[];
  edges: { id: string; source: string; target: string; kind: string }[];
};

