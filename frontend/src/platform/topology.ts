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
  declared_role?: string;
  original_primary?: string;
  previous_primary?: string;
  rejoin_required?: boolean;
};

export type TopologyEdgeLink = {
  name: string;
  pub_cluster: string;
  sub_cluster: string;
  database: string;
  sub_database: string;
  publication: string;
  subscription: string;
  slot: string;
  tables: string | string[];
  via_partition_root: boolean;
  publish?: string | null;
  live?: {
    connected?: boolean;
    state?: string;
    senders?: number;
    replay_lag_bytes?: number;
  };
};

export type TopologyData = {
  target: string;
  kind: string;
  nodes: TopologyNode[];
  edges: { id: string; source: string; target: string; kind: string; link?: TopologyEdgeLink }[];
};

