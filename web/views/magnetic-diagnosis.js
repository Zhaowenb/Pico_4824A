/* Magnetic-circuit preview. Deterministic synthetic signals only; no hardware API. */
(() => {
'use strict';
const U=WaveGuardUI,$=id=>document.getElementById(id),route='magnetic-diagnosis';
U.mountShell({shell:$('magShell'),controls:$('magControls'),main:$('magMain'),intro:$('magIntro')});
const ui=U.createStage({page:route,title:'磁路诊断',className:'mag-stage',inspectorTitle:'分析与实验条件'});
$('magMain').insertBefore(ui.stage,$('magMain').querySelector('.event-panel'));
const names=['原始记录','磁通增量','增量响应','速度对照'];
const tabs=names.map(name=>{const b=U.el('button','',name);ui.views.append(b);return b;});
ui.content.innerHTML=`<div class="mag-readouts"><span>实际电流<strong id="magReadCurrent">—</strong></span><span>棒 ΔB<strong id="magReadB">—</strong></span><span>棒 dB/dI<strong id="magReadSlope">—</strong></span><span>记录长度<strong id="magReadDuration">—</strong></span><span class="mag-origin">SIMULATED DATA<br>模型数据 · 非实测</span></div><div class="mag-plots"><section class="mag-plot"><div class="mag-plot-heading"><span id="magTitleTop"></span><small id="magLegendTop"></small></div><canvas class="mag-canvas" id="magCanvasTop" aria-label="主诊断曲线"></canvas></section><section class="mag-plot"><div class="mag-plot-heading"><span id="magTitleBottom"></span><small id="magLegendBottom"></small></div><canvas class="mag-canvas" id="magCanvasBottom" aria-label="关联诊断曲线"></canvas></section></div><div class="mag-evidence" id="magEvidence"></div>`;
ui.footer.innerHTML=`<button id="magGenerate" class="mag-primary">重新生成仿真</button><button id="magResetCursor">重置游标</button><button id="magExport">导出仿真 CSV</button><progress id="magProgress" max="1" value="0" hidden aria-label="仿真生成进度"></progress><span class="mag-footer-context" id="magCursorText">移动 / 点击曲线查看同一时刻</span>`;
ui.inspector.insertAdjacentHTML('beforeend',`<section><h3>分析副本</h3><label>低通截止 Hz<input id="magLowpass" type="number" value="8" min="1" max="20" step="1"></label><p class="mag-inspector-note">预览采用双向一阶低通，原始数据保留。积分前减去前置基线均值；Ramp 起止和低斜率区域不参与增量响应。</p><label>最小有效 dI/dt · A/s<input id="magMinSlope" type="number" value="0.2" min="0.01" max="10" step="0.01"></label><label>显示原始搜索线圈<input id="magShowRaw" type="checkbox" checked></label><label>显示局部增量下降区<input id="magShowBand" type="checkbox" checked></label></section><section><h3>本次磁历史记录</h3><label>起始状态<textarea id="magHistory" rows="3" placeholder="例如：上次最大电流、回零后等待、温度、预磁化步骤"></textarea></label><p class="mag-inspector-note">仿真模型无剩磁；实测时回到 0 A 不代表消磁。正式对照应保持磁历史和热状态一致。</p></section><section><h3>量值边界</h3><p class="mag-inspector-note">ΔB 使用线圈包围截面面积计算，是截面平均磁通密度增量。正负号依绕向定义；本预览使用正向磁通。两个测点不足以唯一定位饱和部位。</p></section><section><h3>下一阶段</h3><p class="mag-inspector-note">换能线性：关联偏置扫描的实际激励电流、PZT 基波和 H2/H1。当前页尚未执行动态激励或实机 Ramp。</p><a href="/bias-scan">打开偏置电流扫描 →</a></section>`);
let view=0,data=null,cursorT=3.5,job=null,snapshot=null;
const n=id=>Number($(id).value);
function configuration(){
 const cfg={end:n('magEnd'),duration:n('magDuration'),rodTurns:n('magRodTurns'),yokeTurns:n('magYokeTurns'),rodDiameter:n('magRodDiameter'),yokeDiameter:n('magYokeDiameter'),cutoff:n('magLowpass'),minSlope:n('magMinSlope'),model:$('magModel').value};
 for(const id of ['magEnd','magRodTurns','magYokeTurns','magRodDiameter','magYokeDiameter','magLowpass','magMinSlope'])if(!$(id).checkValidity())throw Error('请检查参数范围：'+$(id).parentElement.textContent.trim());
 if(Object.values(cfg).some(v=>typeof v==='number'&&!Number.isFinite(v)))throw Error('参数必须为有限数值');
 return cfg;
}
function lowpass(values,dt,cutoff){const a=1-Math.exp(-2*Math.PI*cutoff*dt),out=[...values];for(let i=1;i<out.length;i++)out[i]=out[i-1]+a*(out[i]-out[i-1]);for(let i=out.length-2;i>=0;i--)out[i]=out[i+1]+a*(out[i]-out[i+1]);return out;}
function simulate(cfg){
 const dt=.005,count=Math.round((cfg.duration+2)/dt)+1,areaR=Math.PI*(cfg.rodDiameter/2000)**2,areaY=Math.PI*(cfg.yokeDiameter/2000)**2;
 const t=[],current=[],rodFlux=[],yokeFlux=[];
 for(let i=0;i<count;i++){
  const time=i*dt,p=Math.min(1,Math.max(0,(time-1)/cfg.duration));
  const actual=cfg.end*(cfg.model==='tracking'?(1-Math.exp(-3*p))/(1-Math.exp(-3)):p);
  // Illustrative smooth differential-response decrease; no fitted material B-H claim.
  const integral=cfg.model==='knee'?.025*actual+.095*(actual-Math.log(1+Math.exp(1.2*(actual-7)))/1.2+Math.log(1+Math.exp(-8.4))/1.2):.10*actual;
  const flux=areaR*integral;
  t.push(time);current.push(actual);rodFlux.push(flux);yokeFlux.push(flux*(cfg.model==='knee'?1+.035*Math.max(0,actual-7):1));
 }
 const derivative=arr=>arr.map((_,i)=>(arr[Math.min(count-1,i+1)]-arr[Math.max(0,i-1)])/(dt*(i===0||i===count-1?1:2)));
 const slope=derivative(current);
 const voltage=(flux,turns,phase)=>derivative(flux).map((v,i)=>-turns*v+12e-6+7e-6*Math.sin(2*Math.PI*37*t[i]+phase)+4e-6*Math.sin(2*Math.PI*11*t[i]+phase));
 const rawR=voltage(rodFlux,cfg.rodTurns,0),rawY=voltage(yokeFlux,cfg.yokeTurns,1);
 const offset=values=>values.slice(0,Math.floor(.8/dt)).reduce((a,b)=>a+b,0)/Math.floor(.8/dt);
 const offR=offset(rawR),offY=offset(rawY),r=lowpass(rawR.map(v=>v-offR),dt,cfg.cutoff),y=lowpass(rawY.map(v=>v-offY),dt,cfg.cutoff);
 const integrate=(v,turns)=>{const out=[0];for(let i=1;i<count;i++)out.push(out[i-1]-(v[i]+v[i-1])*dt/(2*turns));return out;};
 const phiR=integrate(r,cfg.rodTurns),phiY=integrate(y,cfg.yokeTurns);
 const valid=t.map((time,i)=>time>1+.08&&time<1+cfg.duration-.08&&slope[i]>cfg.minSlope);
 const response=(v,turns)=>v.map((value,i)=>valid[i]?-value/(turns*slope[i]):null);
 return {cfg,t,current,slope,rawR,rawY,r,y,phiR,phiY,bR:phiR.map(v=>v/areaR),bY:phiY.map(v=>v/areaY),dPhiR:response(r,cfg.rodTurns),dPhiY:response(y,cfg.yokeTurns),dBR:response(r,cfg.rodTurns).map(v=>v===null?null:v/areaR),valid,offR,offY,dt};
}
function regenerate(){
 try{const cfg=configuration();data=simulate(cfg);snapshot=cfg;cursorT=Math.min(cursorT,cfg.duration+1);update();$('magStatus').textContent='仿真已更新 · 无硬件操作';}
 catch(e){$('magStatus').textContent=e.message;}
}
function nearest(){return Math.max(0,Math.min(data.t.length-1,Math.round(cursorT/data.dt)));}
function update(){
 if(!data)return;const i=nearest(),fmt=(x,d=3)=>x===null?'无效区间':x.toFixed(d);
 $('magReadCurrent').textContent=fmt(data.current[i],2)+' A';$('magReadB').textContent=fmt(data.bR[i])+' T';$('magReadSlope').textContent=data.dBR[i]===null?'—':fmt(data.dBR[i])+' T/A';$('magReadDuration').textContent=(data.cfg.duration+2)+' s';
 $('magCursorText').textContent='t '+data.t[i].toFixed(3)+' s  ·  I '+data.current[i].toFixed(2)+' A';
 const explanations={knee:['示例：增量响应下降','模型中约 7 A 后棒的 dΦ/dI 下降，磁通仍继续增长。该组合支持增量磁响应受限，不能单凭此曲线确定饱和部位。'],linear:['示例：磁通继续增长','该模型的主磁通持续增加；若实测 PZT 同时出现平台，应进一步检查贴片响应、激励与接收链路。'],tracking:['示例：电源跟踪不足','搜索线圈电压随实际 dI/dt 下降，除以实际斜率后增量响应保持稳定。直接使用设定 Ramp 斜率会造成误判。']};
 const e=explanations[data.cfg.model];$('magEvidence').replaceChildren();const strong=U.el('strong','',e[0]+' · '),p=U.el('p','',e[1]);p.prepend(strong);$('magEvidence').append(p);
 paint();
}
function series(x,y,color,label,dash=[]){return {x,y,color,label,dash};}
function paint(){
 if(!data)return;const d=data,styles=getComputedStyle(ui.stage),signal=styles.getPropertyValue('--wg-signal-hi').trim(),copper=styles.getPropertyValue('--wg-copper').trim(),B=U.channelColors[1],C=U.channelColors[2];let top,bottom;
 const milli=a=>a.map(v=>v*1000),micro=a=>a.map(v=>v===null?null:v*1e6);
 if(view===0){top={title:'搜索线圈电压',unit:'mV',xLabel:'时间 / s',series:[series(d.t,milli(d.r),B,'B 棒 · 校正'),series(d.t,milli(d.y),C,'C 磁轭 · 校正')]};if($('magShowRaw').checked)top.series.unshift(series(d.t,milli(d.rawR),'#e34b4b60','原始 B',[2,4]),series(d.t,milli(d.rawY),'#39ae5060','原始 C',[2,4]));bottom={title:'实际偏置电流',unit:'A',xLabel:'时间 / s',series:[series(d.t,d.current,U.channelColors[0],'A 实际电流')]};}
 if(view===1){top={title:'磁通增量 ΔΦ',unit:'μWb',xLabel:'实际偏置电流 / A',series:[series(d.current,micro(d.phiR),B,'棒'),series(d.current,micro(d.phiY),C,'磁轭')]};bottom={title:'截面平均 ΔB · 相对起始状态',unit:'T',xLabel:'实际偏置电流 / A',series:[series(d.current,d.bR,B,'棒'),series(d.current,d.bY,C,'磁轭')]};}
 if(view===2){top={title:'增量磁通响应 dΦ/dI',unit:'μWb/A',xLabel:'实际偏置电流 / A',series:[series(d.current,micro(d.dPhiR),B,'棒'),series(d.current,micro(d.dPhiY),C,'磁轭')]};bottom={title:'棒 dB/dI · 起止 / 低斜率区屏蔽',unit:'T/A',xLabel:'实际偏置电流 / A',series:[series(d.current,d.dBR,signal,'棒增量响应')]};}
 if(view===3){const durations=[1,5,10],colors=[signal,copper,'#b9c4d2'];top={title:'速度对照 · 相同起始模型',unit:'μWb/A',xLabel:'实际偏置电流 / A',series:durations.map((v,i)=>{const a=simulate({...snapshot,duration:v});return series(a.current,micro(a.dPhiR),colors[i],v+' s',[[],[6,3],[2,3]][i]);})};bottom={title:'搜索线圈电压 · 速度改变感应幅值',unit:'mV',xLabel:'实际偏置电流 / A',series:durations.map((v,i)=>{const a=simulate({...snapshot,duration:v});return series(a.current,a.r.map((value,j)=>a.valid[j]?value*1000:null),colors[i],v+' s',[[],[6,3],[2,3]][i]);})};}
 for(const [position,plot] of [['Top',top],['Bottom',bottom]]){ $('magTitle'+position).textContent=plot.title;$('magLegend'+position).textContent=plot.series.filter(s=>!s.label.startsWith('原始')).map(s=>s.label).join(' / ');draw($('magCanvas'+position),plot);}
}
function draw(canvas,plot){
 const bounds=canvas.getBoundingClientRect(),w=bounds.width,h=bounds.height;if(w<20||h<30)return;const ratio=devicePixelRatio||1;canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);const c=canvas.getContext('2d');c.scale(ratio,ratio);
 const st=getComputedStyle(ui.stage),muted=st.getPropertyValue('--wg-data-muted'),line=st.getPropertyValue('--wg-stage-line'),signal=st.getPropertyValue('--wg-signal-hi');
 const l=w<550?58:72,r=16,t=20,b=30,pw=w-l-r,ph=h-t-b;if(ph<=0||pw<=0)return;
 let min=0,max=0;plot.series.forEach(s=>s.y.forEach(v=>{if(v!==null&&Number.isFinite(v)){min=Math.min(min,v);max=Math.max(max,v);}}));const range=Math.max(max-min,1e-6);min-=range*.08;max+=range*.12;
 const xmax=view===0?data.cfg.duration+2:data.cfg.end,X=v=>l+v/xmax*pw,Y=v=>t+(max-v)/(max-min)*ph;
 c.font='10px Consolas, Microsoft YaHei';c.fillStyle=muted;c.textAlign='left';c.fillText(plot.unit,4,12);
 for(let k=0;k<=4;k++){const yy=t+k/4*ph,v=max-k/4*(max-min);c.strokeStyle=line;c.lineWidth=.6;c.beginPath();c.moveTo(l,yy);c.lineTo(w-r,yy);c.stroke();c.fillStyle=muted;c.textAlign='right';c.fillText(Math.abs(v)<.001?'0':v.toFixed(Math.abs(v)>10?0:2),l-9,yy+3);}
 c.textAlign='center';for(let k=0;k<=4;k++){const v=xmax*k/4;c.fillStyle=muted;c.fillText(v.toFixed(view===0?1:0),X(v),h-15);}c.textAlign='right';c.fillText(plot.xLabel,w-r,h-1);
 c.save();c.beginPath();c.rect(l,t,pw,ph);c.clip();
 if(view!==0&&data.cfg.model==='knee'&&$('magShowBand').checked&&xmax>7){c.fillStyle='rgba(201,132,53,.055)';c.fillRect(X(7),t,X(xmax)-X(7),ph);c.strokeStyle='rgba(201,132,53,.5)';c.setLineDash([3,4]);c.beginPath();c.moveTo(X(7),t);c.lineTo(X(7),t+ph);c.stroke();c.setLineDash([]);}
 plot.series.forEach(s=>{c.strokeStyle=s.color;c.lineWidth=s.label.startsWith('原始')?1:1.9;c.setLineDash(s.dash);c.beginPath();let start=true;for(let i=0;i<s.x.length;i++){if(s.y[i]===null||!Number.isFinite(s.y[i])){start=true;continue;}if(start)c.moveTo(X(s.x[i]),Y(s.y[i]));else c.lineTo(X(s.x[i]),Y(s.y[i]));start=false;}c.stroke();});c.setLineDash([]);
 const idx=nearest(),cx=X(view===0?data.t[idx]:data.current[idx]);c.strokeStyle=signal;c.globalAlpha=.55;c.setLineDash([3,4]);c.beginPath();c.moveTo(cx,t);c.lineTo(cx,t+ph);c.stroke();c.globalAlpha=1;c.setLineDash([]);c.restore();
 canvas.onpointermove=event=>{if(job)return;const value=Math.max(0,Math.min(1,(event.clientX-bounds.left-l)/pw))*xmax;if(view===0)cursorT=value;else{let best=0;for(let i=1;i<data.current.length;i++)if(Math.abs(data.current[i]-value)<Math.abs(data.current[best]-value))best=i;cursorT=data.t[best];}update();};
}
U.tabs({buttons:tabs,onSelect:index=>{view=index;paint();}});
U.observe(ui.content,paint,route);U.activate(route);
$('magGenerate').onclick=()=>{if(job)return;try{configuration();}catch(e){$('magStatus').textContent=e.message;return;}$('magGenerate').disabled=true;$('magProgress').hidden=false;$('magProgress').value=0;ui.stage.classList.add('mag-running');$('magStatus').textContent='生成仿真数据 · 不发送设备指令';const start=performance.now();const tick=()=>{const p=Math.min(1,(performance.now()-start)/650);$('magProgress').value=p;if(p<1){job=requestAnimationFrame(tick);return;}job=null;regenerate();$('magGenerate').disabled=false;$('magProgress').hidden=true;ui.stage.classList.remove('mag-running');};job=requestAnimationFrame(tick);};
$('magResetCursor').onclick=()=>{cursorT=1+data.cfg.duration/2;update();};
for(const id of ['magEnd','magDuration','magRodTurns','magYokeTurns','magRodDiameter','magYokeDiameter','magLowpass','magMinSlope','magModel'])$(id).onchange=regenerate;
for(const id of ['magShowRaw','magShowBand'])$(id).onchange=paint;
$('magExport').onclick=()=>{
 const d=data,head=['source','time_s','actual_current_A','rod_raw_V','yoke_raw_V','rod_corrected_lowpass_V','yoke_corrected_lowpass_V','rod_delta_flux_Wb','yoke_delta_flux_Wb','rod_delta_B_T','yoke_delta_B_T','actual_dI_dt_A_per_s','rod_dPhi_dI_Wb_per_A','yoke_dPhi_dI_Wb_per_A','valid_response'];
 const lines=['# SIMULATED DATA; not a measurement','# configuration='+JSON.stringify(d.cfg),'# magnetic_history='+JSON.stringify($('magHistory').value),head.join(',')];
 for(let i=0;i<d.t.length;i++)lines.push(['SIMULATED',d.t[i],d.current[i],d.rawR[i],d.rawY[i],d.r[i],d.y[i],d.phiR[i],d.phiY[i],d.bR[i],d.bY[i],d.slope[i],d.dPhiR[i]??'',d.dPhiY[i]??'',d.valid[i]?1:0].join(','));
 const url=URL.createObjectURL(new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download=`磁路诊断_仿真_${d.cfg.end}A_${d.cfg.duration}s_${new Date().toISOString().replace(/[:.]/g,'-')}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);
};
regenerate();
})();
