/* Read-only file navigation. Selection feeds existing page actions, never computes data. */
(() => {
  if(!document.querySelector('.wg-workstation'))return;
  const ui=WaveGuardUI,dialog=document.createElement('dialog');dialog.className='wg-file-browser';
  dialog.innerHTML=`<header class="wg-file-heading"><div><small>LOCAL DATA</small><h2>选择数据</h2></div><button type="button" class="wg-icon-button" aria-label="关闭文件选择">×</button></header><div class="wg-file-location"><button type="button" data-up aria-label="返回上级目录">↑</button><input type="text" aria-label="目录路径"><button type="button" data-go>前往</button></div><div class="wg-file-body"><nav aria-label="数据位置"></nav><section><input type="search" placeholder="搜索当前目录" aria-label="搜索当前目录"><div class="wg-file-list" role="listbox" aria-label="文件和文件夹"></div></section></div><footer><span role="status"></span><button type="button" data-confirm>选择</button></footer>`;
  document.body.append(dialog);
  const pathInput=dialog.querySelector('[aria-label="目录路径"]'),search=dialog.querySelector('input[type=search]'),list=dialog.querySelector('.wg-file-list'),status=dialog.querySelector('[role=status]'),confirm=dialog.querySelector('[data-confirm]');
  let target=null,kind='file',directory='',parent=null,selected=null,entries=[],request=0,returnFocus=null;
  function render(){
    list.replaceChildren();
    const visible=entries.filter(entry=>entry.name.toLowerCase().includes(search.value.toLowerCase())&&(kind==='folder'?entry.directory:entry.directory||/\.(npz|csv)$/i.test(entry.name)));
    for(const entry of visible){const row=ui.el('button','wg-file-entry');row.type='button';row.setAttribute('role','option');row.setAttribute('aria-selected',String(selected?.path===entry.path));row.append(ui.el('span','wg-file-icon',entry.directory?'▱':'≋'),ui.el('span','wg-file-name',entry.name),ui.el('small','',entry.directory?'文件夹':`${(entry.size/1024).toFixed(1)} KB`));row.onclick=()=>{selected=entry;list.querySelectorAll('.wg-file-entry').forEach(node=>node.setAttribute('aria-selected',String(node===row)));confirm.disabled=false;confirm.textContent=kind==='file'&&entry.directory?'打开文件夹':kind==='folder'?'选择文件夹':'选择文件';status.textContent=entry.name;};row.ondblclick=()=>{if(entry.directory)void browse(entry.path);else choose();};row.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();if(entry.directory)void browse(entry.path);else choose();}};list.append(row);}
    if(!visible.length)list.append(ui.el('p','wg-file-empty','当前目录没有符合条件的项目'));
    confirm.disabled=kind==='file'&&!selected;confirm.textContent=kind==='folder'?'选择文件夹':'选择文件';status.textContent=selected?.name||(kind==='folder'?'选择当前目录，或双击进入子目录':'单击选择，双击打开文件夹');
  }
  async function browse(path){
    const id=++request;confirm.disabled=true;status.textContent='正在读取目录…';dialog.setAttribute('aria-busy','true');
    try{const response=await fetch('/api/files/list',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})}),data=await response.json();if(id!==request)return;if(!response.ok)throw new Error(data.error||'目录读取失败');directory=data.path;parent=data.parent;entries=data.entries;selected=null;pathInput.value=directory;search.value='';dialog.querySelector('[data-up]').disabled=!parent;const nav=dialog.querySelector('.wg-file-body nav');nav.replaceChildren();for(const root of data.roots){const button=ui.el('button','',root.name);button.type='button';button.onclick=()=>void browse(root.path);nav.append(button);}render();ui.transition(list);}
    catch(error){if(id===request){list.replaceChildren();status.textContent=error.message;confirm.disabled=true;}}
    finally{if(id===request)dialog.removeAttribute('aria-busy');}
  }
  function choose(){if(kind==='file'&&selected?.directory){void browse(selected.path);return;}const value=kind==='folder'?(selected?.directory?selected.path:directory):selected?.path;if(!value)return;target.value=value;target.dispatchEvent(new Event('input',{bubbles:true}));target.dispatchEvent(new Event('change',{bubbles:true}));dialog.close();}
  function open(input,type='file',fallback=''){target=input;kind=type;returnFocus=document.activeElement;dialog.querySelector('h2').textContent=type==='folder'?'选择数据文件夹':'选择数据文件';confirm.textContent=type==='folder'?'选择文件夹':'选择文件';dialog.showModal();ui.transition(dialog,'panel');pathInput.value=input.value.trim()||'data';void browse(pathInput.value);}
  dialog.querySelector('[aria-label="关闭文件选择"]').onclick=()=>dialog.close();dialog.querySelector('[data-up]').onclick=()=>void browse(parent);dialog.querySelector('[data-go]').onclick=()=>void browse(pathInput.value);pathInput.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();void browse(pathInput.value);}};search.oninput=render;confirm.onclick=choose;dialog.addEventListener('close',()=>{request++;returnFocus?.focus();});
  const targets=[['analysisPath','file',''],['archivePath','folder',''],['bigFolderDirectory','folder','data/lcr_big'],['linearityDirectory','folder','data/lcr_linearity'],['smallFolderDirectory','folder','data/lcr']];
  for(const [id,type,fallback] of targets){const input=document.getElementById(id);if(!input)continue;const row=ui.el('div','wg-path-picker'),button=ui.el('button','wg-path-button','选择');button.type='button';button.setAttribute('aria-label',type==='folder'?'选择数据文件夹':'选择数据文件');input.replaceWith(row);row.append(input,button);button.onclick=()=>open(input,type,fallback);}
  window.WaveGuardFiles=Object.freeze({open});
})();
