import assert from 'node:assert/strict';
import { chromium } from 'playwright';

// Run against an isolated platform with seeded results; never execute database tests.
const base = process.env.PLATFORM_URL || 'http://127.0.0.1:18767';
const browser = await chromium.launch({ headless: true });
const errors = [];
try {
  for (const [suite, target] of [
    ['mac', 'mac.audit.log_access_restrictions'],
    ['mmr', 'mmr.background.maintenance_lifecycle'],
  ]) {
    const page = await browser.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    const bound = await page.request.put(`${base}/api/v1/regression-bindings/fbase-database/${suite}`, {
      data: { environment_id: `report-${suite}` },
    });
    assert.equal(bound.status(), 200, await bound.text());
    await page.addInitScript((mode) => localStorage.setItem('platform-page', `tests:fbase-database:${mode}`), suite);
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.getByPlaceholder(/搜索用例名称/).fill(target);
    const row = page.locator('.case-row').filter({ has: page.locator(`.case-name[title="${target}"]`) });
    await row.getByRole('button', { name: '📄 查看报告', exact: true }).click();
    const modal = page.getByRole('dialog');
    await modal.getByText('检查步骤验收', { exact: false }).waitFor();
    await modal.getByText('disabled', { exact: true }).waitFor();
    await modal.getByRole('tab', { name: '原始报告' }).click();
    await modal.getByText('浏览器原始报告验收', { exact: true }).waitFor();
    const download = await modal.getByRole('link', { name: '下载原始报告' }).getAttribute('href');
    assert.equal((await page.request.get(base + download)).status(), 200);
    const html = await page.getByRole('link', { name: '📄 导出 HTML' }).getAttribute('href');
    assert.equal((await page.request.get(base + html)).status(), 200);
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log('Report browser checks passed: MAC/MMR steps, raw report, downloads and HTML export');
} finally {
  await browser.close();
}
