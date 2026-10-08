import assert from 'node:assert/strict';
import { chromium } from 'playwright';
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(() => {localStorage.setItem('platform-page','tests:fbasecman:cman');localStorage.setItem('platform-environment','cman-lab');});
  await page.goto(process.env.PLATFORM_URL || 'http://127.0.0.1:8080',{waitUntil:'domcontentloaded'});
  await page.getByPlaceholder(/搜索用例名称/).fill('extended_boundary_hint');
  await page.locator('.case-row').filter({has:page.locator('.case-name[title="guc.extended_boundary_hint"]')}).getByRole('button',{name:'📄 查看报告',exact:true}).click();
  const modal=page.getByRole('dialog');
  const group=modal.locator('.cman-scenario-group').filter({hasText:'Execute 后参数生效'}).first();
  await group.locator(':scope > summary').click();
  const table=group.locator('.cman-boundary-table').first();await table.waitFor();
  assert.deepEqual(await table.locator('thead th').allTextContents(),['实际动作／SQL','参数','前一次实测','本步预期','本步实测','为什么通过／失败']);
  assert.match(await table.textContent(),/准备语句／Portal，未执行|解析、绑定并描述 Portal，未执行/);
  assert.match(await table.textContent(),/真正执行已绑定的语句/);
  assert.match(await table.textContent(),/32MB/);
  assert.match(await group.textContent(),/本场景核心配置/);
  assert.deepEqual(errors,[]);
  console.log('Extended GUC report browser passed: execution timing, before/expected/actual values and configuration (read-only).');
} finally {await browser.close();}
