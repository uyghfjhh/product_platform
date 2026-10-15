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
  extension_versions?: Record<string, string>;
  server_version?: string;
  database_version?: string;
};

export type TopologyData = {
  target: string;
  kind: string;
  nodes: TopologyNode[];
  edges: { id: string; source: string; target: string; kind: string }[];
};

