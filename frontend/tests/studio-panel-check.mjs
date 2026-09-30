import assert from 'node:assert/strict';
import { chromium } from 'playwright';

// Prisma Studio 嵌入验收：对运行中服务的真实环境（默认 fbase-mmr）——
// 数据管理器区块挂载、BFF 端点被调用且返回 200、切换直连节点后指向新端口。
const base = process.env.PLATFORM_URL || 'http://127.0.0.1:8080';
const browser = await chromium.launch({ headless: true });
const errors = [];
const bffCalls = [];

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('response', (response) => {
    if (response.url().includes('/studio')) bffCalls.push({ url: response.url(), status: response.status() });
  });

  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.locator('.platform-content').waitFor();
  await page.locator('.ant-menu-title-content', { hasText: '数据库管理' }).first().click();
  await page.getByText('数据库对象').waitFor();

  // 数据管理器区块 → Studio 挂载并自动发起 BFF 请求
  await page.getByText('Prisma Studio——浏览表数据与结构').scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.querySelector('.ps') !== null, undefined, { timeout: 40000 });
  await page.waitForTimeout(2000);
  assert.ok(bffCalls.length > 0, 'studio BFF was called');
  assert.ok(bffCalls.every((call) => call.status === 200), 'BFF calls 200: ' + JSON.stringify(bffCalls));
  const firstPorts = new Set(bffCalls.map((call) => new URL(call.url).searchParams.get('port')));

  // 切换直连节点 → Studio 重新挂载指向新端口（存在备选节点时）
  const instanceSelect = page.locator('.ant-select').filter({ hasText: /PRIMARY|STANDBY|mmr|节点/ }).first();
  if (await instanceSelect.isVisible().catch(() => false)) {
    await instanceSelect.click();
    const options = page.locator('.ant-select-item-option-content');
    const count = await options.count();
    if (count > 1) {
      const before = firstPorts.values().next().value;
      await options.nth(count - 1).click();
      await page.waitForTimeout(4000);
      const laterPorts = bffCalls.map((call) => new URL(call.url).searchParams.get('port')).filter(Boolean);
      assert.ok(laterPorts.some((port) => port !== before), 'BFF retargets to new node port');
    }
  }

  assert.deepEqual(errors, [], 'page errors: ' + errors.join(';'));
  console.log('studio panel browser acceptance OK —', bffCalls.length, 'BFF calls, ports:', [...firstPorts]);
} finally {
  await browser.close();
}
