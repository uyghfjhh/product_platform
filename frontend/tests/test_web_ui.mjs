import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = 'http://127.0.0.1:8080';
const browser = await chromium.launch({ headless: true });
const errors = [];

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.log('Console error:', msg.text());
  });

  console.log('1. Loading base page...');
  await page.goto(base, { waitUntil: 'networkidle' });

  console.log('2. Verifying left navigation...');
  const menuText = await page.locator('.platform-sidebar').innerText();
  console.log('Sider text:', menuText.replace(/\n+/g, ' '));
  assert.ok(menuText.includes('数据库部署管理'), 'Must include 数据库部署管理');
  assert.ok(menuText.includes('测试'), 'Must include 测试');
  assert.ok(menuText.includes('多活'), 'Must include 多活');
  assert.ok(menuText.includes('等保'), 'Must include 等保');
  assert.ok(menuText.includes('fbasecman'), 'Must include fbasecman');
  assert.ok(menuText.includes('License 管理'), 'Must include License 管理');

  console.log('3. Navigating to fbasecman 测试 page...');
  await page.getByRole('menuitem', { name: 'fbasecman' }).click();
  await page.waitForTimeout(1000);

  console.log('4. Verifying main page content...');
  const mainText = await page.locator('.platform-content').innerText();
  console.log('Main snippet:', mainText.slice(0, 150).replace(/\n+/g, ' '));
  assert.ok(mainText.includes('总测试用例'), 'Must show current test summary');
  assert.ok(mainText.includes('综合通过率'), 'Must show current test pass rate');

  console.log('5. Verifying test suites display...');
  const collapseItems = page.locator('.suite-card, .ant-collapse-item');
  const count = await collapseItems.count();
  console.log(`Found ${count} collapse items/suites.`);
  assert.ok(count > 0, 'Must show test suites');

  console.log('6. Searching for ha_rep_promote...');
  const searchInput = page.getByPlaceholder(/搜索/i).first();
  await searchInput.fill('ha_rep_promote');
  await page.waitForTimeout(1000);

  console.log('7. Verifying filtered case ha_rep_promote...');
  const caseText = await page.locator('.platform-content').innerText();
  assert.ok(caseText.includes('ha_rep_promote'), 'Must display ha_rep_promote');

  console.log('8. Opening test report drawer for ha_rep_promote...');
  const reportBtn = page.getByRole('button', { name: /报告/ }).first();
  await reportBtn.click();
  await page.waitForTimeout(1500);

  const drawerTitle = await page.locator('.ant-modal-title').innerText();
  console.log('Report Drawer opened with title:', drawerTitle);
  assert.ok(drawerTitle.includes('ha_rep_promote'), 'Drawer title must include case name');

  // Check tabs
  const tabs = await page.locator('.ant-modal .ant-tabs-tab').allInnerTexts();
  console.log('Report tabs available:', tabs);
  assert.ok(tabs.some(t => t.includes('步骤')), 'Must have 步骤 tab');
  assert.ok(tabs.some(t => t.includes('检测项')), 'Must have 检测项 tab');
  assert.ok(tabs.some(t => t.includes('日志')), 'Must have 日志 tab');
  assert.ok(tabs.some(t => t.includes('原始报告')), 'Must have 原始报告 tab');

  // Verify steps tab content
  const drawerBody = await page.locator('.ant-modal-body').innerText();
  assert.ok(drawerBody.includes('PASS'), 'Report drawer must display PASS status');
  assert.ok(drawerBody.includes('准备测试数据表') || drawerBody.includes('隔离旧主') || drawerBody.includes('升主'), 'Report drawer must show test steps');
  console.log('Step details and PASS status verified in ReportDrawer!');

  // Close drawer
  await page.locator('.ant-modal-close').click();
  await page.waitForTimeout(500);

  console.log('9. Clicking execution button on case...');
  const runButtons = page.locator('button.ant-btn, button.btn-run-case, button.suite-action-btn').filter({ hasText: /执行/ });
  const runBtnCount = await runButtons.count();
  console.log(`Found ${runBtnCount} real execution buttons.`);
  assert.ok(runBtnCount > 0, 'Must have at least one execution button');

  // Click the case-level run button (usually the last or second button)
  const targetBtn = runButtons.last();
  console.log('Clicking target button:', await targetBtn.innerText());
  await targetBtn.click();
  await page.waitForTimeout(1000);

  // If confirm modal appeared (for suite run), click confirm
  const confirmBtn = page.getByRole('button', { name: '确认执行' });
  if (await confirmBtn.isVisible()) {
    console.log('Confirming execution modal...');
    await confirmBtn.click();
    await page.waitForTimeout(1500);
  }

  const messages = await page.locator('.ant-message-notice').allInnerTexts();
  console.log('Ant messages on screen:', messages);

  // Check if TaskDrawer is open
  const taskDrawer = page.locator('.ant-drawer-open');
  const taskDrawerVisible = await taskDrawer.isVisible();
  console.log('Task drawer opened successfully:', taskDrawerVisible);
  assert.ok(taskDrawerVisible, 'Task drawer must open after clicking 执行');

  // Close the task drawer
  await page.locator('.ant-drawer-close').last().click();
  await page.waitForTimeout(500);

  console.log('10. Testing navigation to 多活 and 等保 tabs...');
  await page.getByRole('menuitem', { name: '多活' }).click();
  await page.waitForTimeout(800);
  const mmrText = await page.locator('.platform-content').innerText();
  assert.ok(mmrText.includes('总测试用例') || mmrText.includes('多活'), 'Must navigate to MMR tests');

  await page.getByRole('menuitem', { name: '等保' }).click();
  await page.waitForTimeout(800);
  const macText = await page.locator('.platform-content').innerText();
  assert.ok(macText.includes('总测试用例') || macText.includes('等保'), 'Must navigate to MAC tests');

  console.log('11. Testing navigation to 数据库部署管理 and License 管理...');
  await page.getByRole('menuitem', { name: '数据库部署管理' }).click();
  await page.waitForTimeout(800);
  const deployText = await page.locator('.platform-content').innerText();
  assert.ok(deployText.includes('部署拓扑') || deployText.includes('部署方案') || deployText.includes('环境'), 'Must navigate to deployment');

  await page.getByRole('menuitem', { name: 'License 管理' }).click();
  await page.waitForTimeout(800);
  const licenseText = await page.locator('.platform-content').innerText();
  assert.ok(licenseText.includes('License') || licenseText.includes('授权'), 'Must navigate to license');
  assert.ok(licenseText.includes('密钥版本管理'), 'Must show key management');
  assert.ok(licenseText.includes('指纹'), 'Must show public key fingerprint');
  assert.ok(await page.getByRole('button', { name: '删除版本' }).isVisible(), 'Must show key deletion control');

  assert.deepEqual(errors, []);
  console.log('SUCCESS: ALL WEB UI INTERACTIONS AND REPORT QUALITY VALIDATED COMPLETELY!');
} finally {
  await browser.close();
}
