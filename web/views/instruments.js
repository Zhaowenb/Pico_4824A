/* Whole application presentation: original nodes, original actions, original results. */
(() => {
  const body=document.body, monitor=document.querySelector('.monitor-stack');
  const routes=['sweep','sweep-analysis','lcr','lcr-linearity'];
  const create=(tag,cls,text='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};
  const allNodes=()=>[...new Set([...document.querySelectorAll('.page-only'),...(lcrModeRecords||[]).map(r=>r.node)])];
  const stages=new Map();
  const find=id=>document.getElementById(id)||(lcrModeRecords||[]).map(r=>r.node).flatMap(n=>[n,...n.querySelectorAll('[id]')]).find(n=>n.id===id);
  function move(node,destination){
    if(!node)return;
    const record=(lcrModeRecords||[]).find(r=>r.node===node);
    if(record){destination.append(record.placeholder);if(node.isConnected)destination.append(node);}
    else destination.append(node);
  }
  function modeNode(node,mode,destination){
    node.classList.add('page-only');node.dataset.pages='lcr';node.dataset.lcrMode=mode;
    if(lcrModeRecords){const placeholder=document.createComment('lcr-presentation-'+mode);destination.append(placeholder);lcrModeRecords.push({node,placeholder,mode});if(body.dataset.lcrMode===mode)destination.append(node);}
    else destination.append(node);
  }
  // Use a resize event for existing painters; folder canvases also retain their pixels.
  function paint(){const page=body.dataset.page;if(page==='sweep'){drawSweepResult();drawSweepRun();}if(page==='sweep-analysis'){drawArchiveHeatmap();drawArchiveRun();}if(page==='lcr'){drawLcrParameterCharts();drawLcrBode();drawLcrNyquist();drawLcrWave();drawBigLcrCharts();drawBigLcrWaves();drawLinearityMeasurementCharts();drawLinearityMeasurementWave();}if(body.dataset.page==='lcr-linearity'){if(latestLinearity)updateLinearityView(false);const mode=body.dataset.lcrAnalysisMode;if(mode==='big_lcr'||mode==='small_lcr'){renderLcrFolder(mode,false);const id=mode==='big_lcr'?'bigFolderWaveCanvas':'smallFolderWaveCanvas';if(folderDisplayWaves.has(id))drawFolderRawWave(id,folderDisplayWaves.get(id));}}}
  const schedulePaint=()=>WaveGuardUI.requestPaint();
  routes.forEach(page=>{
    const view=WaveGuardUI.createStage({page,title:({sweep:'扫描结果','sweep-analysis':'参数空间',lcr:'阻抗与响应','lcr-linearity':'测量记录'})[page],className:'application-stage'});
    view.content.classList.add('application-stage-content');view.footer.classList.add('application-action-strip');view.inspector.classList.add('application-inspector');view.button.classList.add('application-inspector-toggle');view.bar.classList.add('application-stage-bar');
    monitor.insertBefore(view.stage,document.querySelector('.event-panel'));stages.set(page,view);
  });
  allNodes().filter(n=>n.parentNode===monitor||((lcrModeRecords||[]).some(r=>r.node===n)&&n.classList.contains('scope-panel'))).forEach(node=>{
    const page=node.dataset.pages;if(!stages.has(page))return;
    if(node.classList.contains('scope-panel')||node.classList.contains('idle-stage'))move(node,stages.get(page).content);
  });
  // One mathematical view at a time. Each canvas and table keeps its original ID.
  const resultPanels=allNodes().filter(n=>n.classList.contains('scope-panel')&&routes.includes(n.dataset.pages));
  resultPanels.forEach(panel=>{
    if(['scanArchivePanel','sweepResultPanel'].includes(panel.id))return;
    const views=[];
    [...panel.children].forEach(node=>{
      if(node.matches('.lcr-chart-grid,.lcr-parameter-grid')){
        node.classList.add('application-plot-grid');[...node.children].forEach(child=>views.push({node:child,label:child.querySelector('.subhead span')?.textContent||child.querySelector('h3')?.textContent||'曲线'}));
      }else if(node.matches('.lcr-canvas-wrap,.lcr-wave-wrap,.result-table-wrap')){
        const previous=node.previousElementSibling;
        views.push({node,heading:previous?.classList.contains('subhead')?previous:null,label:node.matches('.result-table-wrap')?'数据明细':previous?.querySelector('span')?.textContent||'波形'});
      }
    });
    if(views.length<2)return;
    const picker=create('label','application-view-picker','观察视图'), select=create('select','');select.setAttribute('aria-label','结果观察视图');
    views.forEach((view,i)=>{const option=create('option','',view.label);option.value=String(i);select.append(option);view.node.classList.add('application-plot-view');});picker.append(select);(panel.querySelector('.scope-toolbar')||panel).append(picker);
    function choose(){views.forEach((view,i)=>{view.node.classList.toggle('application-view-hidden',i!==Number(select.value));if(view.heading)view.heading.classList.toggle('application-view-hidden',i!==Number(select.value));});[...panel.querySelectorAll('.application-plot-grid')].forEach(grid=>grid.classList.toggle('application-view-hidden',![...grid.children].some(n=>!n.classList.contains('application-view-hidden'))));schedulePaint();}
    select.addEventListener('change',choose);choose();
  });
  function relocateGroup(page,selector){const source=document.querySelector(selector);if(!source)return;const target=stages.get(page).inspector;[...source.querySelectorAll('.evaluation-grid')].forEach(n=>{const prev=n.previousElementSibling;if(prev?.classList.contains('subhead'))move(prev,target);move(n,target);});}
  relocateGroup('sweep','.sweep-control-panel');relocateGroup('sweep-analysis','.panel[data-pages="sweep-analysis"]:not(.analysis-source-panel)');
  const sweepActions=find('sweepBtn')?.closest('.sweep-action-row');move(sweepActions,stages.get('sweep').footer);['sweepProgressText'].forEach(id=>move(find(id),stages.get('sweep').footer));
  move(find('archiveReevaluateBtn'),stages.get('sweep-analysis').footer);
  const archive=stages.get('sweep-analysis');
  move(find('archiveConfigCards'),archive.inspector);
  // Archive overview versus original run detail, retaining all run selectors.
  const archivePanel=find('scanArchivePanel');
  if(archivePanel){const runCanvas=find('archiveRunCanvas')?.parentElement;const runTable=find('archiveRunsTableBody')?.closest('.result-table-wrap');const summaryTable=find('archiveTableBody')?.closest('.result-table-wrap');const map=archivePanel.querySelector('.archive-layout');const legend=find('archiveRunLegend');const switcher=create('div','application-local-tabs');const overview=create('button','','参数地图'),runs=create('button','','采集记录');overview.type=runs.type='button';switcher.append(overview,runs);archivePanel.insertBefore(switcher,map);const setView=detail=>{[map,summaryTable].filter(Boolean).forEach(n=>n.classList.toggle('application-view-hidden',detail));[runCanvas,runTable,legend].filter(Boolean).forEach(n=>n.classList.toggle('application-view-hidden',!detail));const sub=runCanvas?.previousElementSibling;if(sub?.classList.contains('subhead'))sub.classList.toggle('application-view-hidden',!detail);overview.setAttribute('aria-pressed',String(!detail));runs.setAttribute('aria-pressed',String(detail));schedulePaint();};WaveGuardUI.tabs({buttons:[overview,runs],onSelect:index=>setView(index===1)});}

  // Run picker forwards to the original delegated preview action, with no duplicate API.
  if(archivePanel){
    const picker=create('label','application-run-picker','采集记录'),select=create('select','');select.setAttribute('aria-label','选择归档采集记录');picker.append(select);
    archivePanel.querySelector('.scope-toolbar').append(picker);
    const bodyRows=find('archiveRunsTableBody');
    const refresh=()=>{const previous=select.value;select.replaceChildren(new Option('选择记录查看原始波形',''));[...bodyRows.querySelectorAll('tr')].forEach(row=>{const cells=row.querySelectorAll('td');select.append(new Option('#'+cells[0].textContent+' · '+cells[1].textContent+' kHz · '+cells[2].textContent+' 周期',row.dataset.archiveRun));});select.value=previous;};
    select.onchange=()=>{const button=bodyRows.querySelector('[data-archive-preview="'+select.value+'"]');if(button){archivePanel.querySelector('.application-local-tabs button:last-child').click();button.click();}};
    new MutationObserver(refresh).observe(bodyRows,{childList:true});refresh();
  }

  // Keep mode placeholders beside moved nodes: the hardware guard still owns mode changes.
  const lcr=stages.get('lcr');
  const controls=allNodes().filter(n=>n.dataset.pages==='lcr'&&n.classList.contains('panel')&&n.dataset.lcrMode);
  controls.forEach(node=>{
    if(node.matches('#bigLcrScanPanel,.linearity-acquisition-panel')||(node.dataset.lcrMode==='small'&&!node.classList.contains('hero-control')))move(node,lcr.inspector);
    const rows=[...node.querySelectorAll('.sweep-action-row,.lcr-action-row')];rows.forEach(row=>modeNode(row,node.dataset.lcrMode,lcr.footer));
  });
  ['bigLcrProgressText','linearityProgressText','lcrProgressText'].forEach(id=>{const text=find(id);if(!text)return;const mode=id==='lcrProgressText'?'small':'big';const wrap=create('div','application-progress');const bar=text.previousElementSibling;if(bar?.classList.contains('progress-track'))wrap.append(bar);wrap.append(text);if(id==='linearityProgressText')wrap.dataset.lcrProgressTask='linearity';else if(id==='bigLcrProgressText')wrap.dataset.lcrProgressTask='impedance';modeNode(wrap,mode,lcr.footer);});
  const big=find('bigLcrResultPanel'), linear=find('linearityMeasurementResult');
  if(big&&linear){const tabs=create('div','application-local-tabs'),imp=create('button','','阻抗测量'),lin=create('button','','线性度测试');imp.type=lin.type='button';tabs.append(imp,lin);lcr.stage.querySelector('.application-stage-bar').insertBefore(tabs,lcr.stage.querySelector('.application-inspector-toggle'));const switchTask=task=>{body.dataset.lcrTask=task;big.classList.toggle('application-view-hidden',task!=='impedance');linear.classList.toggle('application-view-hidden',task!=='linearity');controls.forEach(n=>{if(n.id==='bigLcrScanPanel'||n.classList.contains('big-lcr-safety-panel'))n.classList.toggle('application-view-hidden',task!=='impedance');if(n.classList.contains('linearity-acquisition-panel'))n.classList.toggle('application-view-hidden',task!=='linearity');});lcr.footer.querySelectorAll('[data-lcr-mode="big"]').forEach(row=>row.classList.toggle('application-view-hidden',(row.dataset.lcrProgressTask?row.dataset.lcrProgressTask==='linearity':Boolean(row.querySelector('#linearityStartBtn')))!== (task==='linearity')));imp.setAttribute('aria-pressed',String(task==='impedance'));lin.setAttribute('aria-pressed',String(task==='linearity'));schedulePaint();};WaveGuardUI.tabs({buttons:[imp,lin],onSelect:index=>switchTask(index===0?'impedance':'linearity')});}
  const small=find('lcrResultPanel');if(small)move(small.querySelector('.lcr-unit-controls'),lcr.inspector);
  // Keep file/source identity left; advanced reconstruction options live in Inspector.
  const folder=stages.get('lcr-linearity');document.querySelectorAll('.lcr-folder-source').forEach(source=>{const mode=source.dataset.lcrAnalysisMode;const advanced=create('section','application-folder-options');advanced.dataset.lcrAnalysisMode=mode;[...source.querySelectorAll('.form-grid')].forEach(n=>move(n,advanced));if(advanced.children.length)folder.inspector.append(advanced);[...source.querySelectorAll('button')].forEach(button=>{const wrapper=create('span','application-folder-action');wrapper.dataset.lcrAnalysisMode=mode;wrapper.append(button);folder.footer.append(wrapper);});});
  function sync(){const page=body.dataset.page;stages.forEach(({setOpen},key)=>{if(key!==page)setOpen(false);});if(!body.classList.contains('signal-focus'))document.querySelector('.control-stack').inert=false;schedulePaint();}
  window.addEventListener('signal-layout',sync);window.addEventListener('signal-appearance',schedulePaint);document.getElementById('lcrMeasurementMode').addEventListener('change',()=>setTimeout(schedulePaint,180));document.getElementById('lcrAnalysisMode').addEventListener('change',schedulePaint);
  routes.forEach(route=>WaveGuardUI.registerPainter(route,paint));stages.forEach(({content})=>WaveGuardUI.observe(content));WaveGuardUI.activate(body.dataset.page);sync();
})();
