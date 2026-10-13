const {chromium}=require(process.env.WAVEGUARD_PLAYWRIGHT_MODULE);
const base=process.env.WAVEGUARD_URL||'http://127.0.0.1:4878';
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  let state={phase:'current',current:'11111111abcdef',running:'11111111abcdef',latest:'11111111abcdef',available:false,auto_check:false,message:'已是最新版本',blocked:null};
  let applies=0,checks=0,appliedAt=0;
  await page.route('**/api/software-update**',async route=>{
   const r=route.request();
   if(r.url().endsWith('/check')){checks++;state={...state,phase:'available',latest:'22222222abcdef',available:true,message:'发现新版本'};}
   if(r.url().endsWith('/preferences'))state.auto_check=r.postDataJSON().auto_check;
   if(r.url().endsWith('/apply')){applies++;appliedAt=Date.now();state={...state,phase:'installing',message:'正在安全关闭设备并更新代码'};}
   if(appliedAt&&Date.now()-appliedAt>500)state={...state,phase:'current',current:'22222222abcdef',running:'22222222abcdef',available:false,message:'已是最新版本'};
   if(r.method()==='POST'&&r.headers()['x-waveguard-update']!=='1')errors.push('missing explicit update header');
   await route.fulfill({status:r.method()==='POST'?202:200,contentType:'application/json',body:JSON.stringify(state)});
  });
  await page.goto(base+'/measure');
  await page.locator('#workstationSettingsButton').click();
  await page.locator('#wgUpdateCheck').click();
  await page.waitForFunction(()=>!document.getElementById('wgUpdateApply').disabled);
  await page.locator('#wgUpdateApply').click();
  await page.waitForFunction(()=>document.getElementById('wgUpdateCurrent')?.textContent==='22222222',{timeout:10000});
  if(applies!==1)errors.push('apply called more than once');
  const pages=['measure','sweep','lcr','lcr-linearity','file-analysis','sweep-analysis','bias-scan','magnetic-diagnosis','interference','interference/manual'];
  for(const name of pages){
   await page.goto(base+'/'+name);
   await page.locator('#workstationSettingsButton').click();
   await page.locator('#wgUpdateCurrent').waitFor({state:'visible'});
   if(await page.locator('.wg-software-update').count()!==1)errors.push('duplicated or missing update settings '+name);
   await page.keyboard.press('Escape');
   if(await page.locator('#workstationAppearanceSettings').isVisible())errors.push('settings did not close '+name);
  }
  await page.goto(base+'/measure');
  await page.locator('#workstationSettingsButton').click();
  for(const [width,height] of [[1366,768],[1440,900],[1600,900],[1920,1080],[2560,1440],[1024,768]]){
   await page.setViewportSize({width,height});
   for(const theme of ['light','dark']){
    await page.evaluate(t=>WaveGuardUI.appearance({theme:t}),theme);
    await page.locator('.wg-software-update').scrollIntoViewIfNeeded();
    const overflow=await page.evaluate(()=>{const r=document.getElementById('workstationAppearanceSettings').getBoundingClientRect();return r.right>innerWidth+1||r.bottom>innerHeight+1||document.documentElement.scrollWidth>innerWidth;});
    if(overflow)errors.push('settings overflow '+width+' '+theme);
   }
  }
  await page.screenshot({path:'.test-tmp/software-update-settings.png'});
  state={...state,auto_check:true};appliedAt=0;
  const automatic=page.waitForResponse(r=>r.url().endsWith('/api/software-update/check'));
  await page.goto(base+'/measure');
  await automatic;
  if(applies!==1)errors.push('automatic check installed software');
  console.log(JSON.stringify({pages:pages.length,layouts:12,checks,applies,errors}));
  process.exitCode=errors.length?1:0;
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
