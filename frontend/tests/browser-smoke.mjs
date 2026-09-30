import assert from 'node:assert/strict';
import { chromium } from 'playwright';

// PlatformShell 改版后的冒烟：部署页向导+拓扑、测试中心用例搜索、License、移动端。
// 页面结构详见 tests/README.md「已知失修」节的实测记录。
const base = process.env.PLATFORM_URL || 'http://127.0.0.1:8080';
const browser = await chromium.launch({ headless: true });
const errors = [];

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  // 等待部署环境选择区加载（顶部标题栏已移除）
  await page.locator('.platform-content').waitFor();

  const sider = page.locator('.platform-sidebar');
  // 带图标菜单项的可访问名是"图标label+文字"（如 "tool 数据库部署管理"），
  // role+name 匹配不可靠——按 .ant-menu-title-content 文本定位，点击冒泡到菜单项
  const menu = (name) => sider.locator('.ant-menu-title-content', { hasText: name }).first();
  // 子菜单叶子仅在父级展开时入 DOM；未展开则先点父级
  async function clickLeaf(parent, leaf) {
    const leafItem = menu(leaf);
    if (!(await leafItem.isVisible().catch(() => false))) {
      await menu(parent).click();
    }
    await leafItem.click();
  }

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

  await page.reload({ waitUntil: 'domcontentloaded' });
  // Segmented 环境切换条出现新环境 → 选中（产品上下文=fbasecman，决定部署向导出现）
  await page.getByText('浏览器验收环境').first().waitFor();
  await page.getByText('浏览器验收环境').first().click();

  // Existing product profile API remains compatible; seed only configuration,
  // then verify the new workbench can reopen it without database operations.
  const profileResponse = await page.request.post(`${base}/api/v1/environments/${environment.id}/fbasecman-profile`, {
    data: { mmr1_port: environment.port, data_root: '/home/postgres/fbasecman_regress_v2_mmr/browser-cman',
      license_file: '/home/postgres/license/license.dat' },
  });
  assert.equal(profileResponse.status(), 200, await profileResponse.text());
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: '配置部署方案', exact: true }).click();
  await page.getByText('部署方案工作台', { exact: true }).waitFor();
  await page.getByRole('dialog').locator('.ant-modal-close').click();
  await page.locator('.topo-node').first().waitFor({ timeout: 20000 });
  assert.equal(await page.locator('.topo-node').count(), 14);
  // 点击节点标题打开详情抽屉；卡片正中是"SQL 控制台"快捷行，
  // 几何中心点击会命中它（开 SQL 工作台而非详情）。
  await page.locator('.topo-node').first().locator('.node-label-text').click();
  await page.getByText('数据目录').last().waitFor();
  await page.getByRole('button', { name: '启动节点' }).waitFor();
  await page.locator('.ant-drawer-close').last().click();
  await page.screenshot({ path: '/tmp/product-platform-deployment-desktop.png', fullPage: true });

  // 产品测试中心 → fbasecman → 回归测试（tests:fbasecman:cman）→ 用例搜索
  // 用例行是 div.case-row；target 落在 case-name 的 title 属性（文本显示 c.name）
  await clickLeaf('fbasecman', 'fbasecman 回归测试');
  await page.getByPlaceholder(/搜索用例名称/).fill('ha_commands.set_node_invalid_datasource');
  await page.locator('.case-name[title="ha_commands.set_node_invalid_datasource"]').waitFor();
  assert.ok((await page.locator('.case-row').count()) > 0);
  await page.screenshot({ path: '/tmp/product-platform-tests-desktop.png', fullPage: true });

  // License 授权管理 → License 生成
  await clickLeaf('License 授权管理', 'License 生成');
  await page.getByText('授权产品').waitFor();
  await page.screenshot({ path: '/tmp/product-platform-license-desktop.png', fullPage: true });

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
  mobile.on('pageerror', (error) => errors.push(error.message));
  await mobile.goto(base, { waitUntil: 'domcontentloaded' });
  await mobile.locator('.platform-content').waitFor();
  assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth - innerWidth), 0);
  await mobile.screenshot({ path: '/tmp/product-platform-mobile.png', fullPage: true });

  assert.deepEqual(errors, []);
  console.log('Browser checks passed: deployment wizard+topology, tests search, License, mobile layout');
} finally {
  await browser.close();
}
