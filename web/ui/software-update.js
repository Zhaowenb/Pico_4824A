/* Shared settings: check only automatically; install only on explicit click. */
(() => {
  'use strict';
  const panel=document.getElementById('workstationAppearanceSettings');
  if(!panel)return;
  const section=WaveGuardUI.el('section','wg-software-update');
  section.innerHTML=`<h3>软件更新 <span>btf</span></h3><div class="wg-version-row"><span>当前版本</span><code id="wgUpdateCurrent">读取中</code></div><div class="wg-version-row"><span>可用版本</span><code id="wgUpdateLatest">—</code></div><label class="wg-update-auto"><input id="wgUpdateAuto" type="checkbox">自动检查更新</label><p>打开页面及每小时检查，只提示新版本，不自动安装。</p><div class="wg-update-actions"><button id="wgUpdateCheck" type="button">检查更新</button><button id="wgUpdateApply" type="button" disabled>更新并重启</button></div><p id="wgUpdateStatus" role="status" aria-live="polite">读取版本信息…</p><small>更新前须停止仪器任务并确认偏置输出关闭。保存的数据与配置保留。</small>`;
  panel.append(section);
  const get=id=>document.getElementById(id);
  let state={},poll=null,automaticDue=0,reconnecting=false;
  async function request(path='',body){
    const options=body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-WaveGuard-Update':'1'},body:JSON.stringify(body)};
    const response=await fetch('/api/software-update'+path,options),data=await response.json();
    if(!response.ok)throw Error(data.error||'更新服务不可用');
    return data;
  }
  function render(value){
    state=value;
    const busy=['checking','installing','restarting'].includes(value.phase);
    get('wgUpdateCurrent').textContent=(value.running||value.current||'').slice(0,8)||'不可读取';
    get('wgUpdateLatest').textContent=value.latest?value.latest.slice(0,8):'—';
    get('wgUpdateAuto').checked=!!value.auto_check;
    get('wgUpdateCheck').disabled=busy;
    get('wgUpdateApply').disabled=busy||!value.available||!!value.blocked;
    get('wgUpdateAuto').disabled=['installing','restarting'].includes(value.phase);
    get('wgUpdateStatus').textContent=value.blocked||value.message;
    section.dataset.phase=value.phase;
    const trigger=document.getElementById('workstationSettingsButton');
    trigger.classList.toggle('wg-update-available',!!value.available);
    trigger.title=value.available?'设置 · 有新版本':'界面设置';
    if(busy&&!poll)poll=setInterval(refresh,1500);
    if(!busy&&poll){clearInterval(poll);poll=null;}
    if(value.phase==='restarting'&&!reconnecting){reconnecting=true;reconnect(value.current);}
  }
  async function refresh(){try{render(await request());}catch(error){if(!reconnecting)get('wgUpdateStatus').textContent=error.message;}}
  async function reconnect(revision){
    get('wgUpdateStatus').textContent='正在重启服务，请稍候…';
    for(let i=0;i<60;i++){
      await new Promise(r=>setTimeout(r,1000));
      try{
        const current=await request();
        if(current.running===revision&&!['installing','restarting'].includes(current.phase)){location.reload();return;}
        if(current.phase==='error'){
          reconnecting=false;render(current);return;
        }
      }catch(_){}
    }
    get('wgUpdateStatus').textContent='暂未连接到更新后的服务。请检查启动终端或服务日志，再刷新页面。';
    if(poll){clearInterval(poll);poll=null;}
  }
  async function check(){try{render(await request('/check',{}));}catch(error){get('wgUpdateStatus').textContent=error.message;}}
  get('wgUpdateCheck').onclick=check;
  get('wgUpdateApply').onclick=async()=>{
    const revision=state.latest;
    get('wgUpdateApply').disabled=true;
    try{
      render(await request('/apply',{}));
      if(!reconnecting){reconnecting=true;reconnect(revision);}
    }catch(error){get('wgUpdateStatus').textContent=error.message;await refresh();}
  };
  get('wgUpdateAuto').onchange=async e=>{try{render(await request('/preferences',{auto_check:e.target.checked}));automaticDue=0;}catch(error){e.target.checked=!!state.auto_check;get('wgUpdateStatus').textContent=error.message;}};
  async function automatic(){
    if(!state.auto_check||document.hidden||Date.now()<automaticDue||['checking','installing','restarting'].includes(state.phase))return;
    const hardware=window.__biasTaskState;
    if(['running','paused'].includes(hardware))return;
    automaticDue=Date.now()+3600000;
    await check();
  }
  document.addEventListener('visibilitychange',automatic);
  setInterval(automatic,60000);
  refresh().then(automatic);
})();
