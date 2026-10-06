import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
const base = process.env.PLATFORM_URL || 'http://127.0.0.1:18769';
if (!process.env.DEPLOYMENT_FIXTURE) throw new Error('DEPLOYMENT_FIXTURE is required');
const fixture = JSON.parse(await readFile(process.env.DEPLOYMENT_FIXTURE, 'utf8'));
const browser = await chromium.launch({ headless: true });
try {
 const page = await browser.newPage();
 const handshake = await page.request.get(`${base}/api/v1/deployment-browser-fixture`);
 assert.equal(handshake.status(), 200); assert.equal((await handshake.json()).nonce, fixture.nonce);
 const dirs = [fixture.data_root+'/existing-primary',fixture.data_root+'/existing-standby'];
 for (const path of dirs) { await mkdir(path,{recursive:true}); await writeFile(path+'/PG_VERSION','15'); }
 const source = `hosts:\n  local:\n    address: 127.0.0.1\npostgresql_installations:\n  existing:\n    provider: postgres\n    home: ${fixture.home}\ninstances:\n  primary:\n    host: local\n    installation: existing\n    port: 7100\n    data_dir: ${dirs[0]}\n  standby:\n    host: local\n    installation: existing\n    port: 7101\n    data_dir: ${dirs[1]}\nstreaming_clusters:\n  discovered:\n    primary: primary\n    standbys:\n      - instance: standby\n`;
 await page.route('**/api/v1/deployment/discover-existing',async route=>{
  assert.deepEqual(route.request().postDataJSON().data_dirs,dirs);
  await route.fulfill({json:{source_yaml:source,targets:[{value:'streaming.discovered',label:'主备 · primary'}],warnings:[]}});
 });
 await page.goto(base);
 await page.getByRole('button',{name:'新建部署方案',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await dialog.getByLabel('方案名称',{exact:true}).fill('自动接管验收');
 await dialog.getByText('接管已有实例',{exact:true}).click();
 await dialog.getByRole('button',{name:/下一步$/}).click();
 await dialog.getByLabel('已有实例数据目录',{exact:true}).fill(dirs.join('\n'));
 await dialog.getByRole('button',{name:'探测已有实例与复制关系',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('textarea[id$="source_yaml"]')?.value.includes('streaming_clusters'));
 await dialog.getByRole('button',{name:/下一步$/}).click();
 await dialog.getByText('primary',{exact:true}).waitFor();
 await dialog.getByRole('button',{name:'检查并生成部署计划',exact:true}).click();
 await dialog.getByText('检查通过，可关联方案或提交执行',{exact:true}).waitFor();
 await dialog.getByRole('button',{name:'接管并检查健康',exact:true}).waitFor();
 assert.equal(await dialog.getByRole('button',{name:'按计划部署并验收',exact:true}).count(),0);
 const drafts=await (await page.request.get(`${base}/api/v1/deployment/drafts`)).json();
 const draft=drafts.find(item=>item.spec.title==='自动接管验收');
 assert.equal(draft.spec.mode,'adopt');assert.equal(draft.spec.nodes.length,2);
 console.log('Adoption browser check passed: inventory, topology and checked health-only plan.');
} finally { await browser.close(); }
