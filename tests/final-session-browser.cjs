const {chromium}=require(process.env.WAVEGUARD_PLAYWRIGHT_MODULE),assert=require('assert/strict'),fs=require('fs');
const base=process.env.WAVEGUARD_BASE_URL||'http://127.0.0.1:4877';
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];
 page.on('pageerror',e=>errors.push(e.stack));page.on('console',m=>{if(['error','warning'].includes(m.type())&&!m.text().includes('404'))errors.push(m.text());});
 await page.goto(base+'/file-analysis');
 await page.evaluate(async()=>{document.querySelector('#analysisPath').value='F:/Project/01guided_waves/software/Pico_4824A_btf/data/A1.npz';await browseAnalysisPath();setAnalysisLens('experimental');await calculateExperimentalModes();});
 assert(await page.evaluate(()=>experimentalState.status==='ready'&&experimentalModeResult!==null));
 for(const theme of ['light','dark']){await page.evaluate(theme=>WaveGuardUI.appearance({theme}),theme);await page.waitForTimeout(400);await page.screenshot({path:'.test-tmp/final-ex-'+theme+'.png'});}
 await page.evaluate(()=>{document.querySelector('#modeMaxShift').value='3';document.querySelector('#modeMaxShift').dispatchEvent(new Event('change',{bubbles:true}));});
 assert.equal(await page.evaluate(()=>document.body.dataset.analysisState),'pending');
 await page.evaluate(()=>calculateExperimentalModes());assert.equal(await page.evaluate(()=>experimentalState.status),'ready');
 const api=async(path,body)=>{const response=body?await page.request.post(base+path,{data:body}):await page.request.get(base+path);assert(response.ok(),await response.text());return response.json();};
 const cfg=await api('/api/config');cfg.sample_rate_hz=500000;cfg.pre_trigger_samples=50;cfg.post_trigger_samples=950;
 await api('/api/capture',{...cfg,simulate:true});
 for(let i=0;i<100;i++){if((await api('/api/status')).state==='complete')break;await page.waitForTimeout(50);}
 const saved=await api('/api/save',{format:'npz'}),folder=saved.path.replace(/[\\/][^\\/]+$/,'');
 await page.locator('#workstationSettingsButton').click();await page.locator('.wg-storage-archive summary').click();
 await page.locator('[data-pack-path]').fill(folder);
 const pending=page.waitForEvent('download');await page.locator('[data-pack-download]').click();const download=await pending;
 assert(download.suggestedFilename().endsWith('.zip'));await download.saveAs('.test-tmp/final-session.zip');
 await page.waitForFunction(()=>document.querySelector('[data-pack-status]').textContent==='完整任务已导出');
 const readonly=await page.request.get(base+'/api/storage/archive?directory='+encodeURIComponent('F:/Project/01guided_waves/software/Pico_4824A/data'));
 assert.equal(readonly.status(),400);
 assert.equal(await page.evaluate(()=>[...document.querySelectorAll('svg path')].some(n=>/NaN|undefined/.test(n.getAttribute('d')||''))),false);
 assert.deepEqual(errors,[]);console.log(JSON.stringify({exSnapshot:true,zip:download.suggestedFilename(),referenceExportRejected:true,errors},null,2));
 fs.writeFileSync('.test-tmp/final-session-browser.json',JSON.stringify({exSnapshot:true,zip:download.suggestedFilename(),referenceExportRejected:true,errors},null,2));await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
