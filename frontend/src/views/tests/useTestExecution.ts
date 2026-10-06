import { useState } from 'react';
import { App } from 'antd';
import { operationRequest, post, type Environment } from '../../platform/api';
import type { TestProductAdapter } from '../../products/testRegistry';

export function useTestExecution(environment: Environment | undefined,
                                 adapter: TestProductAdapter) {
  const { message, modal } = App.useApp();
  const [terminalTaskId, setTerminalTaskId] = useState<string | null>(null);
  // 10. 执行单个用例
  async function runTarget(target: string, cluster?: string) {
    if (!adapter.action) {
      message.error('此产品没有注册可执行的前端测试动作');
      return;
    }
    if (!environment) {
      message.warning('请先在顶部选择绑定测试环境');
      return;
    }
    const effectiveCluster = cluster || adapter.clusterForSuite('');
    try {
      const task = await operationRequest(
        environment.id,
        adapter.action,
        target,
        adapter.mode === 'cman' ? {} : { cluster: effectiveCluster },
        true,
      );
      setTerminalTaskId(task.id);
    } catch (cause) {
      message.error((cause as Error).message);
    }
  }

  // 11. 执行整套用例
  async function runSuite(suiteId: string) {
    if (!environment) {
      message.warning('请先在顶部选择绑定测试环境');
      return;
    }
    const confirmed = await new Promise<boolean>((resolve) =>
      modal.confirm({
        title: '执行整套用例',
        content: `确认在环境【${environment.title}】执行套件【${suiteId}】的全部用例？`,
        okText: '确认执行',
        cancelText: '取消',
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      }),
    );
    if (confirmed) {
      const cluster = adapter.clusterForSuite(suiteId);
      await runTarget(suiteId, cluster);
    }
  }

  // 12. 重新执行整组中失败的用例
  async function runSuiteFailed(suiteId: string, failCount?: number) {
    if (!environment) {
      message.warning('请先在顶部选择绑定测试环境');
      return;
    }
    const countText = failCount ? ` (${failCount} 项)` : '';
    const confirmed = await new Promise<boolean>((resolve) =>
      modal.confirm({
        title: '重跑失败用例',
        content: `确认在环境【${environment.title}】仅重新执行套件【${suiteId}】中失败的用例${countText}？`,
        okText: '确认重跑',
        cancelText: '取消',
        okButtonProps: { danger: true },
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      }),
    );
    if (confirmed) {
      const cluster = adapter.clusterForSuite(suiteId);
      await runTarget(`${suiteId}.failed`, cluster);
    }
  }

  // 13. 停止当前运行的任务
  async function cancelTask(taskId: string) {
    try {
      await post(`/operations/${taskId}/cancel`, {});
      message.info('已请求终止用例执行任务');
    } catch (cause) {
      message.error((cause as Error).message);
    }
  }

  return { runTarget, runSuite, runSuiteFailed, cancelTask, terminalTaskId };
}
