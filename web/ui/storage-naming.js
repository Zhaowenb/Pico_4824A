/* Shared storage preferences. These labels affect new saves, never old datasets. */
(() => {
  'use strict';
  const settings=document.getElementById('workstationAppearanceSettings');
  if(!settings)return;
  const section=WaveGuardUI.el('details','wg-storage-settings');
  section.innerHTML='<summary>数据保存 · 命名规则</summary><p>新任务：名称__日期_时分秒__仿真/实测。重复任务自动编号。原始文件标明通道、频率或电流档、重复次数。</p><div data-name-fields></div><button type="button" data-save-names>保存命名设置</button><p role="status" data-name-status></p>';
  settings.append(section);
  const pack=WaveGuardUI.el('details','wg-storage-archive');
  pack.innerHTML='<summary>导出已保存任务 · ZIP</summary><p>选择一个任务文件夹，包含原始数据、配置与全部结果。</p><label>任务文件夹<input type="text" data-pack-path placeholder="data/captures/任务名称"></label><button type="button" data-pack-select>选择任务文件夹</button><button type="button" data-pack-download>下载完整任务</button><p role="status" data-pack-status></p>';
  settings.append(pack);
  pack.querySelector('[data-pack-select]').onclick=()=>{if(window.WaveGuardFiles)WaveGuardFiles.open(pack.querySelector('[data-pack-path]'),'folder','data');else pack.querySelector('[data-pack-status]').textContent='请填写任务文件夹完整路径';};
  pack.querySelector('[data-pack-download]').onclick=async()=>{
    const button=pack.querySelector('[data-pack-download]'),status=pack.querySelector('[data-pack-status]'),directory=pack.querySelector('[data-pack-path]').value.trim();
    if(!directory){status.textContent='请先选择任务文件夹';return;}
    button.disabled=true;status.textContent='正在打包…';
    try{const response=await fetch('/api/storage/archive?directory='+encodeURIComponent(directory));if(!response.ok){const data=await response.json();throw Error(data.error||'打包失败');}
      const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
      const name=response.headers.get('Content-Disposition')?.match(/filename\*=UTF-8''(.+)/)?.[1];link.href=url;link.download=name?decodeURIComponent(name):'WaveGuard数据.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);status.textContent='完整任务已导出';
    }catch(error){status.textContent=error.message;}finally{button.disabled=false;}
  };
  const labels={capture:'实时测量',sweep:'参数扫描',lcr:'LCR测量',big_lcr:'大信号LCR',linearity:'LCR线性度',interference:'干扰实验',analysis:'单数据分析导出',bias:'偏置电流扫描'};
  let names={};
  async function request(options){const response=await fetch('/api/storage/naming',options),data=await response.json();if(!response.ok)throw Error(data.error||'命名设置读取失败');return data.names;}
  function render(values){names=values;section.querySelector('[data-name-fields]').replaceChildren();for(const [key,label] of Object.entries(labels)){const row=WaveGuardUI.el('label','',label),input=document.createElement('input');input.value=values[key];input.maxLength=64;input.dataset.storageName=key;input.setAttribute('aria-label',label+'保存名称');row.append(input);section.querySelector('[data-name-fields]').append(row);}window.dispatchEvent(new CustomEvent('waveguard-storage-names',{detail:names}));}
  section.querySelector('[data-save-names]').onclick=async()=>{const status=section.querySelector('[data-name-status]'),button=section.querySelector('[data-save-names]');button.disabled=true;try{const values=Object.fromEntries([...section.querySelectorAll('input')].map(n=>[n.dataset.storageName,n.value]));render(await request({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({names:values})}));status.textContent='已保存 · 下一次新任务使用新名称，历史数据保持原路径。';}catch(e){status.textContent=e.message;}finally{button.disabled=false;}};
  request().then(render).catch(e=>{section.querySelector('[data-name-status]').textContent=e.message;});
  window.WaveGuardStorage=Object.freeze({getName:key=>names[key]||labels[key]});
})();
