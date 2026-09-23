import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.PLATFORM_URL || 'http://127.0.0.1:8766';
const browser = await chromium.launch({ headless: true });
const errors = [];

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: '工作台' }).waitFor();

  // 隔离数据目录中建立一个环境，验证页面与 API 对同一资源的观察。
  const environment = {
    id: 'browser-cman', product_id: 'fbasecman', title: '浏览器验收环境',
    host: '127.0.0.1', port: 15011, database_name: 'postgres',
    database_user: 'postgres',
  };
  const existing = await page.request.get(`${base}/api/v1/environments/${environment.id}`);
  if (existing.status() === 404) {
    const created = await page.request.post(`${base}/api/v1/environments`, { data: environment });
    assert.equal(created.status(), 201, await created.text());
  }

  await page.reload({ waitUntil: 'networkidle' });
  await page.getByRole('menuitem', { name: '产品与环境' }).click();
  await page.getByText('浏览器验收环境').first().waitFor();

  await page.getByRole('combobox', { name: '当前产品' }).click();
  await page.getByText('fbasecman', { exact: true }).last().click();
  await page.getByRole('menuitem', { name: '部署管理' }).click();
  await page.getByRole('button', { name: '生成方案' }).click();
  await page.getByRole('dialog', { name: '生成 pgcluster 回归方案' }).waitFor();
  await page.getByRole('button', { name: '生成并校验' }).click();
  await page.getByText('部署拓扑').waitFor();
  await page.locator('.react-flow__node').first().waitFor();
  assert.equal(await page.locator('.react-flow__node').count(), 14);
  await page.locator('.react-flow__node').first().click();
  await page.getByText('数据目录').last().waitFor();
  await page.getByRole('button', { name: '启动节点' }).waitFor();
  await page.locator('.ant-drawer-close').last().click();
  await page.screenshot({ path: '/tmp/product-platform-deployment-desktop.png', fullPage: true });

  await page.getByRole('menuitem', { name: '自动化测试' }).click();
  await page.getByPlaceholder('搜索目标或用例说明').fill('ha_commands.set_node_invalid_datasource');
  await page.getByText('ha_commands.set_node_invalid_datasource').first().waitFor();
  assert.ok((await page.locator('tbody tr').count()) > 0);
  await page.screenshot({ path: '/tmp/product-platform-tests-desktop.png', fullPage: true });

  await page.getByRole('menuitem', { name: 'License 生成' }).click();
  await page.getByText('授权产品').waitFor();
  await page.screenshot({ path: '/tmp/product-platform-license-desktop.png', fullPage: true });

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
  mobile.on('pageerror', (error) => errors.push(error.message));
  await mobile.goto(base, { waitUntil: 'networkidle' });
  assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth - innerWidth), 0);
  await mobile.screenshot({ path: '/tmp/product-platform-mobile.png', fullPage: true });

  assert.deepEqual(errors, []);
  console.log('Browser checks passed: desktop deployment, cases, License, mobile layout');
} finally {
  await browser.close();
}
