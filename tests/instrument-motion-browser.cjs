const {chromium}=require(process.env.WAVEGUARD_PLAYWRIGHT_MODULE);
require('fs').mkdirSync('.test-tmp',{recursive:true});
(async()=>{
 const base=process.env.WAVEGUARD_BASE_URL||'http://127.0.0.1:4824';
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
 const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];
 page.on('pageerror',e=>{errors.push(e.message);console.log('PAGE ERROR: '+e.message);});
 await page.emulateMedia({reducedMotion:'no-preference'});
 await page.goto(base+'/measure');
 await page.locator('#simulate').locator('..').click();
 await page.locator('#sampleRate').fill('0.5');await page.locator('#captureDurationUs').fill('1000');
 await page.locator('#captureBtn').click();
 await page.waitForFunction(()=>latestResult?.time_s?.length===500);
 const before=await page.evaluate(()=>JSON.stringify(latestResult));
 const canvas=page.locator('#scopeCanvas'),box=await canvas.boundingBox();
 await page.mouse.move(box.x+box.width*.2,box.y+box.height/16);await page.waitForTimeout(230);
 const read=await page.evaluate(()=>({actual:document.getElementById('scopeCursorVoltage').dataset.value,expected:latestResult.channels[scopeCursorChannel][scopeCursorIndex].toFixed(6),channel:scopeCursorChannel,index:scopeCursorIndex}));
 if(read.actual!==read.expected)throw Error('cursor not actual data '+JSON.stringify(read));
 await page.mouse.wheel(0,-500);await page.waitForTimeout(100);
 const start=await page.evaluate(()=>scopeTimeStart);
 await page.mouse.down();await page.mouse.move(box.x+box.width*.4,box.y+box.height/16,{steps:8});await page.mouse.up();await page.waitForTimeout(250);
 if(start===await page.evaluate(()=>scopeTimeStart))throw Error('horizontal drag did not pan');
 await canvas.focus();const previous=await page.evaluate(()=>scopeCursorIndex);
 await page.keyboard.press('ArrowRight');await page.waitForTimeout(220);
 if(await page.evaluate(()=>scopeCursorIndex)!==previous+1)throw Error('keyboard snap failed');
 const stage=page.locator('[data-wg-stage="measure"]'),button=stage.locator('.wg-parameter-button');
 await button.hover();await page.mouse.down();await page.waitForTimeout(110);
 const compression=await button.evaluate(n=>({transform:getComputedStyle(n).transform,depth:n.dataset.wgPressed}));
 if(compression.depth!=='true'||compression.transform==='none')throw Error('no physical compression');
 await page.mouse.up();
 if(await button.getAttribute('aria-expanded')!=='true')throw Error('click blocked by release animation');
 await page.waitForSelector('.wg-fluid-shell');
 const cloneValue=await page.locator('.wg-fluid-shell select').first().inputValue();
 const actualValue=await stage.locator('.wg-inspector select').first().inputValue();
 if(cloneValue!==actualValue)throw Error('morph parameter mismatch');
 await page.waitForTimeout(190);await page.screenshot({path:'.test-tmp/instrument-motion-mid.png'});
 await page.waitForSelector('.wg-fluid-shell',{state:'detached'});
 await stage.getByRole('button',{name:'关闭采集参数',exact:true}).click();
 await page.waitForSelector('.wg-fluid-shell',{state:'detached'});
 await page.mouse.move(box.x+box.width*.25,box.y+box.height/16);await page.waitForTimeout(230);
 await page.screenshot({path:'.test-tmp/instrument-motion-light.png'});
 const styles=await stage.evaluate(n=>({border:getComputedStyle(n,'::before').animationName,aura:getComputedStyle(n.parentElement,'::after').animationName}));
 if(styles.border!=='wg-orbit'||styles.aura!=='wg-stage-aura')throw Error('stage motion missing '+JSON.stringify(styles));
 await page.evaluate(()=>WaveGuardUI.appearance({theme:'dark'}));await page.waitForTimeout(180);await page.screenshot({path:'.test-tmp/instrument-motion-dark.png'});
 const after=await page.evaluate(()=>JSON.stringify(latestResult));
 if(before!==after)throw Error('presentation changed captured arrays');
 await page.emulateMedia({reducedMotion:'reduce'});await button.click();
 if(await page.locator('.wg-fluid-shell').count())throw Error('reduced motion morph not skipped');
 await stage.getByRole('button',{name:'关闭采集参数',exact:true}).click();
 const layouts=[];
 for(const [width,height] of [[1366,768],[1440,900],[1600,900],[1920,1080],[2560,1440],[1024,768]]){
  await page.setViewportSize({width,height});
  for(const theme of ['light','dark']){
   await page.evaluate(t=>WaveGuardUI.appearance({theme:t}),theme);
   if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('overflow '+width);
   layouts.push([width,height,theme]);
  }
 }
 if(errors.length)throw Error(errors.join('\n'));
 console.log(JSON.stringify({cursor:read,compression,styles,arraysUnchanged:true,layouts:layouts.length,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
