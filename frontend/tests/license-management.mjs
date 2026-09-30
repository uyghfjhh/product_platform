import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.PLATFORM_URL || 'http://127.0.0.1:8080';
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.locator('.header-title', { hasText: '数据库部署管理' }).waitFor();
  // 菜单结构见 tests/README.md：License 授权管理（子菜单）→ 密钥管理（叶子）
  const sider = page.locator('.platform-sidebar');
  const menu = (name) => sider.locator('.ant-menu-title-content', { hasText: name }).first();
  if (!(await menu('密钥管理').isVisible().catch(() => false))) {
    await menu('License 授权管理').click();
  }
  await menu('密钥管理').click();
  await page.getByRole('heading', { name: '密钥管理' }).waitFor();
  assert.ok(await page.getByText('生成密钥').isVisible());

  // 密钥库为空时先经 UI 生成一个版本：修改口令/删除版本仅在选中版本后渲染
  const options = await page.request.get(`${base}/api/v1/licenses/options`);
  const { key_versions: versions = [] } = await options.json();
  if (versions.length === 0) {
    await page.locator('.ant-form-item', { hasText: '新版本' }).locator('input').fill('1.2');
    await page.locator('.ant-form-item', { hasText: '新口令' }).locator('input').fill('browser-smoke-pass');
    await page.getByRole('button', { name: '生成密钥' }).click();
    await page.getByText(/密钥版本 .* 已生成/).waitFor();
  } else {
    // 已有版本：等待指纹信息载入（load 时自动选中最新版）
    await page.getByText('SHA256 指纹').waitFor();
  }

  assert.ok(await page.getByText('修改口令').isVisible());
  assert.ok(await page.getByText('删除版本').isVisible());
  assert.ok((await page.locator('.license-key-manager').innerText()).includes('指纹'));
  assert.deepEqual(errors, []);
  await page.screenshot({ path: '/tmp/product-platform-license-management.png', fullPage: true });
  console.log('License management page passed');
} finally {
  await browser.close();
}
