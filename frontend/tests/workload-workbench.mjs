import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.PLATFORM_URL || 'http://127.0.0.1:18773';
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
  const marker = await page.request.get(base + '/api/v1/workload-browser-fixture');
  assert.equal(marker.status(), 200, 'isolated fixture is mandatory');
  assert.equal((await marker.json()).isolated, true);
  await page.addInitScript(() => localStorage.setItem('platform-theme', 'cman'));
  const errors = [];
  page.on('pageerror', (cause) => errors.push(cause.message));
  await page.goto(base + '/?page=stability:fbasecman&env=workload-browser');
  await page.getByRole('heading', { name: '工作负载库', exact: true }).waitFor();
  assert.equal(await page.getByRole('checkbox', { name: '选择 连接与轻查询', exact: true }).isChecked(), true);
  assert.equal(await page.getByRole('checkbox', { name: '选择 MMR hint 长连接读写切换', exact: true }).isDisabled(), true);
  await page.getByRole('checkbox', { name: '选择 系统目录查询', exact: true }).check();
  await page.locator('.wb-library-open').filter({ hasText: '系统目录查询' }).click();
  await page.getByRole('spinbutton', { name: '时长（秒）', exact: true }).fill('7');
  await page.getByRole('spinbutton', { name: '并发客户端', exact: true }).fill('3');
  await page.locator('.wb-library-open').filter({ hasText: '连接与轻查询' }).first().click();
  assert.equal(await page.getByRole('spinbutton', { name: '并发客户端', exact: true }).inputValue(), '4');
  await page.locator('.wb-library-open').filter({ hasText: '系统目录查询' }).click();
  assert.equal(await page.getByRole('spinbutton', { name: '并发客户端', exact: true }).inputValue(), '3');
  await page.getByRole('tab', { name: 'SQL 脚本', exact: true }).click();
  await page.locator('.monaco-editor').waitFor();
  await page.locator('.monaco-editor [role="textbox"]').press('Control+A');
  await page.keyboard.insertText('SELECT 42;');
  await page.getByText('本次自定义', { exact: true }).waitFor();
  await page.getByRole('button', { name: '恢复默认 SQL', exact: true }).click();
  await page.getByText('默认脚本', { exact: true }).waitFor();
  await page.locator('.monaco-editor [role="textbox"]').press('Control+A');
  await page.keyboard.insertText('SELECT 42;');
  await page.screenshot({ path: '/tmp/workload-workbench-desktop.png', fullPage: true });
  const reviewResponse = page.waitForResponse((response) => response.url().endsWith('/workload-plans') && response.request().method() === 'POST');
  await page.getByRole('button', { name: /审阅并启动 2 项/ }).click();
  const plan = await (await reviewResponse).json();
  assert.equal(plan.ready, true);
  assert.equal(plan.steps[1].parameters.script, 'SELECT 42;');
  assert.equal(plan.steps[1].parameters.clients, 3);
  assert.equal(plan.steps[1].parameters.duration_seconds, 7);
  const dialog = page.getByRole('dialog');
  await dialog.getByText('127.0.0.1:17403/postgres', { exact: true }).waitFor();
  const startResponse = page.waitForResponse((response) => response.url().endsWith('/workload-plans/' + plan.id + '/run'));
  await dialog.getByRole('button', { name: '确认启动', exact: true }).click();
  const runId = (await (await startResponse).json()).id;
  const getRun = async () => (await page.request.get(base + '/api/v1/workload-runs/' + runId)).json();
  async function until(predicate) {
    for (let index = 0; index < 80; index++) {
      const row = await getRun();
      if (predicate(row)) return row;
      await page.waitForTimeout(100);
    }
    throw new Error('fixture state did not converge');
  }
  let run = await until((row) => row.tasks.length === 1);
  assert.equal((await page.request.post(base + '/api/v1/workload-browser-fixture/finish/' + run.tasks[0])).status(), 200);
  run = await until((row) => row.tasks.length === 2);
  await page.getByRole('button', { name: '采样与判点', exact: true }).first().click();
  await page.getByText('fixture exit', { exact: true }).waitFor();
  await page.getByRole('button', { name: /停止整组/ }).click();
  await page.getByRole('dialog').locator('.ant-btn-primary').click();
  run = await until((row) => row.status === 'CANCELLING');
  assert.equal(Boolean(run.task_details[1].cancel_requested), true);
  await page.request.post(base + '/api/v1/workload-browser-fixture/finish/' + run.tasks[1]);
  run = await until((row) => row.status === 'CANCELLED');
  assert.equal(run.tasks.length, 2);
  await page.getByText('已取消', { exact: true }).first().waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('tab', { name: '配置方案', exact: true }).click();
  await page.screenshot({ path: '/tmp/workload-workbench-mobile.png', fullPage: true });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'mobile must not overflow horizontally');
  await page.route('**/api/v1/environments/workload-browser/workloads', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'fixture catalog unavailable' }) }));
  await page.reload();
  await page.getByText('负载目录读取失败，请确认后端已加载新接口', { exact: true }).waitFor();
  assert.equal(await page.locator('.wb-loading').count(), 0);
  assert.deepEqual(errors, []);
  console.log('Workload workbench browser PASS: selection, per-item parameters, SQL edit/reset, review, sequential tasks, metrics, cancellation, mobile and catalog failure.');
} finally {
  await browser.close();
}
