import assert from 'node:assert/strict';
import { chromium } from 'playwright';
const browser=await chromium.launch({headless:true});
try {
 const page=await browser.newPage({viewport:{width:1600,height:1000}});
 await page.addInitScript(()=>{localStorage.setItem('platform-page','tests:fbasecman:cman');localStorage.setItem('platform-environment','cman-lab');});
 await page.goto(process.env.PLATFORM_URL || 'http://127.0.0.1:8080',{waitUntil:'domcontentloaded'});
 await page.getByPlaceholder(/搜索用例名称/).fill('transaction_sync_hint');
 await page.locator('.case-row').filter({has:page.locator('.case-name[title="guc.transaction_sync_hint"]')}).getByRole('button',{name:'📄 查看报告',exact:true}).click();
 const modal=page.getByRole('dialog');
 const group=modal.locator('.cman-scenario-group').filter({hasText:'SET LOCAL 的事务作用域'}).first();
 await group.locator(':scope > summary').click();
 const table=group.locator('.cman-transaction-table').first();await table.waitFor();
 assert.deepEqual(await table.locator('thead th').allTextContents(),['阶段／实际操作','参数','前次实测','预期','实测','实际连接／判定']);
 assert.match(await group.textContent(),/不写入持久会话同步缓存/);
 assert.match(await table.textContent(),/事务内/);
 assert.match(await table.textContent(),/提交后|回滚后/);
 assert.match(await table.textContent(),/32MB/);
 await table.getByText('本事务实际执行 SQL',{exact:true}).first().click();
 assert.match(await table.textContent(),/SET LOCAL work_mem/);
 console.log('Transaction report browser passed: transaction stages, actual SQL and LOCAL non-persistence (read-only).');
} finally {await browser.close();}
