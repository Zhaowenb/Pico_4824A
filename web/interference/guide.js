/* Shared, human-reviewed operating instructions for the live page and manual. */
window.INTERFERENCE_GUIDE = [
  {
    title:"拉线棒浮空与低阻连接",aim:"确认接棒抑制是否可重复，同时检查回波有没有被一并削弱。",
    before:["固定 TX/PZT 位置、普通 RX 引线、功放增益和偏置磁场；本实验只改变棒与 PZT− 的电气关系。","条件 A：断电后确认棒与 PZT 电极/接收地隔离；PZT− 仍接原 RX 地。","条件 B：断电后把 PZT− 低阻接棒，通电前测量并记录连接电阻，目标为已有装置中约 0～2 Ω。"],
    during:["每个条件采 3～5 次，每次保存原始波形；不要在条件中途移动 PZT。","先看输入是否溢出，再比较发射峰值、尾振、能量、回波峰值与 SNR。"],
    interpret:"浮空/接棒的发射峰值与尾振抑制比为正且稳定，支持接棒有抑制作用。若回波也明显下降，标记边界条件改变，不能把下降全部解释为干扰消除。",
    readout:"K_peak=20log₁₀(A_tx,float/A_tx,grounded)；能量用 10log₁₀(E_float/E_grounded)。",
    caution:"“棒浮空”指相对 PZT/系统地电气隔离；不要拆掉 PZT 原有接收参考连接。"
  },
  {
    title:"棒连接串联电阻扫描",aim:"检查棒是否需要低阻高频参考，并估算抑制减半的 R₅₀。",
    before:["保持 PZT、TX、RX 引线和棒的位置不动。","断电后按 PZT− → Rs → 棒串入电阻；默认 0、2、10、100、1k、10k、100k Ω，可按实际元件修改。","测量每一档实际电阻；0 Ω 使用短接，避免把接触不良当作扫描效应。"],
    during:["从低阻到高阻逐点采集，每档 3～5 次。","每档检查发射和尾振指标；浮空基准采用实验 1 的相同装置状态。"],
    interpret:"若 Rs 增大时串扰逐渐回到浮空基准，支持棒需要低阻参考；只有少数离散点或无浮空基准时，R₅₀ 不足以判别。",
    readout:"绘制 A_tx、A_tail、E_tx、E_tail、A_echo 随 Rs 的变化；0 Ω 单独识别，并留意正电阻跨数量级的趋势。",
    caution:"更换电阻时先停止激励；长引线的寄生电感也可能影响结果。"
  },
  {
    title:"棒对系统地共模电压",aim:"直接检验浮空棒电位是否与 RX 干扰同步。",
    before:["保持棒与 PZT 电极及系统地的原定隔离状态。","用高输入阻抗探头的探针接棒；探头地只接既定公共参考点，另一路同步测 RX。","在页面选择 RX、棒、激励参考三个不同通道，填写棒探头倍率、探头型号和地线位置。"],
    during:["先采一条并检查棒通道未溢出，再完成 3～5 次重复。","重点比较发射起止和尾振；观察同步波形、Pearson 相关、75 kHz 幅值比与相位差。"],
    interpret:"发射窗 |ρ| 较高且相位跨重复采集稳定，支持棒电位与 RX 串扰相关；相关本身不能证明唯一因果路径。",
    readout:"ρ 分别在发射窗、尾振窗计算；H₇₅k=X_RX(75k)/X_rod(75k)。",
    caution:"高阻探头仍会对浮空棒加载，绝不能把 Pico BNC 地夹接到高压输出或未知电位。"
  },
  {
    title:"距离 × 棒状态",aim:"区分可被接棒抑制的部分和随距离快速衰减的残余近场部分。",
    before:["确定 TX 线圈到 PZT 的测距基准，写下装置允许的距离列表；默认 5、10、20、40、80 cm。","每个距离先测棒浮空，再测 PZT− 低阻接棒；两状态除电气连接外位置完全相同。","移动 PZT 时保持贴合压力、导线长度/走向、偏置磁场和激励条件尽可能一致。"],
    during:["按页面生成的“距离 × 状态”条件逐一完成，每格 3～5 次。","记录实测距离和接棒电阻；检查接棒后残余串扰、回波与输入溢出。"],
    interpret:"接棒后残余随距离明显衰减可支持空间近场耦合。拟合 n>1 且 R² 足够高只是经验线索，不能单凭 n 断定磁场。",
    readout:"每个距离计算 ΔA=A_float−A_grounded、K=20log₁₀(A_float/A_grounded)；拟合 A_grounded(r)=a/rⁿ+b。",
    caution:"距离改变也会改变真实导波到达时间；如回波移出既定窗口，应在新的统一窗口下重新分析。"
  },
  {
    title:"普通线、双绞线、屏蔽双绞线",aim:"检验 RX 引线闭合回路是否在拾取磁场。",
    before:["固定 TX/PZT 的位置与棒接地状态。","依次使用现有普通线、PZT+ 与 PZT− 全程紧密双绞、屏蔽双绞线。","屏蔽双绞线应记录屏蔽层接地方式；三种条件尽量保持导线长度和路径一致。"],
    during:["每种线型重复 3～5 次，保存原始波形。","同时查看串扰、回波峰值和 SNR，避免将信号与干扰一起衰减的方案误判为更好。"],
    interpret:"双绞显著降低早期串扰，支持引线回路感应贡献；屏蔽双绞效果需结合屏蔽接法解释。",
    readout:"K_twist=20log₁₀(A_normal/A_twisted)，并比较三种接线的 A_tail、E_tx、E_tail、A_echo、SNR。",
    caution:"不要因更换线缆改变 PZT 安装或 TX–RX 间距。"
  },
  {
    title:"接收回路面积扫描",aim:"验证干扰是否与 RX 导线围成的回路面积相关。",
    before:["固定 PZT/TX 位置、线缆总长度和棒状态。","准备紧贴、分开约 2、5、10 cm 等接线方式；以实际闭合路径估计面积。","在页面扫描值中输入对应的实测/估计面积 cm²，而不是仅输入导线间距。"],
    during:["每种面积采 3～5 次；尽量保持回路法向和 TX 线圈的相对方向。","用同一发射窗比较 A_tx、A_tail 和能量。"],
    interpret:"干扰随面积增大且线性拟合质量较好，支持磁通变化在 RX 回路中感应电压；走线位置同时改变会削弱结论。",
    readout:"拟合 A_tx=k·A_loop+b，报告 k、b、R²。",
    caution:"不能让新接线短接 PZT+ 与 PZT−；避免导线靠近功放高压端。"
  },
  {
    title:"接收回路方向扫描",aim:"验证 RX 引线回路相对于磁场的方向依赖。",
    before:["保持 PZT 位置、回路面积与接线长度固定。","约定 0° 的回路平面方向，依次设 0°、45°、90°，建议补测 135° 以支撑拟合。","记录角度基准与实际角度；仅旋转回路，不移动 PZT。"],
    during:["每个角度重复 3～5 次，并查看早期发射窗和尾振窗。","角度顺序可往返重复，以排除接触变化或缓慢漂移。"],
    interpret:"显著且可重复的角度依赖支持磁场互感；拟合差或角度样本不足时只报告原始趋势。",
    readout:"拟合 A(θ)=|a cos(θ−θ₀)|+b，报告 a、θ₀、b、R²。",
    caution:"旋转时保持回路面积不变，否则面积效应与方向效应混合。"
  },
  {
    title:"Dummy PZT",aim:"移除真实压电机械响应，保留尽可能相同的电气路径。",
    before:["先测量真实 PZT 的电容 C_PZT，并记录。","用接近 C_PZT 的普通电容替代 PZT；保持与真实 PZT 相同的引线、位置、棒状态和输入设置。","不要让 Dummy 改变 TX 驱动或接收通道量程。"],
    during:["真实 PZT 与 Dummy 各采 3～5 次，保存波形。","比较归一化早期波形和晚到回波，而非只看单个峰。"],
    interpret:"Dummy 仍出现相似发射同步波形，说明该部分可由非机械路径产生；Dummy 晚到回波显著弱于真实 PZT，支持机械响应。",
    readout:"比较 A_tx、A_tail、E_tx、E_tail 和回波；K_dummy=A_tx,dummy/A_tx,real。",
    caution:"Dummy 电容近似 PZT 的电容，但不复制其损耗、寄生参数和机械边界。"
  },
  {
    title:"TX 工作、机械耦合关闭",aim:"在尽量维持 TX 电气工作条件的同时，剥离 TX 到棒的机械激励。",
    before:["先测 TX 与棒正常耦合的基准。","停止激励后移开 TX 磁致伸缩耦合或显著减弱机械作用，保留 AWG、功放、匹配网络与接收回路。","如有 TX 电流监测，记录两种条件的电流波形/幅值；无法维持相同电流时在备注中说明。"],
    during:["正常与去机械状态各重复 3～5 次。","分别比较早期发射同步串扰与晚到回波。"],
    interpret:"早期信号保留而晚到回波消失，支持早期主要为电气/EMI、晚到可能为机械导波；TX 电流明显变化时不能单独据此定因。",
    readout:"比较两状态 A_tx、A_tail、A_echo、SNR 与波形到达时间。",
    caution:"移动线圈前先停止输出；不要改变功放负载到不允许的状态。"
  },
  {
    title:"现有共地与 Star/Kelvin 回流",aim:"判别 TX 电流经过 RX 参考路径所致的公共阻抗耦合。",
    before:["画出当前 TX−、RX−、功放、Pico BNC 屏蔽、USB、供电负极的实际连接，拍照或写在备注。","条件 A 保持现有长 BNC/公共地连接。","条件 B 在断电后安排 TX 功率回流与 RX/Pico 参考分别到受控星点；确认不存在意外并联回流，记录星点和线长。"],
    during:["两条件各重复 3～5 次，保持 TX 电流、线圈位置与接收位置不变。","比较 A_tx、A_tail、能量、回波和 SNR。"],
    interpret:"Star/Kelvin 明显改善可支持共享阻抗耦合；若 BNC/USB 仍构成并联回路，负结果不能排除该机制。",
    readout:"K_star=20log₁₀(A_common/A_star)，并记录回波是否下降。",
    caution:"Pico BNC 通道地并不彼此隔离；不要断开保护接地或把功放高压输出直接接 Pico。"
  },
  {
    title:"公共地连接长度扫描",aim:"检查共享地导体阻抗是否随长度改变串扰。",
    before:["明确真正承载公共回流的导体，不要把信号线长度误当公共地长度。","准备 10、50、100、200 cm 或适合装置的连接，记录实际长度。","尽量保持导线走向、TX/RX 相对位置、闭合回路面积与接头不变。"],
    during:["每档重复 3～5 次；每次改线前停止激励。","观察发射峰值和尾振峰值随长度的趋势，注意接头接触电阻。"],
    interpret:"长度增加而串扰可重复上升，支持公共阻抗贡献；若线形和回路面积同时改变，结果也可能包含空间耦合。",
    readout:"绘制 A_tx(L_gnd)、A_tail(L_gnd)，显示线性拟合斜率与 R²。",
    caution:"长导线不等于电气隔离；检查其他 BNC/USB 回流是否绕过所扫描线段。"
  },
  {
    title:"静电屏蔽",aim:"结合双绞结果，比较电场屏蔽与回路面积控制的作用。",
    before:["保持 RX 导线、PZT/TX 位置和棒状态不变。","条件 A 不加屏蔽；条件 B 在 TX 与 PZT/RX 引线之间放置接地开槽导电屏蔽板。","记录屏蔽板材质、开槽方向、位置及接地点。"],
    during:["两状态各重复 3～5 次，比较同一时间窗指标和回波。","确认屏蔽板没有移动 TX 线圈、PZT 或改变机械接触。"],
    interpret:"屏蔽改善显著而双绞改善小，偏向电场耦合；双绞改善显著而屏蔽改善小，偏向磁回路拾取；两者均改善可并存。",
    readout:"K_shield=20log₁₀(A_no_shield/A_shield)，比较发射、尾振、回波和 SNR。",
    caution:"不要用闭合金属环包围磁致伸缩区域，以免涡流改变 TX 磁场与真实激励。"
  }
];

/* Shared appearance for the independent experiment and its manual. */
document.addEventListener('DOMContentLoaded',()=>{
 const byId=id=>document.getElementById(id),panel=byId('workstationAppearanceSettings'),button=byId('workstationSettingsButton');
 if(!panel||!button)return;
 let accent='#2f73ff';
 try {const saved=localStorage.getItem('waveguard-workstation-accent');if(/^#[0-9a-f]{6}$/i.test(saved||''))accent=saved;}catch(_){}
 const color=byId('workstationAccentColor'),presets=byId('workstationAccentPreset');
 const apply=()=>{
  document.body.style.setProperty('--signal',accent);
  const rgb=[1,3,5].map(i=>parseInt(accent.slice(i,i+2),16));
  const high=rgb.map(v=>Math.round(v+(242-v)*.4));
  document.body.style.setProperty('--signal-hi',`rgb(${high.join(',')})`);
  document.body.style.setProperty('--wg-on-accent',rgb.map(v=>{const c=v/255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0)>.179?'#111820':'#fff');
  color.value=accent;presets.value=[...presets.options].some(o=>o.value===accent)?accent:'custom';
  document.querySelectorAll('[data-analysis-theme-choice]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.analysisThemeChoice===document.body.dataset.uiTheme)));
  if(typeof drawWave==='function'){drawWave(selectedPreview||latestWave);drawCompare();drawFft();}
 };
 const setOpen=open=>{panel.hidden=!open;button.setAttribute('aria-expanded',String(open));if(open)panel.querySelector('button').focus();};
 button.addEventListener('click',()=>setOpen(panel.hidden));byId('workstationSettingsClose').addEventListener('click',()=>{setOpen(false);button.focus();});
 document.querySelectorAll('[data-analysis-theme-choice]').forEach(b=>b.addEventListener('click',()=>{
  if(typeof applyUiTheme==='function')applyUiTheme(b.dataset.analysisThemeChoice,true);else applyManualTheme(b.dataset.analysisThemeChoice,true);apply();
 }));
 const save=value=>{if(!/^#[0-9a-f]{6}$/i.test(value))return;accent=value;try{localStorage.setItem('waveguard-workstation-accent',value);}catch(_){}apply();};
 color.addEventListener('input',()=>save(color.value));presets.addEventListener('change',()=>{if(presets.value!=='custom')save(presets.value);else color.focus();});byId('workstationAppearanceReset').addEventListener('click',()=>save('#2f73ff'));
 document.addEventListener('pointerdown',e=>{if(!panel.contains(e.target)&&!button.contains(e.target))setOpen(false);});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!panel.hidden){setOpen(false);button.focus();}});
 apply();
});
