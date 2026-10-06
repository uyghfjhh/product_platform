import assert from 'node:assert/strict';
import { chromium } from 'playwright';
const base = process.env.PLATFORM_URL || 'http://127.0.0.1:18769';
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  assert.equal((await page.request.get(base + '/api/v1/deployment-browser-fixture')).status(), 200, 'Use isolated fixture only');
  await page.goto(base + '/', { waitUntil: 'networkidle', timeout: 20000 });
  await page.evaluate(() => {
    localStorage.setItem('platform-environment', 'cman-lab');
    localStorage.setItem('platform-page', 'tests:fbasecman:default');
  });
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(3500);
  // 点 ha_commands 卡片的「执行整组」→ 确认弹窗
  const card = page.locator('.suite-card', { has: page.locator('.suite-id-tag', { hasText: 'ha_commands' }) });
  await card.locator('.suite-action-btn').click();
  await page.locator('.ant-modal-confirm .ant-btn-primary').click();
  // 等终端出现并等第一条进度
  let bar = null;
  for (let i = 0; i < 30; i++) {
    await page.waitForTimeout(4000);
    bar = await page.evaluate(() => {
      const el = document.querySelector('.regression-terminal-progress');
      return el ? el.textContent : null;
    });
    if (bar) break;
  }
  console.log('terminal progress bar:', JSON.stringify(bar));
  const terminal = await page.evaluate(() => !!document.querySelector('.regression-terminal'));
  console.log('terminal present:', terminal);
  await page.screenshot({ path: '/tmp/terminal-progress.png' });
} finally { await browser.close(); }
