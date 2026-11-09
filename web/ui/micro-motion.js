/* Presentation only: actual values arrive first; no synthetic acquisition data. */
(() => {
  const reduced=matchMedia('(prefers-reduced-motion:reduce)'),numbers=new WeakMap(),presses=new Map(),runs=new Set();
  function track(run){runs.add(run);run.onfinish=run.oncancel=()=>runs.delete(run);return run;}
  function ticker(node,value){
    if(!node)return;
    const text=String(value),old=numbers.get(node);
    if(old?.text===text)return;
    old?.runs.forEach(run=>run.cancel());
    node.dataset.value=text;node.setAttribute('aria-label',text);node.classList.add('wg-ticker');
    const state={text,runs:[]};numbers.set(node,state);
    if(!old||reduced.matches){node.textContent=text;return;}
    const base=document.createElement('span');base.textContent=text;node.replaceChildren(base);
    const overlay=document.createElement('span');overlay.className='wg-ticker-roll';overlay.setAttribute('aria-hidden','true');
    const prior=old.text.padStart(text.length,' ');
    [...text].forEach((char,i)=>{
      const slot=document.createElement('span');slot.className='wg-ticker-slot';
      const rail=document.createElement('span');rail.className='wg-ticker-rail';
      const before=document.createElement('span'),after=document.createElement('span');before.textContent=prior[i]||' ';after.textContent=char;rail.append(before,after);slot.append(rail);overlay.append(slot);
      if(before.textContent!==char){
        state.runs.push(track(rail.animate([{transform:'translateY(0)'},{transform:'translateY(-50%)'}],{duration:190,easing:'cubic-bezier(.2,.7,.2,1)',fill:'forwards'})));
      }else rail.style.transform='translateY(-50%)';
    });
    base.style.visibility='hidden';node.append(overlay);
    Promise.all(state.runs.map(r=>r.finished.catch(()=>{}))).then(()=>{if(numbers.get(node)===state){node.textContent=text;}});
  }
  function release(key,animate=true){
    const item=presses.get(key);if(!item)return;presses.delete(key);
    item.run?.cancel();item.button.removeAttribute('data-wg-pressed');
    if(animate&&!reduced.matches&&item.button.isConnected&&!item.button.disabled){
      track(item.button.animate([{transform:'translateY(1px) scale(.965)'},{transform:'translateY(-.4px) scale(1.018)',offset:.52},{transform:'translateY(0) scale(1)'}],{duration:290,easing:'cubic-bezier(.2,.65,.25,1)'}));
    }
  }
  function press(button,key){
    if(!button||button.disabled||button.closest('[inert]'))return;
    release(key,false);button.setAttribute('data-wg-pressed','true');
    const run=reduced.matches?null:track(button.animate([{transform:'translateY(0) scale(1)'},{transform:'translateY(1px) scale(.965)'}],{duration:100,easing:'cubic-bezier(.2,.75,.25,1)',fill:'forwards'}));
    presses.set(key,{button,run});
  }
  document.addEventListener('pointerdown',e=>{if(e.button===0)press(e.target.closest('button'),'pointer-'+e.pointerId);},{capture:true});
  document.addEventListener('pointerup',e=>release('pointer-'+e.pointerId),{capture:true});
  document.addEventListener('pointercancel',e=>release('pointer-'+e.pointerId,false),{capture:true});
  document.addEventListener('keydown',e=>{if(!e.repeat&&['Enter',' '].includes(e.key)&&e.target.matches('button'))press(e.target,'key-'+e.key);});
  document.addEventListener('keyup',e=>release('key-'+e.key));
  window.addEventListener('blur',()=>[...presses.keys()].forEach(k=>release(k,false)));
  reduced.addEventListener('change',()=>{if(reduced.matches){runs.forEach(r=>r.cancel());[...presses.keys()].forEach(k=>release(k,false));}});
  window.WaveGuardMotion=Object.freeze({ticker});
})();
