/* Shared settings: check only automatically; install only on explicit click. */
(() => {
  'use strict';
  const panel=document.getElementById('workstationAppearanceSettings');
  if(!panel)return;
  const section=WaveGuardUI.el('section','wg-software-update');
  section.innerHTML=`<h3>软件更新 <span>btf</span></h3><div class="wg-version-row"><span>当前版本</span><code id="wgUpdateCurrent">读取中</code></div><div class="wg-version-row"><span>可用版本</span><code id="wgUpdateLatest">—</code></div><label class="wg-update-auto"><input id="wgUpdateAuto" type="checkbox">自动检查更新</label><p>按所设间隔检查，只提示新版本，不自动安装。</p><div class="wg-update-actions"><button id="wgUpdateCheck" type="button">检查更新</button><button id="wgUpdateApply" type="button" disabled>更新并重启</button></div><p id="wgUpdateStatus" role="status" aria-live="polite">读取版本信息…</p><small>更新前须停止仪器任务并确认偏置输出关闭。保存的数据与配置保留。</small>`;
  panel.append(section);
  getIntervalControls();
  function getIntervalControls(){
    const row=WaveGuardUI.el('div','wg-update-interval');
    row.innerHTML='<label for="wgUpdateInterval">检查间隔</label><div><input id="wgUpdateInterval" type="number" min="1" max="525600" step="any" value="1" aria-label="自动检查间隔"><select id="wgUpdateIntervalUnit" aria-label="检查间隔单位"><option value="60">分钟</option><option value="3600" selected>小时</option><option value="86400">天</option><option value="31536000">年</option></select><button id="wgUpdateIntervalSave" type="button">保存</button></div><small>1 分钟至 1 年（365 天）；打开页面时仍会检查。</small>';
    section.querySelector('.wg-update-auto').after(row);
  }
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
    const interval=value.check_interval_s||3600;
    if(!['wgUpdateInterval','wgUpdateIntervalUnit'].includes(document.activeElement?.id)&&Number(get('wgUpdateInterval').value)*Number(get('wgUpdateIntervalUnit').value)!==interval){
      const unit=[31536000,86400,3600,60].find(unit=>interval>=unit&&interval%unit===0)||60;
      get('wgUpdateIntervalUnit').value=String(unit);
      get('wgUpdateInterval').value=String(interval/unit);
    }
    intervalBounds();
    get('wgUpdateCheck').disabled=busy;
    get('wgUpdateApply').disabled=busy||!value.available||!!value.blocked;
    get('wgUpdateAuto').disabled=['installing','restarting'].includes(value.phase);
    ['wgUpdateInterval','wgUpdateIntervalUnit','wgUpdateIntervalSave'].forEach(id=>get(id).disabled=['installing','restarting'].includes(value.phase));
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
  async function check(){automaticDue=Date.now()+(state.check_interval_s||3600)*1000;try{render(await request('/check',{}));}catch(error){get('wgUpdateStatus').textContent=error.message;}}
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
  function intervalBounds(){const factor=Number(get('wgUpdateIntervalUnit').value);get('wgUpdateInterval').min=String(60/factor);get('wgUpdateInterval').max=String(31536000/factor);}
  get('wgUpdateIntervalUnit').onchange=intervalBounds;
  get('wgUpdateIntervalSave').onclick=async()=>{
    const seconds=Number(get('wgUpdateInterval').value)*Number(get('wgUpdateIntervalUnit').value);
    if(!Number.isInteger(seconds)||seconds<60||seconds>31536000){get('wgUpdateStatus').textContent='间隔须为 1 分钟至 1 年（365 天），且换算后为整数秒。';return;}
    try{
      render(await request('/preferences',{auto_check:!!state.auto_check,check_interval_s:seconds}));
      automaticDue=Date.now()+seconds*1000;
      get('wgUpdateStatus').textContent='检查间隔已保存。';
    }catch(error){get('wgUpdateStatus').textContent=error.message;}
  };
  async function automatic(){
    if(!state.auto_check||document.hidden||Date.now()<automaticDue||['checking','installing','restarting'].includes(state.phase))return;
    const hardware=window.__biasTaskState;
    if(['running','paused'].includes(hardware))return;
    await check();
  }
  document.addEventListener('visibilitychange',automatic);
  setInterval(automatic,60000);
  refresh().then(automatic);
})();
