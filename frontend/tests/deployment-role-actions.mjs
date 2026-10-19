import assert from 'node:assert/strict';
import { chromium } from 'playwright';
const base=process.env.PLATFORM_URL || 'http://127.0.0.1:8080';
const environment=process.env.ROLE_ENVIRONMENT || 'env-605c7ca89e6f4cb2';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1600,height:1100}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try {
 await page.goto(`${base}/?page=deployment&env=${environment}`);
 await page.locator('#node_pg_1').click();
 const drawer=page.locator('body');
 assert.equal(await drawer.getByRole('button',{name:'备库升主',exact:true}).count(),0);
 await page.keyboard.press('Escape');
 await page.locator('#node_pg_3').click();
 await drawer.getByRole('button',{name:'备库升主',exact:true}).waitFor();
 await drawer.getByRole('button',{name:'备库升主',exact:true}).click();
 let modal=page.getByRole('dialog').last();
 await modal.getByRole('button',{name:'停止 pg_1，提升 pg_3'}).waitFor();
 assert.match(await modal.innerText(),/选中节点：pg_3/);
 assert.match(await modal.innerText(),/旧主 pg_1 保持停止/);
 await modal.getByRole('button',{name:/取\s*消/}).click();
 await drawer.getByRole('button',{name:'主备切换',exact:true}).click();
 modal=page.getByRole('dialog').last();
 await modal.getByRole('button',{name:'停止 pg_1，提升 pg_3'}).waitFor();
 assert.match(await modal.innerText(),/pg_1 的数据目录/);
 await page.screenshot({path:'../output/fbasecman/role-change-confirmation.png',fullPage:true});
 await modal.getByRole('button',{name:/取\s*消/}).click();
 // Simulate task transport only; never submit a real role mutation.
 const taskId='11111111-2222-4333-8444-555555555555'; let polls=0; let submitted=false;
 const task=()=>({id:taskId,environment_id:environment,action:'deployment.failover',target:'streaming.pg_cluster_1',
   status:polls>2?'SUCCEEDED':'RUNNING',reason:null,progress:{done:polls>2?6:0,total:6,label:'核对实际角色与目标'},last_sequence:3});
 await page.route('**/api/v1/operations',async route=>{
   if(route.request().method()!=='POST'){await route.continue();return;}
   const request=route.request().postDataJSON();
   assert.deepEqual(request.parameters,{selected_node:'pg_3',expected_primary:'pg_1'});
   assert.equal(request.target,'streaming.pg_cluster_1');submitted=true;
   await route.fulfill({status:202,json:task()});
 });
 await page.route(`**/api/v1/operations/${taskId}`,async route=>{polls++;await route.fulfill({json:task()});});
 await page.route(`**/api/v1/operations/${taskId}/events?*`,async route=>{
   const after=Number(new URL(route.request().url()).searchParams.get('after'));
   const lines=[`ROLE_STEP ${JSON.stringify({index:1,total:6,title:'核对实际角色与目标',status:'passed',elapsed:.2,at:new Date().toISOString()})}`,
     'COMMAND [local] psql -c SELECT pg_is_in_recovery()', 'STDOUT: t'];
   await route.fulfill({json:lines.map((line,index)=>({sequence:index+1,event_type:'command.output',payload:{line}})).filter(e=>e.sequence>after)});
 });
 await drawer.getByRole('button',{name:'备库升主',exact:true}).click();
 modal=page.getByRole('dialog').last();
 await modal.getByRole('button',{name:'停止 pg_1，提升 pg_3'}).click();
 await page.getByLabel('角色操作步骤').waitFor();
 assert.ok(submitted);
 await page.getByLabel('执行终端日志').getByText('STDOUT: t',{exact:false}).waitFor();
 await page.reload();
 await page.getByLabel('角色操作步骤').waitFor();
 await page.screenshot({path:'../output/fbasecman/role-change-progress.png',fullPage:true});
 assert.match(await page.getByLabel('执行终端日志').innerText(),/SELECT pg_is_in_recovery/);
 assert.deepEqual(errors,[]);
 console.log('按钮、确认文案、目标请求、实时步骤与SQL输出、刷新恢复通过；任务API使用模拟响应，未切换真实环境');
} finally { await browser.close(); }
