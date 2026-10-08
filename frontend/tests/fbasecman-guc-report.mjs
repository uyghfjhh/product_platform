import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';

const base=process.env.PLATFORM_URL || 'http://127.0.0.1:8080';
const environment=process.env.GUC_ENVIRONMENT || 'cman-mmr';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1600,height:1100}});
const errors=[];page.on('pageerror',error=>errors.push(error.message));
const results=[];
const targets=['extended_boundary','transaction_sync','savepoint_report','backend_redeploy']
 .flatMap(group=>['sql_parse','hint'].map(mode=>`guc.${group}_${mode}`));
try {
 for(const target of targets){
  await page.goto(`${base}/?page=tests:fbasecman:cman&env=${environment}`);
  await page.getByPlaceholder(/搜索用例名称/).fill(target);
  await page.locator('.suite-card').first().waitFor();
  await page.getByRole('button',{name:/全部展开/}).click();
  const row=page.locator('.case-row').filter({has:page.locator(`.case-name[title="${target}"]`)});
  await row.locator('.btn-view-report').first().click();
  const dialog=page.getByRole('dialog').last();
  await dialog.getByText('测试目的',{exact:true}).first().waitFor({timeout:30000});
  const cards=dialog.locator('.cman-evidence-card');
  assert.ok(await cards.count()>0);
  assert.ok(await cards.count()<=50,'仅渲染当前页，不能一次生成全部大报告卡片');
  const hasPagination = await dialog.locator('.cman-report-pagination').count() > 0;
  if (hasPagination) {
    await dialog.locator('.cman-report-pagination').first().locator('.ant-pagination-item-2').click();
    await page.waitForFunction(()=>document.querySelector('.regress-report-modal .cman-step-number')?.textContent==='51');
    assert.equal(await cards.locator('.cman-step-number').first().innerText(),'51');
  }
  assert.ok(await cards.first().getByText('期望结果',{exact:true}).count()>0);
  assert.ok(await cards.first().getByText('执行命令、实际结果与检查分析',{exact:true}).count()>0);
  await dialog.getByRole('tab',{name:/原始报告/}).click();
  const raw=await dialog.locator('pre.raw-report').innerText();
  assert.ok(raw.includes(target),'原始报告必须是当前用例真实报告');
  await dialog.getByRole('tab',{name:/诊断日志/}).click();
  await dialog.getByPlaceholder('搜索原始日志').waitFor({timeout:10000});
  await dialog.getByRole('log',{name:'原始日志'}).waitFor({timeout:15000});
  assert.ok((await dialog.getByRole('log',{name:'原始日志'}).innerText()).length>0);
  await dialog.getByRole('tab',{name:/验证结果与证据/}).click();
  await page.screenshot({path:`../output/fbasecman/browser-guc-check/${target}-paginated-report.png`,fullPage:true});
  results.push({target,cards:await cards.count(),raw_length:raw.length,pagination:hasPagination,raw_report:true,diagnostic_log:true});
  console.log(target,'报告、翻页、原文通过');
 }
 assert.deepEqual(errors,[]);
 await fs.writeFile('../output/fbasecman/browser-guc-check/report-pagination.json',JSON.stringify({results,errors},null,2));
}finally{await browser.close();}
