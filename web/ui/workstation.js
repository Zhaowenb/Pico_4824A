/* WaveGuard UI foundation. No API calls, acquisition timers or data transforms. */
(() => {
  'use strict';
  const routes = [
    ['measure','实时测量'], ['sweep','参数扫描'], ['lcr','LCR测量'],
    ['lcr-linearity','LCR数据分析'], ['file-analysis','单数据分析'],
    ['sweep-analysis','参数数据分析'], ['interference','干扰实验']
  ];
  const channelColors = Object.freeze(['#2e73e8','#e34b4b','#39ae50','#e5ce29','#a060cb','#a3a3a3','#56b5df','#d44bb7']);
  const stages = new Map(), panels = new Set(), painters = new Map();
  let paintFrame = 0;
  const read = (key,fallback) => {try{return localStorage.getItem(key)||fallback;}catch{return fallback;}};
  const write = (key,value) => {try{localStorage.setItem(key,value);}catch{}};
  const state = {theme:read('waveguard-ui-theme','light'),accent:read('waveguard-workstation-accent','#2f73ff'),route:null,focus:false};
  const el = (tag,className='',text='') => {const n=document.createElement(tag);n.className=className;n.textContent=text;return n;};
  const emit = name => window.dispatchEvent(new Event(name));
  function requestPaint() {
    cancelAnimationFrame(paintFrame);
    paintFrame=requestAnimationFrame(()=>{adoptReadouts();painters.forEach((paint,key)=>{if(key===state.route||key==='*')paint();});});
  }
  const observer = new ResizeObserver(requestPaint);
  function observe(node,paint,key='*') {if(paint)painters.set(key,paint);if(node)observer.observe(node);}
  function palette() {
    const rgb=[1,3,5].map(i=>parseInt(state.accent.slice(i,i+2),16));
    const mix=(a,b,t)=>a.map((v,i)=>Math.round(v+(b[i]-v)*t));
    const floor=[12,13,15],white=[242,242,243],vivid=mix(rgb,white,.1),high=mix(rgb,white,.4);
    return {accent:state.accent,high:`rgb(${high.join(',')})`,stops:[floor,mix(floor,vivid,.18),mix(floor,vivid,.48),vivid,high,mix(rgb,white,.86)]};
  }
  function appearance({theme=state.theme,accent=state.accent}={},persist=false) {
    state.theme=theme==='dark'?'dark':'light';
    if(/^#[0-9a-f]{6}$/i.test(accent))state.accent=accent.toLowerCase();
    const body=document.body,p=palette();
    body.dataset.wgTheme=state.theme;
    // Compatibility attributes carry state only; they no longer own styling.
    body.dataset.analysisTheme=state.theme;body.dataset.uiTheme=state.theme;
    ['--wg-signal','--signal','--lab-signal'].forEach(name=>body.style.setProperty(name,state.accent));
    ['--wg-signal-hi','--signal-hi'].forEach(name=>body.style.setProperty(name,p.high));
    const c=[1,3,5].map(i=>parseInt(state.accent.slice(i,i+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
    body.style.setProperty('--wg-on-accent',c.reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0)>.26?'#111820':'#fff');
    p.stops.forEach((rgb,i)=>body.style.setProperty(`--wg-heat-${i}`,`rgb(${rgb.join(',')})`));
    document.querySelectorAll('[data-analysis-theme-choice]').forEach(n=>n.setAttribute('aria-pressed',String(n.dataset.analysisThemeChoice===state.theme)));
    const color=document.getElementById('workstationAccentColor'),preset=document.getElementById('workstationAccentPreset');
    if(color)color.value=state.accent;
    if(preset)preset.value=[...preset.options].some(o=>o.value===state.accent)?state.accent:'custom';
    if(persist){write('waveguard-ui-theme',state.theme);write('waveguard-analysis-theme',state.theme);write('waveguard-workstation-accent',state.accent);}
    emit('signal-appearance');requestPaint();
  }
  function panelController({panel,trigger,host,onChange=()=>{},exclusive=true}) {
    const controller={panel,trigger,isOpen:()=>!panel.hidden,set(open,{focus=false}={}){
      const changed=panel.hidden===Boolean(open);
      if(open&&exclusive)panels.forEach(p=>{if(p!==controller)p.set(false);});
      panel.hidden=!open;trigger?.setAttribute('aria-expanded',String(open));
      host?.classList.toggle('wg-inspector-open',Boolean(open));
      if(changed){onChange(Boolean(open));requestPaint();}
      if(focus){if(open)panel.querySelector('button,input,select,textarea')?.focus();else trigger?.focus();}
    }};
    panels.add(controller);
    return controller;
  }
  function inspector({host,trigger,panel,title='任务参数',onChange}) {
    panel.classList.add('wg-inspector');panel.setAttribute('role','region');panel.setAttribute('aria-label',title);
    const heading=el('div','wg-inspector-heading'),label=el('strong','',title),close=el('button','wg-icon-button','×');
    close.type='button';close.setAttribute('aria-label','关闭'+title);heading.append(label,close);panel.prepend(heading);
    const control=panelController({panel,trigger,host,onChange});trigger.type='button';trigger.classList.add('wg-parameter-button');trigger.setAttribute('aria-expanded','false');
    trigger.addEventListener('click',()=>control.set(!control.isOpen()));close.addEventListener('click',()=>control.set(false,{focus:true}));control.set(false);
    return control;
  }
  function createStage({page,title='',kind='plot',className='',inspectorTitle='任务参数'}={}) {
    const stage=el('section','wg-stage '+className),bar=el('div','wg-stage-bar'),views=el('div','wg-view-controls'),content=el('div','wg-stage-main'),footer=el('footer','wg-actions'),panel=el('aside','');
    stage.dataset.wgStage=page;stage.dataset.pages=page;stage.dataset.kind=kind;stage.setAttribute('aria-label',title||routes.find(r=>r[0]===page)?.[1]||'数据工作区');
    stage.hidden=page!==state.route;
    const caption=el('span','wg-stage-caption',title),button=el('button','wg-parameter-button','参数设置');views.append(caption);bar.append(views,button);stage.append(bar,content,footer,panel);
    const api={stage,bar,views,content,footer,inspector:panel,button};
    const control=inspector({host:stage,trigger:button,panel,title:inspectorTitle});api.control=control;api.setInspector=open=>control.set(open);api.setOpen=api.setInspector;
    stages.set(page,api);observe(content);return api;
  }
  function bindInspector(stage,{trigger,panel,title}) {
    const old=stage.control;old.panel.remove();old.trigger.remove();panels.delete(old);
    stage.stage.append(panel);stage.inspector=panel;stage.button=trigger;
    stage.control=inspector({host:stage.stage,trigger,panel,title});stage.setInspector=open=>stage.control.set(open);stage.setOpen=stage.setInspector;
    return stage.control;
  }
  function tabs({buttons,onSelect,selected=0}) {
    const select=index=>{buttons.forEach((button,i)=>{button.setAttribute('aria-pressed',String(i===index));button.setAttribute('aria-selected',String(i===index));});onSelect(index);requestPaint();};
    buttons.forEach((button,index)=>{button.type='button';button.addEventListener('click',()=>select(index));});select(selected);return {select};
  }
  function markSelection(buttons,predicate) {
    [...buttons].forEach(button=>{const selected=Boolean(predicate(button));button.setAttribute('aria-selected',String(selected));button.setAttribute('aria-pressed',String(selected));});
  }
  function adoptReadouts() {
    document.querySelectorAll(".wg-status-bar strong,.wg-status-bar #status").forEach(node=>node.title=node.textContent);
    document.querySelectorAll('.wg-stage .metrics,.wg-stage .lcr-metrics,.wg-stage .big-lcr-status-grid,.wg-stage .evaluation-summary-cards,.wg-stage .analysis-inspector').forEach(group=>{
      group.classList.add('wg-readouts');[...group.children].forEach(item=>item.classList.add('wg-readout'));
    });
  }
  function setFocus(enabled) {
    state.focus=enabled;document.body.classList.toggle('wg-focus',enabled);document.body.classList.toggle('signal-focus',enabled);
    document.querySelector('.wg-controls').inert=enabled;
    document.querySelectorAll('.wg-focus-button').forEach(n=>{n.textContent=enabled?'退出专注':'专注';n.setAttribute('aria-pressed',String(enabled));});
    requestPaint();emit('signal-layout');
  }
  function mountShell({shell,controls,main,intro,documentMode=false}) {
    document.body.classList.add(documentMode?'wg-document':'wg-workstation');
    shell.classList.add('wg-shell');controls.classList.add('wg-controls');main.classList.add('wg-main');intro?.classList.add('wg-intro');
    if(intro&&!documentMode){
      const focus=el('button','wg-focus-button signal-focus-button','专注');focus.type='button';focus.setAttribute('aria-pressed','false');intro.append(focus);
      focus.onclick=()=>{if(matchMedia('(max-width:760px)').matches){const open=document.body.classList.toggle('wg-controls-open');focus.textContent=open?'关闭设置':'工作设置';requestPaint();}else setFocus(!state.focus);};
    }
    if(!documentMode){
      let status=main.querySelector('.event-panel');
      if(!status){status=el('div','event-panel');const label=el('span','event-label','实验状态');status.append(label,document.getElementById('status')||el('span','','等待配置'));main.append(status);}
      status.classList.add('wg-status-bar');
    }
    observe(main);return {shell,controls,main,intro};
  }
  function activate(route) {
    if(state.route!==route){state.route=route;panels.forEach(p=>p.set(false));if(state.focus)setFocus(false);}
    stages.forEach((view,key)=>view.stage.hidden=key!==route);
    document.querySelectorAll('[data-pages]:not(.wg-stage)').forEach(node=>node.dataset.wgRouteHidden=String(!node.dataset.pages.split(/\s+/).includes(route)));
    document.querySelectorAll('.wg-nav a').forEach(link=>{const active=link.dataset.nav===route;link.classList.toggle('active',active);if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
    requestPaint();
  }
  function bootstrap() {
    const header=document.querySelector('[data-wg-header]');if(!header)return;
    header.className='wg-header';
    header.innerHTML=`<a class="wg-brand" href="/measure"><span class="wg-brand-mark" aria-hidden="true"><i></i><i></i><i></i></span><span><small id="productBrandEyebrow" hidden>SCIENTIFIC SIGNAL WORKSTATION</small><strong id="productBrandTitle">WaveGuard</strong></span></a><nav class="wg-nav page-nav" aria-label="主功能"></nav><button id="workstationSettingsButton" class="wg-icon-button workstation-settings-button" type="button" aria-label="界面设置" title="界面设置"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m9.5 3-.6 2.4-2 .9L4.6 5.6 2.8 8.7l1.8 1.7-.2 2.3L2.8 14.5l1.8 3.1 2.4-.7 1.9 1.1.6 2.5h3.6l.6-2.5 2-1.1 2.3.7 1.8-3.1-1.7-1.8.1-2.3 1.6-1.7-1.8-3.1-2.3.7-2-.9L13.1 3Z"/><circle cx="11.3" cy="11.8" r="3.1"/></svg></button>`;
    routes.forEach(([route,label])=>{const a=el('a','',label);a.href='/'+route;a.dataset.nav=route;header.querySelector('nav').append(a);});
    const settings=el('section','wg-appearance workstation-appearance-settings');settings.id='workstationAppearanceSettings';settings.hidden=true;settings.setAttribute('role','dialog');settings.setAttribute('aria-label','界面设置');
    settings.innerHTML=`<div class="wg-inspector-heading"><strong>界面设置</strong><button id="workstationSettingsClose" class="wg-icon-button" type="button" aria-label="关闭界面设置">×</button></div><fieldset><legend>显示模式</legend><div class="wg-theme-choices"><button type="button" data-analysis-theme-choice="light">Light · 浅色</button><button type="button" data-analysis-theme-choice="dark">Dark · 深色</button></div></fieldset><fieldset><legend>界面强调色</legend><div class="wg-accent-choices"><select id="workstationAccentPreset" aria-label="界面强调色方案"><option value="#2f73ff">钴蓝</option><option value="#98704a">铜棕</option><option value="#478463">森林绿</option><option value="#8269b8">紫灰</option><option value="#667587">石板灰</option><option value="custom">自定义</option></select><input id="workstationAccentColor" type="color" aria-label="自定义界面强调色"></div><p>界面与数学图使用强调色，输入通道保持 PicoScope 色序。</p></fieldset><button id="workstationAppearanceReset" type="button">恢复默认强调色</button>`;
    document.body.append(settings);
    const button=document.getElementById('workstationSettingsButton'),control=panelController({panel:settings,trigger:button});
    button.onclick=()=>control.set(!control.isOpen(),{focus:true});document.getElementById('workstationSettingsClose').onclick=()=>control.set(false,{focus:true});
    settings.querySelectorAll('[data-analysis-theme-choice]').forEach(n=>n.onclick=()=>appearance({theme:n.dataset.analysisThemeChoice},true));
    document.getElementById('workstationAccentColor').oninput=e=>appearance({accent:e.target.value},true);
    document.getElementById('workstationAccentPreset').onchange=e=>{if(e.target.value!=='custom')appearance({accent:e.target.value},true);else document.getElementById('workstationAccentColor').focus();};
    document.getElementById('workstationAppearanceReset').onclick=()=>appearance({accent:'#2f73ff'},true);
    document.addEventListener('pointerdown',e=>{if(control.isOpen()&&!settings.contains(e.target)&&!button.contains(e.target))control.set(false);});
    appearance();
  }
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){const open=[...panels].reverse().find(p=>p.isOpen());if(open){event.preventDefault();open.set(false,{focus:true});}document.body.classList.remove('wg-controls-open');}});
  window.addEventListener('resize',requestPaint);
  window.addEventListener('storage',event=>{if(['waveguard-ui-theme','waveguard-workstation-accent'].includes(event.key))appearance({theme:read('waveguard-ui-theme','light'),accent:read('waveguard-workstation-accent','#2f73ff')});});
  window.WaveGuardUI=Object.freeze({el,createStage,bindInspector,panelController,tabs,markSelection,mountShell,activate,appearance,palette,observe,requestPaint,registerPainter:(key,paint)=>painters.set(key,paint),getStage:key=>stages.get(key),channelColors,state});
  bootstrap();
})();
