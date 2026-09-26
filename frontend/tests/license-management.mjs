import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.PLATFORM_URL || 'http://127.0.0.1:8080';
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.getByRole('menuitem', { name: 'License 管理' }).click();
  await page.getByText('密钥版本管理').waitFor();
  assert.ok(await page.getByText('生成密钥').isVisible());
  assert.ok(await page.getByText('修改口令').isVisible());
  assert.ok(await page.getByText('删除版本').isVisible());
  assert.ok((await page.locator('.license-key-manager').innerText()).includes('指纹'));
  assert.deepEqual(errors, []);
  await page.screenshot({ path: '/tmp/product-platform-license-management.png', fullPage: true });
  console.log('License management page passed');
} finally {
  await browser.close();
}
