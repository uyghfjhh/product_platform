import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';
// Run only against the isolated fake installation fixture, never a production API.
const base = process.env.PLATFORM_URL || 'http://127.0.0.1:18769';
if (!process.env.DEPLOYMENT_FIXTURE) throw new Error('Set DEPLOYMENT_FIXTURE to the fixture JSON printed by deployment-workbench-fixture.py');
const fixture = JSON.parse(await readFile(process.env.DEPLOYMENT_FIXTURE, 'utf8'));
// AntD's leaving loading icon can remain in the accessible name; match the action suffix.
const name = '工作台浏览器验收-' + Date.now();
const browser = await chromium.launch({ headless: true });
try {
 const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
 const errors=[]; page.on('pageerror',error=>errors.push(error.message));
 const handshake = await page.request.get(`${base}/api/v1/deployment-browser-fixture`);
 assert.equal(handshake.status(), 200, 'Refuse to mutate a non-fixture platform');
 assert.equal((await handshake.json()).nonce, fixture.nonce);
 const environmentId='env-browser-'+Date.now();
 const registered=await page.request.post(`${base}/api/v1/environments`,{data:{
  id:environmentId,product_id:'fbase-database',title:name,host:'127.0.0.1',port:7000,
 }});
 assert.equal(registered.status(),201,await registered.text());
 await page.goto(`${base}/?page=deployment&env=${environmentId}`,{waitUntil:'domcontentloaded'});
 await page.getByRole('button',{name:'配置部署方案',exact:true}).click();
 const dialog=page.getByRole('dialog');
 const ready=()=>page.waitForFunction(()=>!document.querySelector('.ant-modal .ant-btn-loading'));
 const next=async()=>{await ready();await dialog.getByRole('button',{name:/下一步$/}).click();};
 await dialog.getByLabel('方案名称',{exact:true}).fill(name);
 await next();
 await dialog.getByLabel('数据库安装目录（PGHOME）',{exact:true}).fill(fixture.home);
 await dialog.getByLabel('数据根目录',{exact:true}).fill(fixture.data_root);
 await dialog.getByLabel('License 文件',{exact:true}).fill(fixture.license_file);
 await dialog.getByLabel('主节点起始端口',{exact:true}).fill('7000');
 await dialog.getByRole('button',{name:'探测数据库安装',exact:true}).click();
 await next();
 await dialog.getByText('mac_primary',{exact:true}).waitFor();
 await ready();
 await dialog.getByRole('button',{name:'保存草稿',exact:true}).click();
 await page.getByText('草稿已保存',{exact:true}).waitFor();
 const saved=await page.request.get(`${base}/api/v1/deployment/drafts`);
 const draft=(await saved.json()).find(item=>item.spec.title===name);
 assert.equal(draft.id,environmentId);assert.equal(draft.spec.nodes.length,3);
 await dialog.locator('.ant-modal-close').click();
 await page.getByRole('button',{name:'配置部署方案',exact:true}).click();
 await dialog.getByLabel('方案名称',{exact:true}).waitFor({state:'visible'});
 await page.waitForFunction((title)=>document.querySelector('.ant-modal input')?.value===title,name);
 await next();
 await dialog.getByLabel('数据库安装目录（PGHOME）',{exact:true}).waitFor({state:'visible'});
 assert.equal(await dialog.getByLabel('数据库安装目录（PGHOME）',{exact:true}).inputValue(),fixture.home);
 await next();
 await dialog.getByRole('button',{name:'检查并生成部署计划',exact:true}).click();
 await dialog.getByText('检查通过，可关联方案或提交执行',{exact:true}).waitFor();
 assert.equal(await dialog.getByRole('button',{name:'按计划部署并验收',exact:true}).isDisabled(),true);
 await dialog.getByRole('button',{name:'查看生成 YAML',exact:true}).click();
 await dialog.locator('.raw-report').waitFor();
 assert.ok((await dialog.locator('.raw-report').textContent()).includes(fixture.home));
 await dialog.getByRole('button',{name:'仅关联环境',exact:true}).click();
 await page.getByText('集群拓扑',{exact:true}).waitFor();
 await page.locator('.topo-node').nth(2).waitFor();assert.equal(await page.locator('.topo-node').count(),3);
 const environment=await page.request.get(`${base}/api/v1/environments/${draft.id}`);
 assert.ok((await environment.json()).deployment_config.includes('/deployment-plans/'));
 assert.deepEqual(errors,[]);
 console.log('Workbench browser checks passed: discover, save/resume, nodes, checked plan, YAML preview and environment link');
}catch(error){
 const page=browser.contexts()[0]?.pages()[0];
 console.log(await page?.locator('.ant-modal').innerText().catch(()=>''));
 console.log(await page?.locator('.ant-modal button').evaluateAll(buttons=>buttons.map(button=>({text:button.textContent,aria:button.getAttribute('aria-label'),html:button.outerHTML}))).catch(()=>[]));
 throw error;
}finally{await browser.close();}
