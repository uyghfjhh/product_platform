import assert from 'node:assert/strict';
import { chromium } from 'playwright';

// Read an existing archive only; this check never launches database cases.
const base = process.env.PLATFORM_URL || 'http://127.0.0.1:8080';
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1600 } });
  await page.goto(`${base}/?page=tests:fbasecman:ha_commands`);
  await page.getByPlaceholder(/搜索用例名称/).fill('ha_commands.sql_parse_extended_protocol');
  const row = page.locator('.case-row').filter({ has: page.locator('.case-name[title="ha_commands.sql_parse_extended_protocol"]') });
  await row.getByRole('button', { name: /查看报告/ }).click();
  const modal = page.getByRole('dialog');
  await modal.getByText('测试目的', { exact: true }).waitFor();
  await modal.getByText('连接与事务范围', { exact: true }).waitFor();
  const scope = await modal.locator('.cman-transaction-scope').innerText();
  assert.match(scope, /同一个 JDBC Connection/);
  assert.match(scope, /事务 1/);
  assert.match(scope, /事务 2/);
  assert.match(scope, /事务 3/);
  assert.match(scope, /实际执行回滚/);
  for (const label of ['期望结果', '实际结果', '判定依据']) {
    await modal.getByText(label, { exact: true }).first().waitFor();
  }
  assert.ok(await modal.locator('.cman-evidence-card').count() > 0);
  const cards = await modal.locator('.cman-evidence-card').allInnerTexts();
  assert.ok(cards.every(text => !text.includes('工件证据') && !text.includes('command-9.json')));
  assert.ok(cards.every(text => !text.includes('原始输出')));
  assert.ok(cards.some(text => text.includes('参数查询返回值：42')));
  assert.match(await modal.locator('.report-header').innerText(), /PreparedStatement|SQL_PARSE/);
  if (process.env.REPORT_EXPECT_EXAMPLES) {
    await modal.getByText('关键代码（节选）', { exact: true }).first().waitFor();
    assert.ok(await modal.getByText(/statement.setInt\(1, 42\)/).count() > 0);
  }
  if (process.env.REPORT_SCREENSHOT) {
    const box = await modal.boundingBox();
    const first = await modal.locator('.cman-evidence-card').first().boundingBox();
    await page.screenshot({ path: process.env.REPORT_SCREENSHOT, clip: {
      x: box.x, y: box.y, width: box.width,
      height: Math.min(page.viewportSize().height - box.y, first.y + first.height + 16 - box.y),
    } });
  }
  await modal.getByRole('button', { name: /查看全部步骤/ }).click();
  assert.ok(await modal.getByText('执行动作', { exact: true }).count() > 0);
  await modal.getByRole('tab', { name: '原始报告（report.txt）' }).click();
  await modal.locator('pre.raw-report').waitFor();
  assert.match(await modal.locator('pre.raw-report').innerText(), /用例: ha_commands.sql_parse_extended_protocol/);
  console.log('fbasecman report browser checks passed');
} finally {
  await browser.close();
}
