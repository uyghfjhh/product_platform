import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type Case, type Result, type Task } from '../../platform/api';
import type { TestProductAdapter } from '../../products/testRegistry';

export type SourceStatus = { status: string; duration?: string; has_report?: boolean; modified_at: number };
export type FlakyStatus = { flaky: boolean; recent: string[] };

export function useTestData(productId: string, environmentId: string | undefined,
                            adapter: TestProductAdapter, tasks: Task[]) {
  const [cases, setCases] = useState<Case[]>([]);
  const [results, setResults] = useState<Result[]>([]);
  const [sourceStatuses, setSourceStatuses] = useState<Record<string, SourceStatus>>({});
  const [flakyMap, setFlakyMap] = useState<Record<string, FlakyStatus>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const scope = `${productId}:${environmentId || ''}:${adapter.mode}`;
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const refreshVersion = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    setCases([]); setError(''); setLoading(true);
    void api<Case[]>(`/cases?product_id=${encodeURIComponent(productId)}`, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setCases(adapter.suiteFilter ? data.filter((c) => c.suite === adapter.suiteFilter) : data); })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [productId, adapter.suiteFilter]);

  const refreshResults = useCallback(async () => {
    const version = ++refreshVersion.current;
    const [newResults, statuses] = await Promise.all([
      environmentId ? api<Result[]>(`/environments/${encodeURIComponent(environmentId)}/results`).catch(() => []) : Promise.resolve([]),
      adapter.sourceStatusPath ? api<Record<string, SourceStatus>>(adapter.sourceStatusPath(environmentId)).catch(() => ({})) : Promise.resolve({}),
    ]);
    if (currentScope.current !== scope || version !== refreshVersion.current) return;
    setResults(newResults); setSourceStatuses(statuses);
  }, [environmentId, adapter.sourceStatusPath, scope]);

  useEffect(() => {
    setResults([]); setSourceStatuses({}); setFlakyMap({});
    void refreshResults();
    return () => { ++refreshVersion.current; };
  }, [refreshResults]);

  const hasActiveTask = tasks.some((task) => task.environment_id === environmentId
    && ['QUEUED', 'RUNNING', 'CANCELLING'].includes(task.status));
  useEffect(() => {
    // Also fetch the final result when the last running task becomes terminal.
    void refreshResults();
    if (!hasActiveTask) return;
    const timer = window.setInterval(() => void refreshResults(), 2500);
    return () => window.clearInterval(timer);
  }, [hasActiveTask, refreshResults]);

  useEffect(() => {
    if (!environmentId) return;
    const controller = new AbortController();
    void api<Record<string, FlakyStatus>>(`/environments/${encodeURIComponent(environmentId)}/flaky`, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setFlakyMap(data); })
      .catch(() => { if (!controller.signal.aborted) setFlakyMap({}); });
    return () => controller.abort();
  }, [environmentId, results]);

  return { cases, results, sourceStatuses, flakyMap, loading, error, refreshResults };
}
