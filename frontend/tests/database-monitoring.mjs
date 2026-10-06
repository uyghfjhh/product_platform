import assert from 'node:assert/strict';
import { chromium } from 'playwright';
const base=process.env.PLATFORM_URL||'http://127.0.0.1:18769';
const browser=await chromium.launch();
try {
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 assert.equal((await page.request.get(base+'/api/v1/deployment-browser-fixture')).status(),200);
 const id='monitor-browser-'+Date.now();
 assert.equal((await page.request.post(base+'/api/v1/environments',{data:{id,product_id:'fbase-database',title:'监控验收环境',host:'127.0.0.1',port:7400,database_name:'postgres',database_user:'postgres'}})).status(),201);
 const sections={runtime:{valid:true,rows:[{recovery:false,wal_position:'0/100',replay_position:null,started_at:'2026-10-03'}]},senders:{valid:true,rows:[{application_name:'standby',state:'streaming',sync_state:'async',sent_lsn:'0/100',write_lsn:'0/F0',flush_lsn:'0/E0',replay_lsn:'0/D0'}]},receivers:{valid:true,rows:[]},slots:{valid:true,rows:[{slot_name:'peer',slot_type:'logical',active:true,retained_bytes:1048576}]},conflicts:{valid:true,rows:[{nspname:'public',relname:'orders',conflict_type:'insert_exists',conflict_resolution:'update_if_newer',local_tuple:{status:'paid'},remote_tuple:{status:'cancelled'},apply_tuple:{apply_tuple:'local'}}]},errors:{valid:false,error:'permission denied'},members:{valid:true,rows:[]},subscriptions:{valid:true,rows:[]},scope:{valid:true,rows:[]}};
 const stamp=new Date().toISOString(), latest={observed_at:stamp,replication_metrics:[{node_id:'rep8',pid:1,backend_start:'start',application_name:'standby',stage_bytes:{sent_to_write:16,write_to_flush:16,flush_to_replay:16},bytes_per_second:{sent_lsn:1024,write_lsn:512,flush_lsn:512,replay_lsn:256}}],nodes:[{node:{id:'rep8',label:'rep8',host:'127.0.0.1',port:7400},sections}]};
 sections.sessions={valid:true,rows:[{pid:11,backend_start:stamp,state:'idle in transaction',query:'UPDATE lock_evidence SET value=1'},{pid:22,backend_start:stamp,state:'active',query:'UPDATE lock_evidence SET value=2'}]};
 sections.blocking={valid:true,rows:[{blocking_pid:11,blocking_start:stamp,waiting_pid:22,waiting_start:stamp,waiting_query:'UPDATE lock_evidence SET value=2',blocking_query:'UPDATE lock_evidence SET value=1'}]};
 latest.database_metrics=[{node_id:'rep8',baseline:'start',metrics:{commit_per_second:2,rollback_per_second:0,buffer_hit_percent:90,active:2,idle:3,blocked:1,max_connections:100,instance_clients:5,database_clients:5,wal_mib_per_second:1}}];
 latest.nodes.push({node:{id:'rep9',label:'rep9',host:'127.0.0.1',port:7401},sections:{...sections,senders:{valid:true,rows:[]},slots:{valid:true,rows:[]},conflicts:{valid:true,rows:[]},errors:{valid:true,rows:[]},subscriptions:{valid:true,rows:[{sub_id:1,sub_name:'node1-to-node2'}]},subscription_runtime:{valid:true,rows:[{subid:1,relid:null,pid:22}]},origins:{valid:true,rows:[{external_id:'pg_1',remote_lsn:'0/F0'}]}}});
 latest.resolved_topology={members:[{node_id:'rep8',member_key:'g:db:1',member_name:'node1',recovery:false},{node_id:'rep9',member_key:'g:db:2',member_name:'node2',recovery:false}],links:[{id:'ab',kind:'mmr',source:'rep8',target:'rep9',status:'connected',reason:'fixture connection evidence',sub_id:1,sub_name:'node1-to-node2',slot_name:'peer',origin_name:'pg_1'}]};
 latest.alerts=[{key:'host-alert',category:'host',node_id:'server',slot_name:'CPU',status:'active',severity:'warning',reason:'host-pressure-fixture',hits:3,healthy:0,observed_at:stamp}];
 latest.member_consensus=[{key:'g:db:1',member_name:'node1',status:'disagree',observations:[{observer:'rep8',state:'ACTIVE'},{observer:'rep9',state:'PART_START'}]}];
 await page.route('**/api/v1/operations/monitoring-task-evidence',r=>r.fulfill({json:{id:'monitoring-task-evidence',environment_id:id,action:'deployment.switchover',target:'cluster',status:'SUCCEEDED',parameters:{},created_at:stamp,last_sequence:0}}));
 await page.route('**/api/v1/operations/monitoring-task-evidence/**',r=>r.fulfill({json:r.request().url().includes('/log')?{lines:[],available:false}:[]}));
 await page.route('**/api/v1/environments/'+id+'/monitoring*',r=>r.fulfill({json:{enabled:true,interval_seconds:15,events:[{kind:'deployment',subject:'monitoring-task-evidence',task_id:'monitoring-task-evidence',message:'deployment.switchover · 任务结束',observed_at:stamp,source:'平台任务记录'}],series:[{key:'slot:peer',label:'rep8 / peer · 聚合保留 WAL',unit:'MiB',points:[{time:new Date(Date.now()-60000).toISOString(),value:1,peak:9,valid_samples:4},{time:new Date().toISOString(),value:2,peak:8,valid_samples:4}]}],latest,history:[{...latest,observed_at:new Date(Date.now()-15000).toISOString()},latest]}}));
 let queryReads=0;
 latest.hosts=[{host:'127.0.0.1',valid:true,identity:'host-demo',observed_at:stamp,raw:{hostname:'monitor-lab',boot_id:'boot',cpu_count:4,uptime_seconds:7200,load_average:[.2,.1,.1],filesystems:[{device:'fs',paths:['/opt/data/rep8'],total_bytes:10737418240,available_bytes:5368709120}]},metrics:{cpu_percent:25,memory_percent:50,iowait_percent:1,devices:{sda:{read_mib_per_second:1,write_mib_per_second:2}}}}];
 await page.route('**/monitoring/nodes/*/diagnostics/*',r=>{
   if(r.request().url().endsWith('/queries')){queryReads++;return r.fulfill({json:queryReads===1?{available:false,reason:'未安装 pg_stat_statements',observed_at:stamp,sections:{}}:{available:true,observed_at:stamp,sections:{ranking:{valid:true,rows:[{queryid:'1',calls:3,total_exec_time:36,mean_exec_time:12,query:'SELECT diagnostic_demo'}]},reset:{valid:true,rows:[{stats_reset:stamp}]}}}});}
   return r.fulfill({json:{available:true,observed_at:stamp,sections:{tables:{valid:true,rows:[{relname:'maintenance_demo',n_dead_tup:3,n_live_tup:10}]},vacuum:{valid:true,rows:[]}}}});
 });
 const selectTab=async(name)=>{
   if(['物理流复制','MMR 有向链路','成员状态对照','WAL 保留趋势','复制范围'].includes(name))await page.getByRole('tab',{name:'复制监控',exact:true}).click();
   if(['会话与锁','冲突与行值','进程与恢复','查询排行','表维护'].includes(name))await page.getByRole('tab',{name:'诊断工作区',exact:true}).click();
   if(name==='长周期趋势')await page.getByRole('tab',{name:'负载趋势',exact:true}).click();
   await page.getByRole('tab',{name,exact:true}).click();
 };
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.clock.install();
 await page.goto(base+'/?page=monitoring&env='+id);
 await page.getByRole('heading',{name:'数据库监控',exact:true}).waitFor();
 await page.getByText('host-pressure-fixture',{exact:false}).waitFor();
 await page.getByLabel('告警类别',{exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('复制槽',{exact:true}).click();await page.getByText('没有符合筛选条件的告警',{exact:true}).waitFor();
 await page.getByLabel('告警类别',{exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('全部类别',{exact:true}).click();
 await page.getByRole('button',{name:'多活 · rep8 → rep9 · connected',exact:true}).click();
 const drawer=page.getByRole('dialog');await drawer.getByText('fixture connection evidence',{exact:true}).waitFor();await drawer.getByText('node1-to-node2',{exact:true}).waitFor();await drawer.locator('.ant-drawer-close').click();await drawer.waitFor({state:'hidden'});
 await page.getByLabel('实例详情范围',{exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('rep8',{exact:true}).click();await selectTab('负载趋势');await page.locator('svg[aria-label="rep8 · 提交事务速率，tx/s"]').waitFor();assert.equal(await page.locator('svg[aria-label="rep8 · 提交事务速率，tx/s"]').count(),1);
 await selectTab('会话与锁');await page.locator('.react-flow__node').filter({hasText:'PID 11'}).first().click();const sessionDrawer=page.getByRole('dialog');await sessionDrawer.getByText('UPDATE lock_evidence SET value=1',{exact:true}).first().waitFor();await sessionDrawer.locator('.ant-drawer-close').click();await sessionDrawer.waitFor({state:'hidden'});
 await selectTab('物理流复制');await page.getByText('standby · streaming · async',{exact:true}).waitFor();assert.equal(await page.locator('svg[aria-label="发送位置推进，MiB/s"]').count(),1);
 await selectTab('WAL 保留趋势');assert.equal(await page.locator('svg[aria-label="peer 保留 WAL，MiB"]').count(),1);
 await page.getByLabel('监控时间范围',{exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('24 小时',{exact:true}).click();await selectTab('长周期趋势');assert.equal(await page.locator('svg[aria-label="rep8 / peer · 聚合保留 WAL，MiB"]').count(),1);
 await selectTab('冲突与行值');await page.getByText('paid',{exact:true}).waitFor();await page.getByText('cancelled',{exact:true}).waitFor();
 await selectTab('进程与恢复');await page.getByRole('tab',{name:'进程错误与恢复',exact:true}).first().click();await page.getByText('permission denied',{exact:true}).waitFor();
 await selectTab('成员状态对照');await page.getByText('PART_START',{exact:true}).waitFor();
 await selectTab('观测事件');await page.getByRole('button',{name:'查看部署任务',exact:true}).click();const taskDrawer=page.getByRole('dialog');await taskDrawer.getByText('deployment.switchover',{exact:true}).waitFor();await taskDrawer.locator('.ant-drawer-close').click();
 await selectTab('主机资源');await page.getByText('25.00 %',{exact:true}).waitFor();
 await selectTab('查询排行');await page.getByRole('button',{name:/读取查询排行/}).click();await page.getByText('未安装 pg_stat_statements',{exact:true}).waitFor();await page.getByRole('button',{name:/读取查询排行/}).click();await page.getByText('SELECT diagnostic_demo',{exact:true}).first().waitFor();
 await selectTab('表维护');await page.getByRole('button',{name:/读取表维护/}).click();await page.getByText('maintenance_demo',{exact:true}).waitFor();
 await selectTab('实例概况');await page.setViewportSize({width:390,height:900});await page.waitForTimeout(200);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true,'Monitoring must fit narrow screens');
 await page.clock.fastForward(65000);await page.getByText('缓存已过期，请核查采集状态',{exact:false}).first().waitFor();assert.equal(await page.getByRole('button',{name:'多活 · rep8 → rep9 · connected',exact:true}).count(),0);
 assert.deepEqual(errors,[]);console.log('monitoring browser passed');
} finally {await browser.close();}
