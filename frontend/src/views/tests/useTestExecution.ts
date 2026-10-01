import { useState } from 'react';
import { App } from 'antd';
import { operationRequest, type Environment } from '../../platform/api';
import type { TestProductAdapter } from '../../products/testRegistry';

export function useTestExecution(environment: Environment | undefined,
                                 adapter: TestProductAdapter,
                                 openTask: (taskId: string) => void) {
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
      if (adapter.supportsTerminal) setTerminalTaskId(task.id);
      else openTask(task.id);
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

  return { runTarget, runSuite, terminalTaskId };
}
