export type Product = {
  id: string;
  title: string;
  description: string;
  capabilities: string[];
  source_path: string;
};

export type Environment = {
  id: string;
  product_id: string;
  title: string;
  host: string;
  port: number;
  database_name: string;
  database_user: string;
  deployment_config: string | null;
  deployment_target: string | null;
  created_at: string;
};

export type Action = {
  id: string;
  title: string;
  capability: string;
  changes_environment: boolean;
};

export type Task = {
  id: string;
  environment_id: string;
  action: string;
  target: string;
  status: string;
  reason: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested: boolean;
  last_sequence: number;
};

export type Event = {
  task_id: string;
  sequence: number;
  recorded_at: string;
  event_type: string;
  payload: Record<string, unknown>;
};

export type Case = {
  suite: string;
  target: string;
  title: string;
  enabled: boolean;
  tags: string[];
};

export type Result = {
  product_id: string;
  environment_id: string;
  target: string;
  profile: string;
  status: string;
  reason: string | null;
  updated_at: string;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
  });
  if (!response.ok) {
    let error = response.statusText;
    try {
      const data = await response.json();
      error = typeof data.detail === 'string' ? data.detail : data.message || JSON.stringify(data.detail);
    } catch {
      // 非 JSON 错误仍显示服务端状态。
    }
    throw new Error(error || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function post<T>(path: string, value: unknown): Promise<T> {
  return api<T>(path, { method: 'POST', body: JSON.stringify(value) });
}

export function put<T>(path: string, value: unknown): Promise<T> {
  return api<T>(path, { method: 'PUT', body: JSON.stringify(value) });
}

export function statusColor(status: string): string {
  if (['SUCCEEDED', 'PASS'].includes(status)) return 'success';
  if (['FAILED', 'ERROR', 'FAIL', 'RECOVERY_REQUIRED'].includes(status)) return 'error';
  if (['RUNNING', 'CANCELLING'].includes(status)) return 'processing';
  return 'default';
}

export function operationRequest(environmentId: string, action: string, target?: string, parameters: Record<string, unknown> = {}, acknowledgeChange = false) {
  return post<Task>('/operations', {
    environment_id: environmentId,
    action,
    target: target || undefined,
    parameters,
    submission_key: crypto.randomUUID(),
    acknowledge_change: acknowledgeChange,
  });
}
