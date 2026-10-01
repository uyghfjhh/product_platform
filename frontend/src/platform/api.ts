import type { Task } from './contracts.generated';

export type { Product, Environment, RegressionBinding, Action, Task, Event, Case, Result } from './contracts.generated';

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

export function generateUUID(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export function operationRequest(environmentId: string, action: string, target?: string, parameters: Record<string, unknown> = {}, acknowledgeChange = false) {
  return post<Task>('/operations', {
    environment_id: environmentId,
    action,
    target: target || undefined,
    parameters,
    submission_key: generateUUID(),
    acknowledge_change: acknowledgeChange,
  });
}
