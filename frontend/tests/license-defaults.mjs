import assert from 'node:assert/strict';
import { chromium } from 'playwright';

// Browser writes are intercepted; no real defaults, keys or licenses are changed.
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const base = process.env.PLATFORM_URL || 'http://127.0.0.1:8080';
  const response = await page.request.get(`${base}/api/v1/licenses/options`);
  assert.equal(response.status(), 200);
  const options = await response.json();
  options.key_versions = ['1.1']; options.usable_key_versions = ['1.1'];
  options.defaults.license_version = '1.1';
  for (const product of Object.values(options.defaults.products)) product.selected = true;
  let saved;
  await page.route('**/api/v1/licenses/options', async (route) => {
    const defaults = saved || options.defaults;
    await route.fulfill({ json: { ...options, vendor: defaults.vendor, defaults,
      products: Object.entries(defaults.products).map(([name, p]) => ({ name, version: p.default_version })) } });
  });
  await page.route('**/api/v1/licenses/defaults', async (route) => {
    assert.equal(route.request().method(), 'PUT');
    saved = route.request().postDataJSON();
    await route.fulfill({ json: saved });
  });
  await page.addInitScript(() => localStorage.setItem('platform-page', 'license:defaults'));
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: 'License 默认设置' }).waitFor();
  await page.locator('label').filter({ hasText: /^厂商名称$/ }).locator('input').fill('浏览器测试厂商');
  await page.locator('label').filter({ hasText: /^默认密钥口令/ }).locator('input').fill('browser-test-default');
  await page.locator('label').filter({ hasText: /^用途$/ }).locator('input').fill('浏览器测试用途');
  await page.locator('label').filter({ hasText: /^默认保存目录/ }).locator('input').fill('/tmp/browser-license-output');
  const productBlock = page.locator('.form-surface > div').filter({ has: page.getByText('fbasecman', { exact: true }) });
  await productBlock.locator('label').filter({ hasText: /^默认版本/ }).locator('input').fill('2.0');
  await productBlock.getByRole('checkbox', { name: '继承全局有效期' }).uncheck();
  await productBlock.getByRole('spinbutton').first().fill('2');
  await page.getByRole('button', { name: '保存默认设置' }).click();
  await page.getByText('默认设置已保存，下次打开 License 生成页时生效').waitFor();
  assert.equal(saved.products.fbasecman.default_version, '2.0');
  assert.equal(saved.products.fbasecman.validity.years, 2);
  assert(!('allowed_versions' in saved.products.fbasecman));
  assert(!('password' in saved));
  assert.equal(saved.default_password, 'browser-test-default');
  assert.equal(saved.output_directory, '/tmp/browser-license-output');
  await page.locator('.platform-sidebar .ant-menu-title-content').filter({ hasText: /^License 生成$/ }).click();
  await page.getByRole('heading', { name: 'License 生成', exact: true }).waitFor();
  const cman = page.locator('.license-product-row').filter({ hasText: 'fbasecman' });
  assert.equal(await cman.locator('input:not([role="combobox"]):not([type="date"])').inputValue(), '2.0');
  assert.equal(await page.locator('#password').inputValue(), 'browser-test-default');
  assert.equal(await page.locator('#purpose').inputValue(), '浏览器测试用途');
  assert.equal(Number((await cman.locator('input[type="date"]').inputValue()).slice(0, 4)), new Date().getFullYear() + 2);
  assert.equal(await page.locator('#output_directory').inputValue(), '/tmp/browser-license-output');
  await page.getByRole('checkbox', { name: '同时保存到服务器目录', exact: true }).uncheck();
  assert.equal(await page.locator('#output_directory').count(), 0);
  const dates = page.locator('.license-product-row input[type="date"]');
  const firstDate = await dates.first().inputValue();
  assert.equal(Number(firstDate.slice(0, 4)), new Date().getFullYear() + 10);
  await dates.first().fill('2040-01-01');
  await page.getByRole('button', { name: '添加产品' }).click();
  assert.equal(await dates.last().inputValue(), firstDate);
  assert.equal(await dates.first().inputValue(), '2040-01-01');
  assert.deepEqual(errors, []);
  console.log('License defaults browser checks passed (writes isolated).');
} finally { await browser.close(); }
