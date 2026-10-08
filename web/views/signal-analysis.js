/* Main application view composition. UI geometry belongs to WaveGuardUI. */
WaveGuardUI.mountShell({shell:document.querySelector('.workspace'),controls:document.querySelector('.control-stack'),main:document.querySelector('.monitor-stack'),intro:document.querySelector('.workstation-intro')});
function signalTimeAtPixel(pixelX, width, startUs, endUs) {
  const ratio=Math.max(0,Math.min(1,(pixelX-68)/Math.max(1,width-84)));
  return startUs+(endUs-startUs)*ratio;
}
(() => {
  const body = document.body;
  const monitor = document.querySelector('.monitor-stack');
  const intro = document.querySelector('.workstation-intro');
  const create = (tag, className, html = '') => {
    const node = document.createElement(tag); node.className = className;
    if (html) node.innerHTML = html;
    return node;
  };
  const analysisUI=WaveGuardUI.createStage({page:'file-analysis',kind:'signal',className:'signal-stage',inspectorTitle:'分析参数'});
  const measureUI=WaveGuardUI.createStage({page:'measure',kind:'signal',title:'时域波形',className:'signal-stage',inspectorTitle:'采集参数'});
  const analysisStage=analysisUI.stage,measureStage=measureUI.stage;
  const wavePanel = $('scopeCanvas').closest('.scope-panel');
  monitor.insertBefore(measureStage, wavePanel);
  measureUI.content.append(wavePanel);
  const metrics = document.querySelector('.metrics[data-pages="measure"]');
  wavePanel.insertBefore(metrics, wavePanel.querySelector('.canvas-wrap'));
  const actionStrip = create('div', 'signal-action-strip');
  actionStrip.append($('captureBtn'), $('stopBtn'));
  const origin = create('span','signal-data-origin','尚无采集结果');
  actionStrip.append(origin, create('span','signal-action-spacer'), $('saveNpz'), $('saveCsv'));
  measureUI.footer.append(actionStrip);
  const lensBar = document.querySelector('.analysis-lens-bar');
  monitor.insertBefore(analysisStage, lensBar);
  analysisUI.views.replaceChildren(lensBar);
  const context = create('div','signal-context-strip');
  context.hidden = true;
  const contextLabel = create('div','signal-context-label','<span>TIME WINDOW</span><strong></strong><span class="signal-context-units"></span>');
  const contextCanvas = create('canvas','signal-context-canvas');
  contextCanvas.setAttribute('aria-label','当前时间窗真实波形');
  context.append(contextLabel,contextCanvas);
  analysisUI.content.append(context);
  ['.analysis-workbench','.spectrum-panel','.time-frequency-panel','.experimental-result-panel','.analysis-inspector'].forEach(selector => {
    const node = monitor.querySelector(selector); if(node) (node.classList.contains('analysis-inspector')?analysisUI.footer:analysisUI.content).append(node);
  });
  const popover = $('analysisLensSettingsPopover');
  WaveGuardUI.bindInspector(analysisUI,{panel:popover,trigger:$('analysisLensSettingsButton'),title:'分析参数'});
  const filterPanel = create('section','signal-filter-controls');
  filterPanel.innerHTML='<div class="panel-heading"><h2>带通参数</h2></div>';
  const bandHeading = document.querySelector('.analysis-band-heading');
  filterPanel.append(bandHeading, bandHeading.nextElementSibling);
  const applyFilter=create('button','signal-apply-filter','应用带通参数');applyFilter.type='button';applyFilter.id='analysisApplyBandBtn';
  applyFilter.addEventListener('click',()=>{if(analysisSource)void refreshAnalysis();});
  filterPanel.append(applyFilter);
  popover.append(filterPanel);
  ['analysisBandLow','analysisBandHigh','analysisTransition'].forEach(id=>$(id).addEventListener('change',()=>{if(analysisSource)scheduleAnalysisRefresh();}));
  const focus=document.querySelector('.wg-focus-button');
  const sourcePanel=document.querySelector('.analysis-source-panel[data-pages="file-analysis"]');
  const fileButton=create('button','signal-file-summary','<span>↗</span><span><strong>打开数据文件</strong><small>NPZ / CSV · 选择文件或目录</small></span>');
  fileButton.type='button'; fileButton.setAttribute('aria-label','打开文件抽屉'); fileButton.setAttribute('aria-expanded','false');
  const fileDrawer=create('section','signal-file-drawer'); fileDrawer.hidden=true;
  fileDrawer.setAttribute('aria-label','文件浏览');
  const drawerHeading=create('div','signal-inspector-heading','<strong>文件浏览</strong>');
  const closeDrawer=create('button','','×'); closeDrawer.type='button'; closeDrawer.setAttribute('aria-label','关闭文件抽屉'); drawerHeading.append(closeDrawer); fileDrawer.append(drawerHeading);
  [...sourcePanel.children].filter(n=>!n.classList.contains('panel-heading')).forEach(n=>fileDrawer.append(n));
  sourcePanel.append(fileButton); monitor.append(fileDrawer);
  const setDrawer=(open)=>{fileDrawer.hidden=!open; fileButton.setAttribute('aria-expanded',String(open)); if(open) $('analysisPath').focus(); else fileButton.focus();};
  fileButton.addEventListener('click',()=>setDrawer(fileDrawer.hidden)); closeDrawer.addEventListener('click',()=>setDrawer(false));
  const candidates=$('modeCandidatesBody').closest('.result-table-wrap');
  const details=create('details','signal-candidates','<summary>候选回波与匹配结果</summary>'); candidates.replaceWith(details); details.append(candidates);
  document.querySelectorAll('.control-stack>.panel[data-pages="measure"]').forEach((panel,index)=>{if(index>0)measureUI.inspector.append(panel);});
  let lastFile='';
  let selection=null;
  let dragSelection=null;
  let timeCursor=null;
  const paint=()=>{
      if(body.dataset.page==='measure') drawScope();
      if(body.dataset.page==='file-analysis') {
        if(['raw','filtered','mix'].includes(analysisLens)) drawAnalysisWaveform();
        else if(analysisLens==='fft') drawSpectrum();
        else if(analysisLens==='experimental') drawExperimentalModes();
        else drawTimeFrequency();
        drawContext();
      }
  };
  const redraw=()=>WaveGuardUI.requestPaint();
  const pulse=(node,className)=>{
    if(matchMedia('(prefers-reduced-motion:reduce)').matches) return;
    node.classList.remove(className); void node.offsetWidth; node.classList.add(className);
  };
  function drawContext() {
    const valid=['fft','stft','cwt','wpd'].includes(analysisLens)&&analysisResult?.time_s?.length;
    context.hidden=!valid;
    if(!valid) return;
    const channel=['stft','cwt','wpd'].includes(analysisLens)?$('timeFrequencyChannel').value:analysisSelectedChannels[0];
    const keys=Object.keys(analysisResult.waveform);
    const key=keys.find(k=>k===`${channel}:raw`)||keys.find(k=>k.startsWith(`${channel}:`))||keys[0];
    if(!key) return;
    const values=analysisResult.waveform[key], times=analysisResult.time_s;
    contextLabel.querySelector('strong').textContent=`${key.replace(':',' · ')}  /  ${(times[0]*1e6).toFixed(2)} — ${(times.at(-1)*1e6).toFixed(2)} μs`;
    let peak=1e-12; for(const v of values) peak=Math.max(peak,Math.abs(v));
    contextLabel.querySelector('.signal-context-units').textContent=`±${peak.toPrecision(3)} V`;
    const {ctx,width,height}=canvasSetup(contextCanvas);
    ctx.clearRect(0,0,width,height); if(width<=0||height<=0) return;
    ctx.strokeStyle='#343638'; ctx.beginPath(); ctx.moveTo(72,height/2); ctx.lineTo(width-18,height/2); ctx.stroke();
    // Pixel envelopes retain true minima/maxima; source arrays stay untouched.
    ctx.strokeStyle=channelDisplayColor(key.split(':')[0]); ctx.lineWidth=1.1;
    const pixels=Math.max(1,Math.floor(width-90));
    ctx.beginPath();
    for(let x=0;x<pixels;x++) {
      const from=Math.floor(x*values.length/pixels), to=Math.min(values.length,Math.max(from+1,Math.floor((x+1)*values.length/pixels)));
      let low=Infinity,high=-Infinity; for(let i=from;i<to;i++){low=Math.min(low,values[i]);high=Math.max(high,values[i]);}
      if(!Number.isFinite(low))continue;
      ctx.moveTo(72+x,height/2-high/peak*height*.42);ctx.lineTo(72+x,height/2-low/peak*height*.42);
    } ctx.stroke();
    if(timeCursor!==null){const x=72+(timeCursor-times[0]*1e6)/Math.max((times.at(-1)-times[0])*1e6,1e-12)*(width-90);if(x>=72&&x<=width-18){ctx.strokeStyle=workstationDataColor('high');ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,height);ctx.stroke();}}
  }
  function syncLensPanels(){
    const selectors={'.analysis-workbench':['raw','filtered','mix'],'.spectrum-panel':['fft'],'.time-frequency-panel':['stft','cwt','wpd'],'.experimental-result-panel':['experimental']};
    Object.entries(selectors).forEach(([selector,lenses])=>{analysisUI.content.querySelector(selector).hidden=!lenses.includes(analysisLens);});
  }
  const layout=()=>{
    syncLensPanels();
    if(body.dataset.page!=='file-analysis'){fileDrawer.hidden=true;fileButton.setAttribute('aria-expanded','false');}
    if(!['measure','file-analysis'].includes(body.dataset.page)) { document.querySelector('.control-stack').inert=false; return; }
    focus.textContent=matchMedia('(max-width:760px)').matches?'采集 / 文件设置':body.classList.contains('signal-focus')?'退出专注':'专注';
    focus.setAttribute('aria-pressed',String(body.classList.contains('signal-focus')));
    document.querySelector('.control-stack').inert=body.classList.contains('signal-focus');
    filterPanel.hidden=!['filtered','mix','fft'].includes(analysisLens);
    redraw();
  };
  window.addEventListener('signal-layout',layout);
  window.addEventListener('signal-appearance',redraw);
  WaveGuardUI.observe(analysisStage);
  WaveGuardUI.observe(measureStage);
  // Dock transitions resize chart parents without resizing the outer stage.
  ['scopeCanvas','analysisWaveCanvas','spectrumCanvas','timeFrequencyCanvas','modeWaveCanvas'].forEach(id=>WaveGuardUI.observe($(id).parentElement));
  window.addEventListener('signal-lens-change',()=>requestAnimationFrame(()=>{
    syncLensPanels();
    filterPanel.hidden=!['filtered','mix','fft'].includes(analysisLens);
    drawContext(); pulse(analysisStage,'signal-lens-settle');
    const enabled=['filtered','mix','fft','stft','cwt','wpd'].includes(analysisLens);
    $('analysisLensSettings').style.setProperty('display',enabled?'flex':'none','important');
  }));
  window.addEventListener('signal-capture-ready',()=>{
    origin.textContent=latestResult?.summary?.simulated?'SIMULATED · 仿真采集':'PicoSDK · 实机采集';
    pulse(measureStage,'signal-result-settle');
  });
  window.addEventListener('signal-analysis-ready',()=>requestAnimationFrame(()=>{
    const metadata=analysisResult?.metadata;
    fileButton.querySelector('strong').textContent=analysisSource?.split(/[\\/]/).at(-1)||'打开数据文件';
    fileButton.querySelector('small').textContent=metadata?`${(metadata.sample_rate_hz/1e6).toFixed(2)} MS/s · ${metadata.channels.join(' / ')}`:'NPZ / CSV';
    if(lastFile!==analysisSource){selection=null;timeCursor=null;lastFile=analysisSource;fileDrawer.hidden=true;fileButton.setAttribute('aria-expanded','false');fileButton.focus();}
    drawContext(); redraw();
  }));
  const waveform=$('analysisWaveCanvas');
  const waveCursor=create('div','signal-crosshair');waveCursor.hidden=true;waveform.parentElement.append(waveCursor);
  const selectBand=create('div','signal-selection'); selectBand.hidden=true; waveform.parentElement.append(selectBand);
  const xToTime=(event)=>{
    const rect=waveform.getBoundingClientRect();
    const times=analysisResult.time_s;
    return signalTimeAtPixel(event.clientX-rect.left,rect.width,times[0]*1e6,times.at(-1)*1e6);
  };
  const linkSelectionWindow=()=>{
    const start=number('analysisTimeStart'),end=number('analysisTimeEnd');
    if(!analysisSource||!Number.isFinite(start)||!Number.isFinite(end)||end<=start)return;
    const first=$('spectrumWindows').firstElementChild;
    if(first){first.querySelector('.spec-start').value=start.toFixed(3);first.querySelector('.spec-end').value=end.toFixed(3);first.querySelector('.spec-label').value='选区';}
  };
  // Capture phase updates the existing FFT window before refreshAnalysis builds
  // its payload. Extra comparison windows are preserved.
  $('analysisApplyBtn').addEventListener('click',linkSelectionWindow,true);
  $('analysisResetViewBtn').addEventListener('click',linkSelectionWindow);
  waveform.addEventListener('dblclick',linkSelectionWindow);
  waveform.addEventListener('mousedown',event=>{
    if(event.button!==0||!event.shiftKey||!analysisResult)return;
    event.preventDefault(); event.stopImmediatePropagation();
    dragSelection={start:xToTime(event),end:xToTime(event)};
    selectBand.hidden=false;
  },true);
  waveform.addEventListener('mousemove',event=>{
    if(!analysisResult)return;
    timeCursor=xToTime(event);
    waveCursor.hidden=false;waveCursor.style.left=`${Math.max(68,Math.min(waveform.clientWidth-16,event.clientX-waveform.getBoundingClientRect().left))}px`;
    if(dragSelection){event.stopImmediatePropagation();dragSelection.end=timeCursor;const rect=waveform.getBoundingClientRect(),times=analysisResult.time_s,span=(times.at(-1)-times[0])*1e6;const left=68+(Math.min(dragSelection.start,dragSelection.end)-times[0]*1e6)/span*(rect.width-84);const width=Math.abs(dragSelection.end-dragSelection.start)/span*(rect.width-84);selectBand.style.left=`${left}px`;selectBand.style.width=`${width}px`;}
  },true);
  waveform.addEventListener('mouseleave',()=>waveCursor.hidden=true);
  window.addEventListener('mouseup',()=>{
    if(!dragSelection)return;
    selection=[Math.min(dragSelection.start,dragSelection.end),Math.max(dragSelection.start,dragSelection.end)];dragSelection=null;selectBand.hidden=true;
    if(selection[1]-selection[0]<.01)return;
    $('analysisTimeStart').value=selection[0].toFixed(3);$('analysisTimeEnd').value=selection[1].toFixed(3);syncAnalysisTimeRange('time');
    linkSelectionWindow();
    scheduleAnalysisRefresh();
  });
  waveform.setAttribute('aria-label','波形；Shift 拖动选择分析时间段');
  const help=document.querySelector('.analysis-workbench .scope-toolbar');
  help.title='Shift 拖动：选择分析时间段；滚轮：缩放；拖动：平移；双击：全时段';
  $('spectrumCanvas').addEventListener('mousemove',event=>{
    const windowData=analysisResult?.spectra?.[0];if(!windowData?.frequency_hz?.length)return;
    const rect=event.currentTarget.getBoundingClientRect();const ratio=Math.max(0,Math.min(1,(event.clientX-rect.left-72)/(rect.width-88)));
    const freqs=windowData.frequency_hz;const index=Math.round(ratio*(freqs.length-1));
    $('analysisCursorTime').textContent=`f = ${(freqs[index]/1000).toFixed(3)} kHz`;
    $('analysisCursorValue').textContent='时间窗保留于上方';
  });
  const tfCursor=create('div','signal-crosshair');tfCursor.hidden=true;$('timeFrequencyCanvas').parentElement.append(tfCursor);
  $('timeFrequencyCanvas').addEventListener('mouseleave',()=>{tfCursor.hidden=true;});
  $('timeFrequencyCanvas').addEventListener('mousemove',event=>{
    const result=timeFrequencyResult;if(!result?.time_us?.length)return;
    const rect=event.currentTarget.getBoundingClientRect();const rx=Math.max(0,Math.min(1,(event.clientX-rect.left-72)/(rect.width-90)));const ry=Math.max(0,Math.min(1,1-(event.clientY-rect.top-17)/(rect.height-51)));
    const col=Math.round(rx*(result.time_us.length-1)),row=Math.round(ry*(result.frequency_hz.length-1));
    timeCursor=result.time_us[col];$('analysisCursorTime').textContent=`t = ${timeCursor.toFixed(3)} μs`;
    tfCursor.hidden=false;tfCursor.style.left=`${72+rx*(rect.width-90)}px`;
    $('analysisCursorValue').textContent=`${(result.frequency_hz[row]/1000).toFixed(2)} kHz · ${result.values_db[row][col].toFixed(1)} dB`;
    drawContext();
  });
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){if(event.target.closest('dialog[open]'))return;if(!fileDrawer.hidden)setDrawer(false);body.classList.remove('signal-mobile-controls');}});
  // Bottom export actions remain available without opening FFT settings.
  const footer=analysisStage.querySelector('.analysis-inspector');
  const exportActions=create('div','signal-action-strip');
  exportActions.append(create('span','signal-data-origin','派生带通数据 · 原始文件保留'),create('span','signal-action-spacer'),$('exportFilteredNpzBtn'),$('exportFilteredCsvBtn'));
  footer.after(exportActions);
  WaveGuardUI.registerPainter('measure',paint);WaveGuardUI.registerPainter('file-analysis',paint);
  layout();
})();
