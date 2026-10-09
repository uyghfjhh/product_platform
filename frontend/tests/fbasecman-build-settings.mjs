import assert from 'node:assert/strict';
import { chromium } from 'playwright';
const browser = await chromium.launch({headless:true});
const page = await browser.newPage({viewport:{width:1500,height:1100}});
const errors=[];
page.on('pageerror', error=>errors.push(error.message));
let saved;
const initial={builds:[{id:'legacy',name:'默认版本',fbasecman_bin:'/baseline/fbasecman',license_dir:'/license'}],active_build_id:'legacy',build_name:'默认版本',version:'test build',source:'网页测试'};
await page.route('**/api/v1/environments/*/fbasecman-test-settings', async route=>{
 if(route.request().method()==='PUT') saved=route.request().postDataJSON();
 const data=saved ? {...initial,...saved,build_name:saved.builds.find(b=>b.id===saved.active_build_id).name} : initial;
 await route.fulfill({json:data});
});
try {
 await page.goto('http://127.0.0.1:8080/?page=tests:fbasecman:cman&env=cman-mmr');
 await page.getByRole('button',{name:'配置被测 fbasecman'}).click();
 const dialog=page.getByRole('dialog');
 await dialog.getByRole('button',{name:'新增版本'}).click();
 await dialog.getByLabel('版本名称').nth(1).fill('Hint 修改版');
 await dialog.getByLabel('fbasecman 可执行文件绝对路径').nth(1).fill('/hint/fbasecman');
 await dialog.getByLabel('fbasecman License 目录绝对路径').nth(1).fill('/license');
 await dialog.getByRole('radio',{name:'当前生效'}).nth(1).check();
 await dialog.getByRole('button',{name:'检查当前版本并保存'}).click();
 await dialog.waitFor({state:'hidden'});
 assert.equal(saved.builds.length,2);
 assert.equal(saved.active_build_id,saved.builds[1].id);
 await page.getByRole('button',{name:'配置被测 fbasecman'}).click();
 assert.ok(await dialog.getByRole('radio').nth(1).isChecked());
 await dialog.getByRole('button',{name:'删除版本'}).nth(1).click();
 assert.ok(await dialog.getByRole('radio').first().isChecked());
 assert.ok(await dialog.getByRole('button',{name:'删除版本'}).first().isDisabled());
 assert.deepEqual(errors,[]);
 console.log('多版本新增、保存、重新打开、删除当前项并切换通过（模拟设置 API，不修改真实配置）');
} finally {await browser.close();}
