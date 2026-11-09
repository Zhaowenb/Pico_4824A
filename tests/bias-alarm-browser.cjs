const {chromium}=require(process.env.WAVEGUARD_PLAYWRIGHT_MODULE);
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.__tones=0;const C=window.AudioContext||window.webkitAudioContext;if(C){const original=C.prototype.createOscillator;C.prototype.createOscillator=function(){window.__tones++;return original.call(this);};}});
 const base=process.env.WAVEGUARD_URL||'http://127.0.0.1:4877';
 await page.goto(base+'/bias-scan');
 await page.route('**/api/bias-scan/preflight',r=>r.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Test only: no hardware'})}));
 await page.locator('#biasStartBtn').click();await page.waitForTimeout(200);
 await page.route('**/api/bias-scan/result',r=>r.fulfill({contentType:'application/json',body:JSON.stringify({status:'error',simulated:true,reason:'激励 B 原始波形疑似削顶；已停止扫描',output_state:'off',output_dir:'SIMULATED-ALARM-TEST',summary:[],runs:[],configuration:{},safety_alarm:{status:'AMPLIFIER_CLIP',target_a:1,awg_vpp:.8,raw_file:'异常波形.npz'}})}));
 await page.evaluate(()=>window.dispatchEvent(new CustomEvent('waveguard-status',{detail:{state:'error',message:'test',task_kind:'bias_scan',capture_id:9001,has_bias_result:true,progress:{output_state:'off'}}})));
 await page.locator('#biasAlarmModal').waitFor({state:'visible'});await page.waitForTimeout(400);
 if(!((await page.locator('#biasAlarmPower').textContent()).includes('已确认')))errors.push('off label');
 const sounds=await page.evaluate(()=>window.__tones);if(sounds<2)errors.push('audio not played');
 const animation=await page.locator('.bias-alarm-beacon').evaluate(n=>getComputedStyle(n).animationName);if(animation==='none')errors.push('warning animation missing');
 await page.screenshot({path:'.test-tmp/bias-alarm-light.png'});
 await page.locator('#biasAlarmSound').click();const count=await page.evaluate(()=>window.__tones);await page.waitForTimeout(1650);if(await page.evaluate(()=>window.__tones)!==count)errors.push('mute failed');
 await page.emulateMedia({reducedMotion:'reduce'});if(await page.locator('.bias-alarm-beacon').evaluate(n=>getComputedStyle(n).animationName)!=='none')errors.push('reduced motion');
 await page.evaluate(()=>WaveGuardUI.appearance({theme:'dark'}));await page.screenshot({path:'.test-tmp/bias-alarm-dark.png'});
 for(const [width,height] of [[1366,768],[1440,900],[1600,900],[1920,1080],[2560,1440],[1024,768]]){await page.setViewportSize({width,height});const fits=await page.locator('.bias-safety-alarm .trip-alarm-dialog').evaluate(n=>{const r=n.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight;});if(!fits)errors.push('alarm overflow '+width);}
 await page.locator('#biasAlarmClose').click();if(await page.locator('#biasAlarmModal').isVisible())errors.push('dismiss failed');
 fs.writeFileSync('.test-tmp/bias-alarm-results.json',JSON.stringify({sounds,animation,errors},null,2));console.log({sounds,animation,errors});await browser.close();process.exitCode=errors.length?1:0;
})();
