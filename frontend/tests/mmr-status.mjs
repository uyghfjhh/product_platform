import assert from 'node:assert/strict';
import {chromium} from 'playwright';
const base=process.env.PLATFORM_URL || 'http://127.0.0.1:8080';
const env=process.env.MMR_ENVIRONMENT || 'env-605c7ca89e6f4cb2';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1600,height:1100}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try {
 await page.goto(`${base}/?page=deployment&env=${env}`);
 const summary=page.getByLabel('MMR 成员状态');
 await summary.getByRole('cell',{name:'node1',exact:true}).waitFor({timeout:30000});
 assert.equal(await summary.getByRole('cell',{name:'ACTIVE',exact:true}).count(),2);
 assert.match(await summary.innerText(),/MMR 双活.*my_test_group/);
 assert.ok(!(await summary.innerText()).includes('16432'));
 assert.ok(!(await summary.innerText()).includes('26432'));
 assert.equal(await page.locator('.topo-mmr-member').count(),0);
 assert.equal(await page.getByText('紫色：MMR 双向逻辑复制',{exact:false}).count(),0);
 const summaryBox=await summary.boundingBox();
 const canvasBox=await page.locator('.reference-deploy-canvas').boundingBox();
 assert.ok(summaryBox.y>=canvasBox.y+canvasBox.height-1,'MMR表格应在拓扑下方');
 assert.equal(await summary.locator('.ant-table').evaluate(e=>getComputedStyle(e).fontSize),'12px');
 const mmrPaths=page.locator('path.topo-edge-line.sync');
 assert.equal(await mmrPaths.count(),2,'双活必须两条独立的线');
 assert.equal(await mmrPaths.nth(0).getAttribute('data-source'),await mmrPaths.nth(1).getAttribute('data-target'));
 assert.equal(await mmrPaths.nth(1).getAttribute('data-source'),await mmrPaths.nth(0).getAttribute('data-target'));
 assert.notEqual(await mmrPaths.nth(0).getAttribute('d'),await mmrPaths.nth(1).getAttribute('d'));
 for(const path of await mmrPaths.all()) assert.equal(await path.getAttribute('marker-end'),'url(#topo-arrow-mmr)');
 for(const path of await page.locator('path.topo-edge-line.replication').all()) assert.equal(await path.getAttribute('marker-end'),'url(#topo-arrow-stream)');
 const linkBox=await page.locator('.topo-mmr-link-label').boundingBox();
 const nextHeader=await page.locator('.cluster-group-tag').nth(1).boundingBox();
 assert.ok(linkBox.y+linkBox.height<nextHeader.y,'MMR标签不得覆盖下一组标题');
 await page.screenshot({path:'../output/fbasecman/mmr-status-layout.png',fullPage:true});
 assert.ok(!(await summary.innerText()).includes('正常'));
 await page.locator('.topo-mmr-link-label').click();
 let drawer=page.getByRole('dialog').filter({hasText:'MMR 状态'}).last();
 await drawer.getByRole('columnheader',{name:'成员',exact:true}).waitFor();
 assert.ok(!(await drawer.innerText()).includes('ERR_SUB_RUN'));
 await page.screenshot({path:'../output/fbasecman/mmr-status-live.png',fullPage:true});
 const live=await (await page.request.get(`${base}/api/v1/environments/${env}/mmr-status`)).json();
 assert.equal(live.members.length,2);assert.equal(live.issue_count,0);
 assert.ok(live.observations.every(o=>o.evidence.filter(e=>e.sql.includes('show_node_info')).every(e=>e.instance===o.source.instance)));
 await page.keyboard.press('Escape');
 let fault=structuredClone(live);fault.issue_count=2;
 fault.members[0].issues=[{code:'ERR_SUB_RUN',detail:'subscriber is down',observer:fault.observations[0].source.instance},
  {code:'ERR_SLOT_STATE',detail:'slot is inactive',observer:fault.observations[1].source.instance}];
 await page.route(`**/api/v1/environments/${env}/mmr-status`,r=>r.fulfill({json:fault}));
 await page.getByRole('button',{name:/刷\s*新\s*状\s*态/}).click();
 await summary.getByText('订阅运行异常',{exact:false}).waitFor();
 await page.getByRole('button',{name:/MMR 状态.*2项异常/}).click();
 drawer=page.getByRole('dialog').filter({hasText:'MMR 状态'}).last();
 await drawer.locator('.ant-table-row-expand-icon').first().click();
 await drawer.getByText('ERR_SUB_RUN',{exact:true}).waitFor();
 await drawer.getByText('ERR_SLOT_STATE',{exact:true}).waitFor();
 await drawer.getByText('诊断 SQL 与原始结果',{exact:true}).click();
 await drawer.getByText(/SELECT CASE WHEN pg_is_in_recovery/).first().waitFor();
 await page.screenshot({path:'../output/fbasecman/mmr-status-issues.png',fullPage:true});
 await page.keyboard.press('Escape');
 await page.route(`**/api/v1/environments/${env}/mmr-status`,r=>r.fulfill({status:422,json:{detail:'检查超时'}}));
 await page.getByRole('button',{name:/刷\s*新\s*状\s*态/}).click();
 await summary.getByText('上次结果已过期 / 状态未知',{exact:true}).waitFor();
 // Role drift fixture: arrows follow the promoted physical instance, without touching servers.
 const topology=await (await page.request.get(`${base}/api/v1/environments/${env}/topology`)).json();
 const moved=structuredClone(live);
 moved.members.find(m=>m.name==='node1').source={instance:'pg_3',host:'127.0.0.1',port:16433};
 moved.observations.find(o=>o.physical_group==='pg_cluster_1').source={instance:'pg_3',host:'127.0.0.1',port:16433};
 for(const n of topology.nodes) if(n.group==='pg_cluster_1') n.role=n.id==='pg_3'?'primary':'standby';
 for(const e of topology.edges) {
   if(e.kind==='streaming' && e.source==='pg_1') {e.source='pg_3';e.target='pg_1';}
   if(e.kind==='mmr' && e.source==='pg_1') e.source='pg_3';
 }
 await page.route(`**/api/v1/environments/${env}/topology`,r=>r.fulfill({json:topology}));
 await page.route(`**/api/v1/environments/${env}/mmr-status`,r=>r.fulfill({json:moved}));
 await page.reload();
 await summary.getByRole('cell',{name:'pg_3',exact:true}).waitFor();
 await page.locator('path.replication[data-source="pg_3"][data-target="pg_1"]').waitFor({state:'attached'});
 assert.equal(await page.locator('path.sync[data-source="pg_3"]').count(),1);
 const colors=await page.evaluate(()=>({mmr:getComputedStyle(document.querySelector('path.sync')).stroke,
   stream:getComputedStyle(document.querySelector('path.replication')).stroke}));
 assert.notEqual(colors.mmr,colors.stream,'MMR和物理流复制必须不同颜色');
 assert.deepEqual(errors,[]);
 console.log('MMR表格不含端口、双活双线反向箭头、流复制单箭头、诊断与过期展示通过；未改动数据库');
} finally {await browser.close();}
