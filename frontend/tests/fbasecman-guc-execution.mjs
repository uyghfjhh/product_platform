import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

// Real UI submissions and real worker results: no route mocks or fixture data.
const base = process.env.PLATFORM_URL || 'http://127.0.0.1:8080';
const environment = process.env.GUC_ENVIRONMENT || 'cman-mmr';
const output = path.resolve(process.env.GUC_OUTPUT || '../output/fbasecman/browser-guc-check');
await fs.mkdir(output, { recursive: true });
const allTargets = ['extended_boundary','transaction_sync','savepoint_report','backend_redeploy']
  .flatMap(group => ['sql_parse','hint'].map(mode => `guc.${group}_${mode}`));
const targets = process.env.GUC_TARGETS ? process.env.GUC_TARGETS.split(',') : allTargets;
assert.ok(targets.every(target=>allTargets.includes(target)));
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {}) });
const page = await browser.newPage({ viewport: {width:1600,height:1100} });
const pageErrors = [];
page.on('pageerror', error => pageErrors.push(error.message));
const results = [];
const url = `${base}/?page=tests:fbasecman:cman&env=${environment}`;
async function openRow(target) {
  await page.goto(url, {waitUntil:'domcontentloaded'});
  await page.getByPlaceholder(/搜索用例名称/).fill(target);
  await page.locator('.suite-card').first().waitFor({timeout:30000});
  await page.getByRole('button', {name:/全部展开/}).click();
  const row = page.locator('.case-row').filter({has:page.locator(`.case-name[title="${target}"]`)});
  await row.waitFor({state:'visible',timeout:30000});
  return row;
}
try {
  for (const target of targets) {
    const previous = path.join(output,`${target}-task.json`);
    let final;
    try { final = JSON.parse(await fs.readFile(previous,'utf8')); } catch {}
    let task = final;
    if (!final) {
    const row = await openRow(target);
    const submitted = page.waitForResponse(response => response.url().endsWith('/api/v1/operations') && response.request().method()==='POST', {timeout:30000});
    await row.locator('button.btn-run-case').click();
    const response = await submitted;
    assert.equal(response.status(),202,await response.text());
    task = await response.json();
    assert.equal(task.target,target);
    assert.equal(task.environment_id,environment);
    console.log(JSON.stringify({event:'submitted',target,task_id:task.id}));
    const deadline = Date.now()+240000;
    while (Date.now()<deadline) {
      const state = await page.request.get(`${base}/api/v1/operations/${task.id}`);
      assert.equal(state.status(),200);
      final = await state.json();
      if (!['QUEUED','PENDING','RUNNING','CANCELLING','DISPATCHING'].includes(final.status)) break;
      await page.waitForTimeout(2000);
    }
    assert.ok(final && !['QUEUED','PENDING','RUNNING','CANCELLING','DISPATCHING'].includes(final.status),`任务超时 ${task.id}`);
    await fs.writeFile(path.join(output,`${target}-task.json`),JSON.stringify(final,null,2));
    const logResponse = await page.request.get(`${base}/api/v1/operations/${task.id}/log?limit=2000`);
    const log = logResponse.ok() ? await logResponse.json() : {status:logResponse.status()};
    await fs.writeFile(path.join(output,`${target}-log.json`),JSON.stringify(log,null,2));
    await page.screenshot({path:path.join(output,`${target}-terminal.png`),fullPage:true});
    }
    const result = {target,task_id:task.id,status:final.status,reason:final.reason,
      progress:final.progress,finished_at:final.finished_at};
    const reportRow = await openRow(target);
    await reportRow.locator('.btn-view-report').first().waitFor({state:'visible'});
    await reportRow.locator('.btn-view-report').first().click();
    const dialog = page.getByRole('dialog').last();
    await dialog.waitFor({timeout:15000});
    await dialog.getByText('测试目的',{exact:true}).first().waitFor({timeout:30000});
    result.report_purpose_visible = true;
    result.row_text = await reportRow.innerText();
    result.report_text = (await dialog.innerText()).slice(0,12000);
    await page.screenshot({path:path.join(output,`${target}-report.png`),fullPage:true});
    results.push(result);
    await fs.writeFile(path.join(output,'results.json'),JSON.stringify({environment,results,pageErrors},null,2));
    console.log(JSON.stringify({event:'completed',...result,report_text:undefined,row_text:undefined}));
    const close = dialog.locator('.ant-modal-close');
    if (await close.count()) await close.click();
  }
  assert.deepEqual(pageErrors,[],'网页 JavaScript 异常');
  console.log(JSON.stringify({event:'finished',count:results.length,output}));
} finally {
  await fs.writeFile(path.join(output,'results.json'),JSON.stringify({environment,results,pageErrors},null,2));
  await browser.close();
}
