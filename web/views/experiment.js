// Presentation only: experiment context, genuine data, and a persistent action strip.
(() => {
 const main=document.querySelector('section.main'),task=document.querySelector('.experiment-workspace');
 const make=(tag,cls,text='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};

 document.querySelector('main>aside').append($('newSession'));$('newSession').hidden=false;
 const intro=make('div','experiment-intro'),titleBlock=make('div','analysis-app-title'),eyebrow=make('p','eyebrow','EXPERIMENT WORKSPACE'),heading=make('h2','','干扰实验'),detail=make('span','analysis-instrument-id','PicoScope 4824A · 实验会话');titleBlock.append(eyebrow,heading);intro.append(titleBlock,detail);
 const ui=WaveGuardUI.createStage({page:'interference',kind:'scroll',className:'experiment-signal-stage',inspectorTitle:'实验参数'});
 const {stage,bar,views:tabs,content:viewport,inspector,footer}=ui;bar.classList.add('experiment-view-tabs');viewport.classList.add('experiment-viewport');footer.classList.add('experiment-actions');
 tabs.replaceChildren();
 const panels=[task,...main.querySelectorAll('.data-stage,.evidence-stage')];
 const names=['接线与条件','波形 / 频谱','条件比较','综合证据'];
 let currentView=0;
 function redrawResults(){if(latestWave||selectedPreview)drawWave(latestWave||selectedPreview);drawFft();drawCompare();}
 const paint=()=>WaveGuardUI.requestPaint();
 panels.forEach((panel,i)=>{viewport.append(panel);const b=make('button','',names[i]);b.type='button';tabs.append(b);});
 function selectView(index){currentView=index;panels.forEach((n,i)=>n.hidden=i!==index);WaveGuardUI.markSelection(tabs.querySelectorAll('[data-view]'),n=>Number(n.dataset.view)===index);paint();}
 [...tabs.children].forEach((n,i)=>n.dataset.view=String(i));WaveGuardUI.tabs({buttons:[...tabs.children],onSelect:selectView});
 const params=ui.button;params.classList.add('experiment-parameters');
 inspector.append($('setup'),$('windowsBox'));const toggle=open=>ui.setInspector(open);
 footer.append(task.querySelector('.actions'),task.querySelector('#message'));main.prepend(intro,stage);
 WaveGuardUI.mountShell({shell:document.querySelector('body>main'),controls:document.querySelector('main>aside'),main,intro});WaveGuardUI.activate('interference');
 $('createSession').addEventListener('click',()=>{const check=()=>{if(session){toggle(false);selectView(0);}else setTimeout(()=>{if(session){toggle(false);selectView(0);}},800);};setTimeout(check,400);});
 $('startCondition').addEventListener('click',()=>{if(checklistReady()&&session)selectView(1);});
 $('newSession').addEventListener('click',()=>setTimeout(()=>{if(!session)toggle(true);},400));
 WaveGuardUI.registerPainter('interference',redrawResults);WaveGuardUI.observe(viewport);selectView(0);toggle(!session);
})();
