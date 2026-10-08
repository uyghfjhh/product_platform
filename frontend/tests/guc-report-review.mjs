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
  await modal.locator('.cman-scenario-group > summary').first().waitFor();
  if (!(await modal.locator('.cman-scenario-group').first().getAttribute('open') !== null)) {
    await modal.locator('.cman-scenario-group > summary').first().click();
  }
  await modal.locator('.cman-process-flow').first().waitFor();
  const firstTable = modal.locator('.cman-process-flow').first();
  await firstTable.getByText('实际返回内容', { exact: true }).first().waitFor();
  const text = await modal.locator('.cman-business-summary').first().textContent();
  assert.match(text, /work_mem/);
  assert.match(text, /32MB/);
  assert.match(text, /后端 PID|后端进程/);
  assert.match(await firstTable.textContent(), /实际执行命令／SQL/);
  assert.match(await firstTable.textContent(), /SELECT current_setting/);
  assert.match(await firstTable.textContent(), /命令返回：SET/);
  await modal.getByRole('button', { name: /查看完整步骤与协议证据/ }).click();
  assert.equal(await modal.locator('.cman-process-flow').count(), 0);
  await modal.getByRole('button', { name: '只看业务验证', exact: true }).click();
  if (!(await firstTable.isVisible())) await modal.locator('.cman-scenario-group > summary').first().click();
  await firstTable.waitFor();
  assert.deepEqual(errors, []);
  console.log('GUC report browser passed: ordered SQL actions, actual responses, expected values and analysis and complete-step toggle (read-only).');
} finally { await browser.close(); }
