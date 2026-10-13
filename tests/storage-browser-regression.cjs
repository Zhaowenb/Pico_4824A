const {chromium}=require(process.env.WAVEGUARD_PLAYWRIGHT_MODULE);
const fs=require('fs');
const base=process.env.WAVEGUARD_BASE_URL||'http://127.0.0.1:4876';
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage(),errors=[],checks=[];
 page.on('pageerror',e=>errors.push(e.message));
 const api=async(path,data)=>{const response=data?await page.request.post(base+path,{data}):await page.request.get(base+path);if(!response.ok())throw Error(path+': '+await response.text());return response.json();};
 const original=await api('/api/storage/naming');
 try{
  for(const [width,height] of [[1366,768],[1440,900],[1600,900],[1920,1080],[2560,1440],[1024,768]]){
   await page.setViewportSize({width,height});await page.goto(base+'/bias-scan');
   for(const theme of ['light','dark']){
    await page.evaluate(t=>WaveGuardUI.appearance({theme:t}),theme);
    const alignment=await page.evaluate(()=>{const a=document.querySelector('#biasStartBtn').getBoundingClientRect(),b=document.querySelector('#biasStopBtn').getBoundingClientRect();return {topDelta:Math.abs(a.top-b.top),heightDelta:Math.abs(a.height-b.height),horizontal:a.right<=b.left,overflow:document.documentElement.scrollWidth>innerWidth};});
    checks.push({width,height,theme,...alignment});if(alignment.topDelta>0.5||alignment.heightDelta>0.5||!alignment.horizontal||alignment.overflow)errors.push('alignment '+JSON.stringify(checks.at(-1)));
   }
  }
  await page.setViewportSize({width:1440,height:900});await page.goto(base+'/measure');
  await page.locator('#workstationSettingsButton').click();await page.locator('.wg-storage-settings summary').click();
  await page.locator('[data-storage-name=capture]').fill('试件01_直达波');await page.locator('[data-save-names]').click();
  await page.waitForFunction(()=>document.querySelector('[data-name-status]').textContent.includes('已保存'));
  await page.screenshot({path:'.test-tmp/storage-settings.png'});await page.keyboard.press('Escape');
  const config=await api('/api/config');config.sample_rate_hz=500000;config.pre_trigger_samples=50;config.post_trigger_samples=950;
  await api('/api/capture',{...config,simulate:true});
  for(let i=0;i<100;i++){const s=await api('/api/status');if(!['running','paused'].includes(s.state))break;await page.waitForTimeout(50);}
  const npz=await api('/api/save',{format:'npz'}),csv=await api('/api/save',{format:'csv'});
  if(!npz.path.includes('试件01_直达波')||npz.path.slice(0,-4)!==csv.path.slice(0,-4))errors.push('capture naming/shared directory');
  await page.goto(base+'/bias-scan');await page.locator('#biasScanName').fill('试件01_偏置验证');await page.locator('#biasEnd').fill('0.5');await page.locator('#biasRepeats').selectOption('5');
  await page.locator('#biasStartBtn').click();await page.waitForFunction(()=>document.querySelector('#biasTaskMessage').textContent.includes('扫描完成'),{},{timeout:30000});
  const result=await api('/api/bias-scan/result');checks.push({scan:result.output_dir,runs:result.runs.length});
  if(!result.output_dir.includes('试件01_偏置验证')||result.runs.length!==10||!result.runs.every(r=>r.file.includes('电流')&&r.file.includes('重复')))errors.push('bias hierarchy');
  await page.locator('#biasPointSelect').selectOption('1');await page.waitForFunction(()=>document.querySelector('#biasPreviewMeta').textContent.includes('0.5 A'));
  await page.screenshot({path:'.test-tmp/storage-bias-loaded.png'});
  const downloadPromise=page.waitForEvent('download');await page.locator('#biasExportBtn').click();const download=await downloadPromise;
  if(!download.suggestedFilename().includes('试件01_偏置验证')||!download.suggestedFilename().endsWith('.zip'))errors.push('ZIP download name');
  await download.saveAs('.test-tmp/storage-browser-export.zip');
  await page.locator('#biasFolderBtn').click();await page.waitForFunction(()=>document.querySelector('.wg-file-location input').value.includes('试件01_偏置验证'));
  await page.waitForFunction(()=>document.querySelector('.wg-file-list').textContent.includes('电流0.500A'));
  await page.keyboard.press('Escape');
  await page.goto(base+'/interference');await page.locator('#workstationSettingsButton').click();await page.locator('.wg-storage-settings summary').click();if(!await page.locator('[data-storage-name=interference]').isVisible())errors.push('shared interference settings');
 }catch(e){errors.push(e.stack);}finally{await api('/api/storage/naming',original);await browser.close();}
 fs.writeFileSync('.test-tmp/storage-browser-results.json',JSON.stringify({checks,errors},null,2));console.log(JSON.stringify({checks:checks.length,errors},null,2));process.exitCode=errors.length?1:0;
})();
