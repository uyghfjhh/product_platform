import assert from 'node:assert/strict';
import { chromium } from 'playwright';

// 通用页面巡检：菜单树、测试页、真实报告 Modal、执行按钮、跨页导航。
// PlatformShell 改版后菜单为两级子菜单结构（见 tests/README.md）。
const base = process.env.PLATFORM_URL || 'http://127.0.0.1:8080';
const browser = await chromium.launch({ headless: true });
const errors = [];

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.log('Console error:', msg.text());
  });

  console.log('1. Loading base page...');
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.locator('.platform-content').waitFor();

  console.log('2. Verifying left navigation (expand product submenus)...');
  const sider = page.locator('.platform-sidebar');
  const menu = (name) => sider.locator('.ant-menu-title-content', { hasText: name }).first();
  // 展开产品子菜单以露出 profile 叶子（产品异步加载，defaultOpenKeys 不生效）
  for (const product of ['接入示例', 'FBase 数据库', 'fbasecman']) {
    await menu(product).click();
    await page.waitForTimeout(300);
  }
  const menuText = await sider.innerText();
  for (const expected of ['数据库部署管理', '产品测试中心', '接入示例', '接入验证',
    'FBase 数据库', '多活回归测试', '等保回归测试', 'fbasecman', 'fbasecman 回归测试',
    '稳定性测试', 'License 授权管理', '密钥管理', 'License 生成']) {
    assert.ok(menuText.includes(expected), `Must include ${expected}`);
  }

  console.log('3. Navigating to fbasecman 回归测试 page...');
  await menu('fbasecman 回归测试').click();
  await page.waitForTimeout(1500);

  console.log('4. Verifying main page content...');
  const mainText = await page.locator('.platform-content').innerText();
  console.log('Main snippet:', mainText.slice(0, 150).replace(/\n+/g, ' '));
  assert.ok(mainText.includes('总测试用例'), 'Must show current test summary');
  assert.ok(mainText.includes('综合通过率'), 'Must show current test pass rate');

  console.log('5. Verifying test suites display...');
  const count = await page.locator('.suite-card').count();
  console.log(`Found ${count} suite cards.`);
  assert.ok(count > 0, 'Must show test suites');

  const target = 'ha_commands.set_node_promoted_idempotent';
  console.log(`6. Searching for ${target}...`);
  await page.getByPlaceholder(/搜索用例名称/).fill(target);
  await page.locator(`.case-name[title="${target}"]`).waitFor();

  console.log(`7. Opening test report modal for ${target}...`);
  await page.locator('.case-row', { has: page.locator(`.case-name[title="${target}"]`) })
    .locator('.btn-view-report').first().click();
  await page.locator('.regress-report-modal').waitFor();
  const drawerTitle = await page.locator('.ant-modal-title').innerText();
  console.log('Report modal opened with title:', drawerTitle);
  assert.ok(drawerTitle.includes(target), 'Modal title must include case name');

  const tabs = await page.locator('.regress-report-modal .ant-tabs-tab').allInnerTexts();
  console.log('Report tabs available:', tabs);
  assert.ok(tabs.some((t) => t.includes('步骤')), 'Must have 步骤 tab');
  assert.ok(tabs.some((t) => t.includes('检测项')), 'Must have 检测项 tab');
  assert.ok(tabs.some((t) => t.includes('日志')), 'Must have 日志 tab');
  assert.ok(tabs.some((t) => t.includes('原始报告')), 'Must have 原始报告 tab');

  const modalBody = await page.locator('.regress-report-modal .ant-modal-body').innerText();
  assert.ok(modalBody.includes('PASS'), 'Report modal must display PASS status');
  await page.locator('.regress-report-modal .ant-modal-close').first().click();
  await page.waitForTimeout(500);

  console.log('8. Verifying case run button exists...');
  const runBtn = page.locator('.case-row', { has: page.locator(`.case-name[title="${target}"]`) })
    .locator('.btn-run-case');
  assert.ok(await runBtn.isVisible(), 'Must show case-level 执行 button');
  assert.ok(await runBtn.isEnabled(), '执行 button must be enabled (env bound)');
  // 不实际点击：执行会真机跑用例，冒烟只验证控件可用

  console.log('9. Testing navigation to 多活/等保 profiles...');
  await menu('多活回归测试').click();
  await page.waitForTimeout(1000);
  const mmrText = await page.locator('.platform-content').innerText();
  assert.ok(mmrText.includes('总测试用例'), 'Must navigate to MMR tests');

  await menu('等保回归测试').click();
  await page.waitForTimeout(1000);
  const macText = await page.locator('.platform-content').innerText();
  assert.ok(macText.includes('总测试用例'), 'Must navigate to MAC tests');

  console.log('10. Testing navigation to 数据库部署管理 and License 授权管理...');
  await menu('数据库部署管理').click();
  await page.waitForTimeout(800);
  const deployText = await page.locator('.platform-content').innerText();
  assert.ok(deployText.includes('部署') || deployText.includes('环境'), 'Must navigate to deployment');

  if (!(await menu('密钥管理').isVisible().catch(() => false))) {
    await menu('License 授权管理').click();
  }
  await menu('密钥管理').click();
  await page.getByRole('heading', { name: '密钥管理' }).waitFor();
  await page.getByText('生成密钥').waitFor();  // options 异步载入后才渲染表单
  const licenseText = await page.locator('.platform-content').innerText();
  assert.ok(licenseText.includes('生成密钥'), 'Must show key generation control');

  assert.deepEqual(errors, []);
  console.log('SUCCESS: ALL WEB UI INTERACTIONS AND REPORT QUALITY VALIDATED COMPLETELY!');
} finally {
  await browser.close();
}
