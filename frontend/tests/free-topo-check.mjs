import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';
// Run only against the isolated fake installation fixture, never a production API.
const base = process.env.PLATFORM_URL || 'http://127.0.0.1:18769';
if (!process.env.DEPLOYMENT_FIXTURE) throw new Error('Set DEPLOYMENT_FIXTURE to the fixture JSON printed by deployment-workbench-fixture.py');
const fixture = JSON.parse(await readFile(process.env.DEPLOYMENT_FIXTURE, 'utf8'));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  const handshake = await page.request.get(`${base}/api/v1/deployment-browser-fixture`);
  assert.equal(handshake.status(), 200, 'Refuse to mutate a non-fixture platform');
  assert.equal((await handshake.json()).nonce, fixture.nonce);
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: '新建部署方案', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('方案名称', { exact: true }).fill('自由拓扑验收');
  await dialog.getByLabel('部署模板').click();
  await page.locator('.ant-select-item-option-content').first().click();
  await dialog.locator('label:has-text("自由拓扑")').click();
  // antd Button re-renders on busy and can detach mid-click; use text locators for wizard navigation
  await dialog.locator('button:visible', { hasText: '下一步' }).click();
  await dialog.getByLabel('目标主机').fill('127.0.0.1');
  await dialog.getByLabel('数据库安装目录（PGHOME）').fill(fixture.home);
  await dialog.getByLabel('数据根目录').fill(fixture.data_root);
  await dialog.getByLabel('License 文件').fill(fixture.license_file);
  await dialog.getByLabel('主节点起始端口').fill('46120');
  await dialog.locator('button:visible', { hasText: '下一步' }).click();
  await dialog.locator('.react-flow:visible').waitFor();
  await dialog.locator('button:visible', { hasText: '添加主节点' }).click();
  await dialog.locator('button:visible', { hasText: '添加备库' }).click();
  await dialog.locator('button:visible', { hasText: '添加备库' }).click();
  await page.waitForTimeout(500);
  assert.equal(await dialog.locator('.react-flow__node').count(), 3, 'canvas nodes');
  const canvasLabels = await dialog.locator('.react-flow__node').allTextContents();
  assert.ok(canvasLabels.some((label) => label.includes('主')), 'primary node labeled');
  const edges = await dialog.locator('.react-flow__edge').count();
  assert.equal(edges, 2, 'two streaming edges');
  // select a standby and rename it — selection must survive the rename
  await dialog.locator('.react-flow__node').nth(1).click();
  const nameInput = dialog.locator('.ant-form-item:visible', { hasText: '节点名' }).locator('input');
  await nameInput.waitFor();
  await nameInput.fill('stb_a');
  await dialog.locator('.ant-form-item:visible', { hasText: '端口' }).waitFor();
  // generate the deployment plan and inspect the compiled YAML
  await dialog.locator('button:visible', { hasText: '检查并生成部署计划' }).click();
  await dialog.getByText('检查通过，可关联方案或提交执行').waitFor();
  await dialog.locator('button:visible', { hasText: '查看生成 YAML' }).click();
  await dialog.locator('.raw-report').waitFor();
  const yaml = await dialog.locator('.raw-report').textContent();
  assert.ok(yaml.includes('streaming_clusters'), 'yaml has streaming cluster');
  assert.ok(yaml.includes('stb_a') && yaml.includes('node3'), 'renamed + default standbys present');
  assert.ok(yaml.includes(fixture.home), 'home in yaml');
  assert.deepEqual(errors, [], 'page errors: ' + errors.join(';'));
  console.log('free-topology browser acceptance OK');
} finally {
  await browser.close();
}
