import assert from 'node:assert/strict';
import { chromium } from 'playwright';

// Read-only verification against archived GUC evidence; never runs cases.
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem('platform-page', 'tests:fbasecman:cman');
    localStorage.setItem('platform-environment', 'cman-lab');
  });
  await page.goto(process.env.PLATFORM_URL || 'http://127.0.0.1:8080', { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder(/搜索用例名称/).fill('backend_redeploy_sql_parse');
  const row = page.locator('.case-row').filter({ has: page.locator('.case-name[title="guc.backend_redeploy_sql_parse"]') });
  await row.getByRole('button', { name: '📄 查看报告', exact: true }).click();
  const modal = page.getByRole('dialog');
  await modal.getByText('本场景核心配置', { exact: true }).first().waitFor({ state: 'attached' });
  const group = modal.locator('.cman-scenario-group').filter({ hasText: 'psql 读写切换与新客户端防污染' }).first();
  await group.locator(':scope > summary').click();
  const table = group.locator('.cman-business-table').first();
  await table.waitFor();
  assert.deepEqual(await table.locator('thead th').allTextContents(), ['操作／客户端', '实际后端', '参数', '预期', '实测', '判定']);
  const text = await group.textContent();
  assert.match(text, /32MB/);
  assert.match(text, /PID/);
  assert.match(text, /连接池/);
  assert.match(text, /读写模式/);
  assert.match(text, /连接组/);
  assert.match(text, /真实 psql/);
  await table.getByText('执行详情', { exact: true }).first().click();
  await table.getByText(/SELECT 'initial'/).first().waitFor();
  await modal.getByRole('button', { name: /查看完整步骤与协议证据/ }).click();
  assert.equal(await modal.locator('.cman-business-table').count(), 0);
  await modal.getByRole('button', { name: '只看业务验证', exact: true }).click();
  if (!(await table.isVisible())) await group.locator(':scope > summary').click();
  await table.waitFor();
  assert.deepEqual(errors, []);
  console.log('GUC report browser passed: ordered SQL actions, actual responses, expected values and analysis and complete-step toggle (read-only).');
} finally { await browser.close(); }
